"""HyDE (Hypothetical Document Embeddings) query enhancement

Reference (RAG Best Practices Section 6.6):
  HyDE generates a hypothetical answer/document from the query,
  then uses it for retrieval to bridge the semantic gap between
  short queries and document chunks.

Trigger conditions:
  1. Query length < threshold (short/ambiguous queries)
  2. First-pass retrieval max score < threshold (low confidence)

When triggered, LLM generates a "hypothetical document passage"
that would answer the query. This passage is embedded and used
as an additional retrieval query, fused via RRF with the original.
"""
from __future__ import annotations
import logging
from typing import Optional

from openai import OpenAI
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Trigger thresholds
HYDE_MIN_QUERY_LENGTH = 10   # chars: trigger for short queries
HYDE_MIN_SCORE = 0.55        # cosine: trigger when best score below this

# HyDE prompt template
HYDE_PROMPT = """You are a helpful assistant. Given a user question, write a short passage (2-4 sentences) that a relevant document might contain to answer this question. Write it as if you are the document itself, in the same language as the question. Do NOT answer the question directly - write what the document would say.

Question: {query}

Document passage:"""


async def generate_hypothetical_doc(query: str) -> Optional[str]:
    """Use LLM to generate a hypothetical document passage for the query.

    Returns None if generation fails (API error, timeout, etc.)
    """
    try:
        client = OpenAI(
            base_url=settings.deepseek_base_url,
            api_key=settings.deepseek_api_key,
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
        prompt = HYDE_PROMPT.format(query=query)
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        content = resp.choices[0].message.content
        if content and len(content.strip()) > 5:
            logger.info(f"HyDE generated: {content[:80]}...")
            return content.strip()
    except Exception as e:
        logger.warning(f"HyDE generation failed: {e}")
    return None


async def should_trigger_hyde(
    query: str,
    best_score: float = 1.0,
    min_query_len: int = HYDE_MIN_QUERY_LENGTH,
    min_score: float = HYDE_MIN_SCORE,
) -> bool:
    """Determine if HyDE should be triggered based on query length and best retrieval score.

    Returns True when EITHER condition is met:
      1. Query is short (< min_query_len chars)
      2. Best retrieval score is below threshold
    """
    query_short = len(query.strip()) < min_query_len
    score_low = best_score < min_score
    return query_short or score_low
