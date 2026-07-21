"""Retrieval pipeline: hybrid + query rewriting

References (RAG Best Practices Section 6):
  6.3 Weighted fusion (min-max norm + alpha mixing) as supplement to RRF
  6.5 Query rewriting: coreference resolution + multi-query variants
  6.6 HyDE: optional, gated by config
"""
from __future__ import annotations
import logging
from typing import Optional

from app.core.embedding import embed_text
from app.core import vector_store
from app.rag.hybrid_retriever import get_hybrid_retriever
from app.rag.reranker import rerank
from app.rag.hyde import generate_hypothetical_doc, should_trigger_hyde
from app.rag.graph_rag import retrieve_graph_evidence, format_graph_evidence
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)


def retrieve(
    query: str,
    top_k: int = 5,
    kb_id: str = "default",
    tenant_id: Optional[str] = None,
    doc_id: Optional[str] = None,
    chat_history: Optional[list[dict]] = None,
    enable_rewrite: bool = True,
    rerank_strategy: str = "none",
    rerank_lambda: float = 0.7,
    enable_graphrag: bool = True,
    recall_multiplier: int = 4,
) -> list[SourceReference]:
    """Main retrieval entry point: hybrid + query rewriting + optional reranker.

    Pipeline: Query -> Rewrite -> Recall (top_k * recall_multiplier) -> Rerank -> Top-k

    Args:
        rerank_strategy: "mmr" | "cross_encoder" | "none". MMR is zero-cost diversity
        rerank_lambda: MMR relevance weight (0-1), higher = more relevant
        recall_multiplier: Recall multiplier before rerank (e.g. 4x top_k)"""
    tid = tenant_id or "default"
    retriever = get_hybrid_retriever(kb_id, tid)

    # Determine queries to search (with HyDE)
    queries = [query]
    if enable_rewrite and chat_history:
        rewritten = rewrite_query_with_history(query, chat_history)
        if rewritten and rewritten != query:
            queries.append(rewritten)

    # HyDE: for short or low-confidence queries, generate hypothetical doc
    hyde_text = None
    if enable_rewrite:
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if not loop.is_running():
                import asyncio as _asyncio
                should_hyde = loop.run_until_complete(should_trigger_hyde(query))
                if should_hyde:
                    hyde_text = loop.run_until_complete(generate_hypothetical_doc(query))
                    if hyde_text:
                        queries.append(hyde_text)
                        logger.info(f"HyDE triggered: {query[:50]}")
        except Exception:
            pass

    # Recall with multiplier for reranker to work with
    recall_k = top_k * recall_multiplier

    # Multi-query retrieval + RRF fusion across queries
    if len(queries) > 1:
        results = _multi_query_retrieve(retriever, queries, recall_k, tid)
    elif retriever._bm25 is not None:
        results = retriever.search(query=query, top_k=recall_k, tenant_id=tid, kb_id=kb_id)
    else:
        results = _vector_only_retrieve(query, recall_k, kb_id, tid)

    # Reranker: recall -> rerank -> top_k
    if rerank_strategy != "none" and len(results) > top_k:
        results = rerank(query, results, top_n=top_k, strategy=rerank_strategy, lambda_mult=rerank_lambda)

    # GraphRAG: enrich with knowledge graph evidence
    if enable_graphrag:
        try:
            graph_sources = _graph_retrieve(query, kb_id, tid)
            if graph_sources:
                results = results + graph_sources
        except Exception as e:
            logger.debug("GraphRAG retrieve skipped: %s", e)

    return results[:top_k]


def _multi_query_retrieve(retriever, queries, top_k, kb_id, tenant_id):
    """Multi-query retrieval: RRF fuse across query variants."""
    all_ranked_lists = []
    id_to_source: dict[str, SourceReference] = {}

    for q in queries:
        if retriever._bm25 is not None:
            results = retriever.search(query=q, top_k=top_k * 2, tenant_id=tenant_id)
        else:
            results = _vector_only_retrieve(q, top_k * 2, kb_id, tenant_id)
        all_ranked_lists.append([r.doc_id for r in results])
        for r in results:
            if r.doc_id not in id_to_source:
                id_to_source[r.doc_id] = r

    from app.rag.hybrid_retriever import _rrf_fuse
    fused = _rrf_fuse(all_ranked_lists)
    results = []
    for doc_id, _ in fused[:top_k]:
        if doc_id in id_to_source:
            results.append(id_to_source[doc_id])
    return results


def _vector_only_retrieve(query, top_k, kb_id, tenant_id):
    """Pure vector retrieval fallback."""
    query_vector = embed_text(query)
    raw = vector_store.search(query_vector=query_vector, top_k=top_k, kb_id=kb_id, tenant_id=tenant_id)
    return [
        SourceReference(
            doc_id=r["doc_id"], filename=r["filename"],
            page_number=r["page_number"], chunk_index=r["chunk_index"],
            content=r["content"], score=r["score"],
            is_table=r.get("is_table", False), table_html=r.get("table_html", ""),
        )
        for r in raw
    ]


