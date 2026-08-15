"""检索流水线：混合检索 + 查询改写

参考（RAG 最佳实践第 6 节）：
  6.3 加权融合（min-max 归一化 + alpha 混合），作为 RRF 的补充
  6.5 查询改写：指代消解 + 多查询变体
  6.6 HyDE：可选，由配置控制
"""
from __future__ import annotations
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional

from app.core.embedding import embed_text
from app.core.user_settings import embedding_config
from app.core import vector_store
from app.rag.hybrid_retriever import get_hybrid_retriever
from app.rag.reranker import rerank
from app.rag.hyde import generate_hypothetical_doc, should_trigger_hyde
from app.rag.graph_rag import retrieve_graph_evidence, format_graph_evidence
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)

_RETRIEVE_CACHE: "OrderedDict[str, tuple[float, list[SourceReference]]]" = OrderedDict()
_RETRIEVE_CACHE_LOCK = threading.Lock()
_RETRIEVE_CACHE_TTL = 300
_RETRIEVE_CACHE_MAX = 128


def clear_retrieve_cache():
    """清空检索缓存，设置切换嵌入模型后调用。"""
    with _RETRIEVE_CACHE_LOCK:
        _RETRIEVE_CACHE.clear()


def _retrieve_cache_key(
    query: str,
    top_k: int,
    kb_id: str,
    tid: str,
    doc_id: Optional[str],
    chat_history: Optional[list[dict]],
    enable_rewrite: bool,
    rerank_strategy: str,
    rerank_lambda: float,
    enable_graphrag: bool,
    recall_multiplier: int,
) -> str:
    cfg = json.dumps(embedding_config(), sort_keys=True, ensure_ascii=False, default=str)
    hist = ""
    if chat_history:
        hist = hashlib.md5(
            json.dumps(chat_history, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
        ).hexdigest()
    return "|".join([
        query, str(top_k), kb_id, tid, doc_id or "", hist,
        str(enable_rewrite), rerank_strategy, str(rerank_lambda),
        str(enable_graphrag), str(recall_multiplier), cfg,
    ])


def _run_async_in_thread(coro_factory):
    """在短生命周期线程中运行异步协程（对同步调用方安全）。"""
    import asyncio
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro_factory())).result()


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
    """主检索入口：混合检索 + 查询改写 + 可选重排。

    流水线：查询 -> 改写 -> 召回（top_k * recall_multiplier）-> 重排 -> Top-k

    参数：
        rerank_strategy: "mmr" | "cross_encoder" | "none"。MMR 为零成本多样性
        rerank_lambda: MMR 相关性权重（0-1），越高越相关
        recall_multiplier: 重排前的召回倍数（例如 4x top_k）"""
    tid = tenant_id or "default"
    cache_key = _retrieve_cache_key(
        query, top_k, kb_id, tid, doc_id, chat_history,
        enable_rewrite, rerank_strategy, rerank_lambda,
        enable_graphrag, recall_multiplier,
    )
    now = time.monotonic()
    with _RETRIEVE_CACHE_LOCK:
        hit = _RETRIEVE_CACHE.get(cache_key)
        if hit and now - hit[0] <= _RETRIEVE_CACHE_TTL:
            return list(hit[1])

    retriever = get_hybrid_retriever(kb_id, tid)

    # 确定待检索查询（含 HyDE）
    queries = [query]
    if enable_rewrite and chat_history:
        rewritten = rewrite_query_with_history(query, chat_history)
        if rewritten and rewritten != query:
            queries.append(rewritten)

    # HyDE：对短查询或低置信度查询生成假设文档
    hyde_text = None
    if enable_rewrite:
        try:
            should_hyde = _run_async_in_thread(lambda: should_trigger_hyde(query))
            if should_hyde:
                hyde_text = _run_async_in_thread(lambda: generate_hypothetical_doc(query))
                if hyde_text:
                    queries.append(hyde_text)
                    logger.info(f"HyDE triggered: {query[:50]}")
        except Exception:
            pass

    # 按倍数召回，供重排器使用
    recall_k = top_k * recall_multiplier

    # 多查询检索 + 跨查询 RRF 融合
    if len(queries) > 1:
        results = _multi_query_retrieve(retriever, queries, recall_k, kb_id, tid)
    elif retriever._bm25 is not None:
        results = retriever.search(query=query, top_k=recall_k, tenant_id=tid, kb_id=kb_id)
    else:
        results = _vector_only_retrieve(query, recall_k, kb_id, tid)

    # 重排：召回 -> 重排 -> top_k
    if rerank_strategy != "none" and len(results) > top_k:
        results = rerank(query, results, top_n=top_k, strategy=rerank_strategy, lambda_mult=rerank_lambda)
    else:
        results = results[:top_k]

    # GraphRAG：用知识图谱证据增强结果
    graph_sources = []
    if enable_graphrag:
        try:
            graph_sources = _graph_retrieve(query, kb_id, tid)
        except Exception as e:
            logger.debug("GraphRAG retrieve skipped: %s", e)

    results = results[:top_k]
    if graph_sources:
        results = results + graph_sources

    with _RETRIEVE_CACHE_LOCK:
        _RETRIEVE_CACHE[cache_key] = (time.monotonic(), list(results))
        while len(_RETRIEVE_CACHE) > _RETRIEVE_CACHE_MAX:
            _RETRIEVE_CACHE.popitem(last=False)
    return list(results)


