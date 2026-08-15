"""重排器：MMR 多样性 + 可选 Cross-Encoder 精度

参考（RAG 最佳实践第 7 节）：
  7.2 Cross-Encoder vs Bi-Encoder：CE 适合小批量高精度重排
  7.4 MMR（最大边际相关）：平衡相关性与多样性
  流水线：召回 Top-K -> MMR 去重 ->（可选 CE）-> 交给 LLM 的 Top-N
"""
from __future__ import annotations
import hashlib
import logging
import threading
import time
from collections import OrderedDict
from typing import Optional
import json
import re

import numpy as np

from app.core.embedding import embed_text, embed_texts
from app.config import settings
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)

# MMR 文档/查询向量缓存：同一分块反复被不同问题召回时，不重复调用嵌入模型
_MMR_DOC_EMBED_CACHE: "OrderedDict[str, list[float]]" = OrderedDict()
_MMR_QUERY_EMBED_CACHE: "OrderedDict[str, tuple[float, list[float]]]" = OrderedDict()
_MMR_CACHE_LOCK = threading.Lock()
_MMR_EMBED_MAX = 512
_MMR_QUERY_TTL = 600


def clear_rerank_cache():
    """清空 MMR 向量缓存，切换嵌入模型后调用。"""
    with _MMR_CACHE_LOCK:
        _MMR_DOC_EMBED_CACHE.clear()
        _MMR_QUERY_EMBED_CACHE.clear()


def _mmr_doc_vectors(texts: list[str]):
    """批量嵌入文档内容，已缓存的片段直接复用。"""
    keys = [hashlib.sha256(t.encode("utf-8")).hexdigest() for t in texts]
    found: dict[int, list[float]] = {}
    missing_idx: list[int] = []
    missing_keys: list[str] = []
    with _MMR_CACHE_LOCK:
        for i, key in enumerate(keys):
            if key in _MMR_DOC_EMBED_CACHE:
                found[i] = _MMR_DOC_EMBED_CACHE[key]
            else:
                missing_idx.append(i)
                missing_keys.append(key)
    if missing_keys:
        missing_texts = [texts[i] for i in missing_idx]
        new_vecs = embed_texts(missing_texts)
        with _MMR_CACHE_LOCK:
            for key, vec in zip(missing_keys, new_vecs):
                _MMR_DOC_EMBED_CACHE[key] = list(vec)
            while len(_MMR_DOC_EMBED_CACHE) > _MMR_EMBED_MAX:
                _MMR_DOC_EMBED_CACHE.popitem(last=False)
        for i, vec in zip(missing_idx, new_vecs):
            found[i] = list(vec)
    return np.array([found[i] for i in range(len(texts))])


def _mmr_query_embed(query: str):
    """查询向量短缓存，避免同一查询在短时间内重复嵌入。"""
    now = time.monotonic()
    with _MMR_CACHE_LOCK:
        hit = _MMR_QUERY_EMBED_CACHE.get(query)
        if hit and now - hit[0] <= _MMR_QUERY_TTL:
            return np.array(hit[1])
    vec = embed_text(query)
    with _MMR_CACHE_LOCK:
        _MMR_QUERY_EMBED_CACHE[query] = (time.monotonic(), list(vec))
        while len(_MMR_QUERY_EMBED_CACHE) > 128:
            _MMR_QUERY_EMBED_CACHE.popitem(last=False)
    return np.array(vec)


def rerank(
    query: str,
    sources: list[SourceReference],
    top_n: int = 5,
    strategy: str = "mmr",
    lambda_mult: float = 0.7,
) -> list[SourceReference]:
    """使用指定策略对来源进行重排。

    参数：
        query: 用户查询文本
        sources: 检索到的来源（混合检索返回 10-50 条）
        top_n: 最终返回的来源数量
        strategy: "mmr" | "cross_encoder" | "none"
        lambda_mult: MMR 相关性权重（越高越相关，越低越多样）
    """
    if len(sources) <= top_n:
        return sources

    if strategy == "mmr":
        return _mmr_rerank(query, sources, top_n, lambda_mult)
    elif strategy == "cross_encoder":
        return _cross_encoder_rerank(query, sources, top_n)
    else:
        return sources[:top_n]


def _mmr_rerank(
    query: str,
    sources: list[SourceReference],
    top_n: int,
    lambda_mult: float = 0.7,
) -> list[SourceReference]:
    """MMR（最大边际相关）多样性重排。

    避免返回同一章节内几乎相同的分块。
    复用已有向量，无需额外模型调用。
    """
    if len(sources) <= top_n:
        return sources

    # 获取来源内容的向量
    texts = [s.content for s in sources]
    try:
        doc_vecs = _mmr_doc_vectors(texts)
        query_vec = _mmr_query_embed(query)
    except Exception as e:
        logger.warning(f"MMR embedding failed, falling back to score sort: {e}")
        return sorted(sources, key=lambda s: s.score, reverse=True)[:top_n]

    # 归一化
    query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    doc_vecs = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-8)

    # MMR 贪心选择
    sim_to_q = doc_vecs @ query_vec
    selected: list[int] = []
    candidates = set(range(len(sources)))

    while len(selected) < top_n and candidates:
        best_idx = None
        best_score = float("-inf")
        for i in candidates:
            redundant = 0.0
            if selected:
                redundant = max(float(doc_vecs[i] @ doc_vecs[j]) for j in selected)
            score = lambda_mult * sim_to_q[i] - (1 - lambda_mult) * redundant
            if score > best_score:
                best_score = score
                best_idx = i
        if best_idx is not None:
            selected.append(best_idx)
            candidates.remove(best_idx)

    return [sources[i] for i in selected]


def _cross_encoder_rerank(
    query: str,
    sources: list[SourceReference],
    top_n: int,
) -> list[SourceReference]:
    """使用 LLM 作为裁判的 Cross-Encoder 重排回退方案。

    使用 DeepSeek 对每对查询-文档按 1-5 分评分。
    生产系统应使用专用重排模型（BGE、Cohere）。
    """
    if len(sources) <= top_n:
        return sources

    try:
        from openai import OpenAI

        client = OpenAI(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=120,
        )

        # 使用 LLM 批量评估
        docs_text = "\n---\n".join(
            f"[{i}] {s.content[:300]}" for i, s in enumerate(sources[:20])
        )
        prompt = f"""Score each document on relevance to the query (1-5 scale).
Query: {query}

Documents:
{docs_text}

Return JSON: [{{"index": 0, "score": 3, "reason": "..."}}, ...]"""

        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=1000,
        )
        result_text = resp.choices[0].message.content or "[]"
        # 提取 JSON 数组
        json_match = re.search(r"\[.*\]", result_text, re.DOTALL)
        if json_match:
            scores = json.loads(json_match.group())
            # 创建索引 -> 分数映射
            score_map = {item["index"]: item["score"] for item in scores}
            # 按分数降序排序
            ranked = sorted(
                enumerate(sources[:20]),
                key=lambda x: score_map.get(x[0], 0),
                reverse=True,
            )
            return [sources[i] for i, _ in ranked[:top_n]]
    except Exception as e:
        logger.warning(f"Cross-encoder rerank failed: {e}")

    # 回退：按分数排序
    return sorted(sources, key=lambda s: s.score, reverse=True)[:top_n]
