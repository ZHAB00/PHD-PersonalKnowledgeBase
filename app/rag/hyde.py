"""HyDE（假设文档向量）查询增强

参考（RAG 最佳实践 6.6）：
  HyDE 根据查询生成假设回答/文档，
  再用于检索，以弥合短查询与文档分块之间的语义差距。

触发条件：
  1. 查询长度低于阈值（短/模糊查询）
  2. 首轮检索最高分低于阈值（低置信度）

触发后，LLM 生成一段“假设文档内容”
来回答查询。这段内容被向量化后作为额外检索条件，
并与原始查询通过 RRF 融合。
"""
from __future__ import annotations
import logging
from typing import Optional

from openai import OpenAI
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 触发阈值
HYDE_MIN_QUERY_LENGTH = 10   # chars: trigger for short queries
HYDE_MIN_SCORE = 0.55        # cosine: trigger when best score below this

# HyDE 提示词模板
HYDE_PROMPT = """You are a helpful assistant. Given a user question, write a short passage (2-4 sentences) that a relevant document might contain to answer this question. Write it as if you are the document itself, in the same language as the question. Do NOT answer the question directly - write what the document would say.

Question: {query}

Document passage:"""


async def generate_hypothetical_doc(query: str) -> Optional[str]:
    """使用 LLM 为查询生成假设文档内容。

    生成失败（API 错误、超时等）时返回 None。
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
    """根据查询长度和最佳检索分数判断是否触发 HyDE。

    满足任一条件时返回 True：
      1. 查询过短（少于 min_query_len 个字符）
      2. 最佳检索分数低于阈值
    """
    query_short = len(query.strip()) < min_query_len
    score_low = best_score < min_score
    return query_short or score_low
