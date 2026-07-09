"""Hybrid Retrieval: BM25 keyword + vector semantic + RRF fusion

References:
  - RAG Best Practices Section 6.2: BM25 keyword retrieval (exact match for proper nouns)
  - RAG Best Practices Section 6.3: Hybrid search (semantic + literal)
  - RAG Best Practices Section 6.4: RRF multi-path rank fusion (scale-invariant)
"""
from __future__ import annotations
import logging
from typing import Optional

from rank_bm25 import BM25Okapi
import jieba

from app.core.embedding import embed_text
from app.core import vector_store
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)

# KB-aware retriever instances: {kb_id: HybridRetriever}
_retrievers: dict[str, HybridRetriever] = {}


def _tokenize(text: str) -> list[str]:
    tokens = jieba.lcut(text)
    return [t.strip().lower() for t in tokens if t.strip()]


def _rrf_fuse(ranked_lists: list[list[str]], k: int = 60) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranks in ranked_lists:
        for rank, doc_id in enumerate(ranks, start=1):
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)


class HybridRetriever:
    def __init__(self, kb_id: str = "default", tenant_id: str = "default"):
        self.kb_id = kb_id
        self.tenant_id = tenant_id
        self._bm25: Optional[BM25Okapi] = None
        self._bm25_doc_ids: list[str] = []
        self._bm25_texts: list[str] = []

    def build_bm25_index(self, doc_texts: list[str], doc_ids: list[str]):
        tokenized = [_tokenize(t) for t in doc_texts]
        if not tokenized:
            return
        self._bm25 = BM25Okapi(tokenized)
        self._bm25_doc_ids = doc_ids
        self._bm25_texts = doc_texts
        logger.info(f"BM25 index built for kb:{self.kb_id}: {len(doc_texts)} chunks")

    def search(
        self,
        query: str,
        top_k: int = 10,
        tenant_id: Optional[str] = None,
        kb_id: Optional[str] = None,
        rrf_k: int = 60,
    ) -> list[SourceReference]:
        tid = tenant_id or self.tenant_id
        kid = kb_id or self.kb_id

        # Vector search via vector_store (collection-aware)
        vector_results = self._vector_search(query, top_k, kid, tid)

        # BM25 keyword search
        bm25_results = self._bm25_search(query, top_k) if self._bm25 else []

        if not bm25_results:
            return vector_results

        # RRF fusion
        vec_ids = [r.doc_id for r in vector_results]
        bm25_ids = [r.doc_id for r in bm25_results]
        fused = _rrf_fuse([vec_ids, bm25_ids], k=rrf_k)

        id_to_source: dict[str, SourceReference] = {}
        for r in vector_results:
            id_to_source[r.doc_id] = r
        for r in bm25_results:
            if r.doc_id not in id_to_source:
                id_to_source[r.doc_id] = r

        results = []
        for doc_id, _rrf_score in fused[:top_k]:
            if doc_id in id_to_source:
                results.append(id_to_source[doc_id])
        return results

    def _vector_search(self, query: str, top_k: int, kb_id: str, tenant_id: str) -> list[SourceReference]:
        query_vector = embed_text(query)
        raw = vector_store.search(query_vector=query_vector, top_k=top_k, kb_id=kb_id, tenant_id=tenant_id)
        return [
            SourceReference(
                doc_id=r["doc_id"],
                filename=r.get("filename", ""),
                page_number=r.get("page_number"),
                chunk_index=r.get("chunk_index", 0),
                content=r.get("content", ""),
                score=r["score"],
                is_table=r.get("is_table", False),
                table_html=r.get("table_html", ""),
            )
            for r in raw
        ]

    def _bm25_search(self, query: str, top_k: int) -> list[SourceReference]:
        if not self._bm25:
            return []
        tokenized = _tokenize(query)
        scores = self._bm25.get_scores(tokenized)
        indexed = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)[:top_k]
        results = []
        for idx, score in indexed:
            if score > 0:
                doc_id = self._bm25_doc_ids[idx] if idx < len(self._bm25_doc_ids) else str(idx)
                content = self._bm25_texts[idx][:200] if idx < len(self._bm25_texts) else ""
                results.append(SourceReference(
                    doc_id=doc_id,
                    filename=doc_id[:30],
                    chunk_index=idx,
                    content=content,
                    score=float(score),
                ))
        return results


def get_hybrid_retriever(kb_id: str = "default", tenant_id: str = "default") -> HybridRetriever:
    key = f"{kb_id}:{tenant_id}"
    if key not in _retrievers:
        _retrievers[key] = HybridRetriever(kb_id=kb_id, tenant_id=tenant_id)
    return _retrievers[key]