# ============================================================
# Query Rewriting (Section 6.5)
# ============================================================

def rewrite_query_with_history(query: str, history: list[dict]) -> str:
    """Rewrite query using chat history for coreference resolution.

    Example:
      User: What is ReAct?
      Assistant: ReAct is ...
      User: How is it different from Plan-and-Execute?  <-- "it" needs resolution

    Strategy: rule-based coreference + LLM-based rewrite as fallback.
    """
    if not history:
        return query

    # Rule-based coreference: replace simple pronouns
    resolved = _resolve_coreferences(query, history)
    if resolved != query:
        return resolved

    # If still unchanged, try extracting key terms from last assistant response
    resolved = _expand_with_context(query, history)
    return resolved


def _resolve_coreferences(query: str, history: list[dict]) -> str:
    """Simple rule-based coreference resolution."""
    # Common Chinese coreference patterns
    replacements = {
        "it": None, "its": None, "that": None, "this": None, "those": None, "these": None,
        "he": None, "she": None, "they": None, "them": None,
    }

    # Check if query contains pronouns
    query_lower = query.lower()
    has_pronoun = any(p in query_lower.split() for p in replacements)

    if not has_pronoun:
        return query

    # Get the last user question as referent
    last_user_q = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user_q = msg.get("content", "")
            break

    if not last_user_q:
        return query

    # For English: replace common pronouns
    import re
    for pronoun in ["it", "its", "that", "this", "those", "these"]:
        pattern = re.compile(r'' + pronoun + r'', re.IGNORECASE)
        if pattern.search(query):
            # Extract key noun phrase from last question (simple heuristic)
            key_terms = _extract_key_terms(last_user_q)
            if key_terms:
                query = pattern.sub(key_terms, query)

    return query


def _expand_with_context(query: str, history: list[dict]) -> str:
    """Expand short/ambiguous queries with context from history."""
    # If query is very short (< 5 chars), prepend last topic
    if len(query) < 5:
        for msg in reversed(history):
            if msg.get("role") == "user":
                last_q = msg.get("content", "")
                if len(last_q) > 5:
                    return f"{last_q} {query}"
                break
    return query


def _extract_key_terms(text: str) -> str:
    """Extract key technical terms from text (simple heuristic)."""
    # Match capitalized acronyms (ReAct, RAG, LLM, etc.)
    import re
    acronyms = re.findall(r'[A-Z][A-Za-z-]+', text)
    if acronyms:
        return acronyms[-1]  # Return the last acronym (usually the main topic)

    # Match quoted terms
    quoted = re.findall(r'["""](.+?)["绂刔', text)
    if quoted:
        return quoted[-1]

    # Fallback: last meaningful word
    words = text.split()
    if words:
        return words[-1]
    return ""


def _graph_retrieve(query: str, kb_id: str, tenant_id: str) -> list[SourceReference]:
    """Retrieve graph evidence and convert to SourceReference format."""
    try:
        evidence = retrieve_graph_evidence(query, kb_id)
        if not evidence:
            return []
        formatted = format_graph_evidence(evidence)
        if not formatted:
            return []
        return [
            SourceReference(
                doc_id="graph_rag",
                filename="[知识图谱]",
                content=formatted,
                score=0.85,
                chunk_index=0,
            )
        ]
    except Exception as e:
        logger.warning("GraphRAG retrieval error: %s", e)
        return []


def rebuild_bm25_index(kb_id: str = "default", tenant_id: str = "default"):
    """Rebuild BM25 index from Qdrant chunks."""
    retriever = get_hybrid_retriever(tenant_id)
    try:
        from qdrant_client import QdrantClient, models
        from app.config import settings
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
        all_points = []
        offset = None
        while True:
            result = client.scroll(
                collection_name=f"kb_{kb_id}",
                scroll_filter=models.Filter(must=[
                    models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id))
                ]) if tenant_id else None,
                limit=1000, offset=offset, with_payload=True, with_vectors=False,
            )
            points, next_offset = result[0], result[1]
            all_points.extend(points)
            if next_offset is None:
                break
            offset = next_offset
        if all_points:
            doc_texts = [p.payload.get("content", "") for p in all_points]
            doc_ids = [p.payload.get("doc_id", str(p.id)) for p in all_points]
            retriever.build_bm25_index(doc_texts, doc_ids)
            logger.info(f"BM25 index rebuilt: {len(doc_texts)} chunks from {len(set(doc_ids))} docs")
    except Exception as e:
        logger.warning(f"BM25 index rebuild failed: {e}")