def _multi_query_retrieve(retriever, queries, top_k, kb_id, tenant_id):
    """多查询检索：跨查询变体进行 RRF 融合。"""
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
    """纯向量检索回退。"""
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
# 查询改写（第 6.5 节）
# ============================================================

def rewrite_query_with_history(query: str, history: list[dict]) -> str:
    """使用对话历史改写查询，用于指代消解。

    示例：
      用户：什么是 ReAct？
      助手：ReAct 是 ...
      用户：它和 Plan-and-Execute 有什么区别？<-- "它"需要消解

    策略：规则指代消解 + LLM 改写作为回退。
    """
    if not history:
        return query

    # 规则指代消解：替换简单代词
    resolved = _resolve_coreferences(query, history)
    if resolved != query:
        return resolved

    # 若仍无变化，尝试从最后一条助手回答中提取关键词
    resolved = _expand_with_context(query, history)
    return resolved


def _resolve_coreferences(query: str, history: list[dict]) -> str:
    """简单的规则指代消解。"""
    # 常见中文指代模式
    replacements = {
        "it": None, "its": None, "that": None, "this": None, "those": None, "these": None,
        "he": None, "she": None, "they": None, "them": None,
    }

    # 检查查询中是否包含代词
    query_lower = query.lower()
    has_pronoun = any(p in query_lower.split() for p in replacements)

    if not has_pronoun:
        return query

    # 取最后一条用户问题作为指代对象
    last_user_q = ""
    for msg in reversed(history):
        if msg.get("role") == "user":
            last_user_q = msg.get("content", "")
            break

    if not last_user_q:
        return query

    # 英文：替换常见代词
    import re
    for pronoun in ["it", "its", "that", "this", "those", "these"]:
        pattern = re.compile(r'' + pronoun + r'', re.IGNORECASE)
        if pattern.search(query):
            # 从最后问题中提取关键名词短语（简单启发式）
            key_terms = _extract_key_terms(last_user_q)
            if key_terms:
                query = pattern.sub(key_terms, query)

    return query


def _expand_with_context(query: str, history: list[dict]) -> str:
    """用历史上下文扩展短/模糊查询。"""
    # 若查询很短（少于 5 个字符），在前面补上最近主题
    if len(query) < 5:
        for msg in reversed(history):
            if msg.get("role") == "user":
                last_q = msg.get("content", "")
                if len(last_q) > 5:
                    return f"{last_q} {query}"
                break
    return query


def _extract_key_terms(text: str) -> str:
    """从文本中提取关键技术词（简单启发式）。"""
    # 匹配大写缩写（ReAct、RAG、LLM 等）
    import re
    acronyms = re.findall(r'[A-Z][A-Za-z-]+', text)
    if acronyms:
        return acronyms[-1]  # 返回最后一个缩写（通常是主要主题）

    # 匹配引号内的词
    quoted = re.findall(r'["""](.+?)["绂刔', text)
    if quoted:
        return quoted[-1]

    # 回退：最后一个有意义的词
    words = text.split()
    if words:
        return words[-1]
    return ""


def _graph_retrieve(query: str, kb_id: str, tenant_id: str) -> list[SourceReference]:
    """检索图谱证据并转换为带文档来源的 SourceReference 列表。"""
    try:
        evidence = retrieve_graph_evidence(query, kb_id)
        if not evidence:
            return []
        doc_groups: dict[str, list[dict]] = {}
        generic: list[dict] = []
        for e in evidence:
            fn = (e.get("filename") or "").strip()
            if e.get("source") == "graph_chunk" and fn:
                doc_groups.setdefault(fn, []).append(e)
            else:
                generic.append(e)

        sources: list[SourceReference] = []
        for fn, items in doc_groups.items():
            formatted = format_graph_evidence(items)
            if not formatted:
                continue
            first = items[0]
            sources.append(SourceReference(
                doc_id=first.get("doc_id") or f"graph_rag:{fn}",
                filename=fn,
                content=formatted,
                score=0.85,
                chunk_index=0,
            ))
        formatted_generic = format_graph_evidence(generic)
        if formatted_generic:
            sources.append(SourceReference(
                doc_id="graph_rag",
                filename="[知识图谱]",
                content=formatted_generic,
                score=0.85,
                chunk_index=0,
            ))
        return sources
    except Exception as e:
        logger.warning("GraphRAG retrieval error: %s", e)
        return []


def rebuild_bm25_index(kb_id: str = "default", tenant_id: str = "default"):
    """根据 Qdrant 分块重建 BM25 索引。"""
    retriever = get_hybrid_retriever(kb_id, tenant_id)
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
            metas = [
                {
                    "filename": p.payload.get("filename", ""),
                    "page_number": p.payload.get("page_number"),
                    "chunk_index": p.payload.get("chunk_index", 0),
                }
                for p in all_points
            ]
            retriever.build_bm25_index(doc_texts, doc_ids, metas)
            logger.info(f"BM25 index rebuilt: {len(doc_texts)} chunks from {len(set(doc_ids))} docs")
    except Exception as e:
        logger.warning(f"BM25 index rebuild failed: {e}")
