"""Reranker: MMR diversity + optional Cross-Encoder precision

References (RAG Best Practices Section 7):
  7.2 Cross-Encoder vs Bi-Encoder: CE for small-batch precision re-ranking
  7.4 MMR (Maximal Marginal Relevance): balance relevance vs diversity
  Pipeline: Recall Top-K -> MMR dedup -> (optional CE) -> Top-N for LLM
"""
from __future__ import annotations
import logging
from typing import Optional
import json
import re

import numpy as np

from app.core.embedding import embed_text, embed_texts
from app.config import settings
from app.models.chat import SourceReference

logger = logging.getLogger(__name__)


def rerank(
    query: str,
    sources: list[SourceReference],
    top_n: int = 5,
    strategy: str = "mmr",
    lambda_mult: float = 0.7,
) -> list[SourceReference]:
    """Re-rank sources using the specified strategy.

    Args:
        query: User query text
        sources: Retrieved sources (10-50 from hybrid search)
        top_n: Number of final sources to return
        strategy: "mmr" | "cross_encoder" | "none"
        lambda_mult: MMR relevance weight (higher = more relevant, lower = more diverse)
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
    """MMR (Maximal Marginal Relevance) diversity re-ranking.

    Prevents returning nearly-identical chunks from the same section.
    Uses existing embedding vectors without extra model calls.
    """
    if len(sources) <= top_n:
        return sources

    # Get embeddings for source contents
    texts = [s.content for s in sources]
    try:
        doc_vecs = np.array(embed_texts(texts))
        query_vec = np.array(embed_text(query))
    except Exception as e:
        logger.warning(f"MMR embedding failed, falling back to score sort: {e}")
        return sorted(sources, key=lambda s: s.score, reverse=True)[:top_n]

    # Normalize
    query_vec = query_vec / (np.linalg.norm(query_vec) + 1e-8)
    doc_vecs = doc_vecs / (np.linalg.norm(doc_vecs, axis=1, keepdims=True) + 1e-8)

    # MMR greedy selection
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
    """Cross-Encoder re-ranking using LLM-as-judge fallback.

    Uses DeepSeek to score each query-document pair on a 1-5 scale.
    Production systems should use dedicated reranker models (BGE, Cohere).
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

        # Batch evaluate with LLM
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
        # Extract JSON array
        json_match = re.search(r"\[.*\]", result_text, re.DOTALL)
        if json_match:
            scores = json.loads(json_match.group())
            # Create index -> score mapping
            score_map = {item["index"]: item["score"] for item in scores}
            # Sort by score descending
            ranked = sorted(
                enumerate(sources[:20]),
                key=lambda x: score_map.get(x[0], 0),
                reverse=True,
            )
            return [sources[i] for i, _ in ranked[:top_n]]
    except Exception as e:
        logger.warning(f"Cross-encoder rerank failed: {e}")

    # Fallback: weighted sort
    return sorted(sources, key=lambda s: s.score, reverse=True)[:top_n]
