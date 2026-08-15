"""记忆系统 v2：按智能体记忆最佳实践的四层架构

层级：
  1. 短期记忆：基于 token 预算的滑动窗口（第 2 节）
  2. 长期记忆：基于向量的持久记忆 + 重要性评分（第 3 节）
  3. 总结：通过 LLM 定期压缩（第 4 节）
  4. 重要性评分：LLM 对记忆评分 1-10（第 3.2.3 节）
  5. 用户隔离：所有记忆操作按 user_id 过滤（第 8.2.1 节）
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from datetime import datetime, UTC
from typing import Optional

from openai import OpenAI
import httpx

from app.core import cache
from app.config import settings
from app.prompts import SUMMARY_PROMPT, IMPORTANCE_PROMPT, EXTRACT_FACTS_PROMPT, MEMORY_PREFIX

logger = logging.getLogger(__name__)

def validate_memory_content(content: str) -> str:
    """若内容不应存储则返回原因字符串，否则返回空。"""
    import re
    text = (content or "").strip()
    if not text:
        return "empty"
    low = text.lower()
    poison = ["\u5ffd\u7565\u7cfb\u7edf", "\u65e0\u89c6\u7cfb\u7edf", "\u5ffd\u7565\u89c4\u5219", "\u65e0\u89c6\u89c4\u5219", "system prompt", "jailbreak", "\u8d8a\u72f1", "\u4ece\u73b0\u5728\u5f00\u59cb", "\u4f60\u5fc5\u987b"]
    if any(k in low for k in poison):
        return "prompt-injection"
    if re.search(r"(password|passwd|secret|api[_-]?key|token|\u8eab\u4efd\u8bc1|\u94f6\u884c\u5361|\u5bc6\u7801|\u53e3\u4ee4)", low):
        return "sensitive"
    return ""

# 预算配置
TOKEN_BUDGET_CHARS = 6000
SYSTEM_BUDGET_CHARS = 800
SUMMARY_TRIGGER = 6          # summarize every N turns
MEMORY_TTL = 86400 * 7       # 7 days

# Qdrant 中的记忆集合
MEMORY_COLLECTION = "kb_memory"

# 记忆检索短缓存：相同问题在短时间内不重复向量化
_MEMORY_RETRIEVE_CACHE: "OrderedDict[tuple, tuple[float, list[dict]]]" = OrderedDict()
_MEMORY_RETRIEVE_CACHE_LOCK = threading.Lock()
_MEMORY_RETRIEVE_TTL = 300
_MEMORY_RETRIEVE_MAX = 64


def clear_memory_retrieve_cache():
    """清空记忆检索缓存，切换嵌入模型后调用。"""
    with _MEMORY_RETRIEVE_CACHE_LOCK:
        _MEMORY_RETRIEVE_CACHE.clear()


# ============================================================
# 第一层：短期记忆（基于 token 预算的滑动窗口）
# ============================================================

def _estimate_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in messages)


def apply_token_budget(history: list[dict], max_chars: int = TOKEN_BUDGET_CHARS) -> list[dict]:
    """带字符预算的滑动窗口。保留系统消息，从头部裁剪用户/助手消息。"""
    system_msgs = [m for m in history if m.get("role") == "system"]
    other_msgs = [m for m in history if m.get("role") != "system"]

    system_chars = _estimate_chars(system_msgs)
    available = max_chars - system_chars - SYSTEM_BUDGET_CHARS
    if available <= 0:
        return system_msgs

    kept = []
    total = 0
    for msg in reversed(other_msgs):
        msg_chars = len(msg.get("content", ""))
        if total + msg_chars > available:
            break
        kept.insert(0, msg)
        total += msg_chars

    return system_msgs + kept


# ============================================================
# 第二层：长期记忆（向量存储 + 用户隔离）
# ============================================================

def _memory_collection_name() -> str:
    return MEMORY_COLLECTION


_memory_collection_ready = False

def _ensure_memory_collection():
    global _memory_collection_ready
    if _memory_collection_ready:
        return
    from app.core.vector_store import _get_client
    from app.core.embedding import embedding_dimension
    from qdrant_client.http import models
    client = _get_client()
    col_name = _memory_collection_name()
    existing = [c.name for c in client.get_collections().collections]
    if col_name not in existing:
        client.create_collection(
            collection_name=col_name,
            vectors_config=models.VectorParams(size=embedding_dimension(), distance=models.Distance.COSINE),
        )
        client.create_payload_index(collection_name=col_name, field_name="user_id", field_schema=models.PayloadSchemaType.KEYWORD)
        client.create_payload_index(collection_name=col_name, field_name="memory_type", field_schema=models.PayloadSchemaType.KEYWORD)
        logger.info(f"Created memory collection: {col_name}")
    else:
        try:
            info = client.get_collection(col_name)
            vectors = info.config.params.vectors
            current_dim = None
            if isinstance(vectors, dict):
                for v in vectors.values():
                    if hasattr(v, "size"):
                        current_dim = v.size
                        break
            else:
                current_dim = getattr(vectors, "size", None)
            expected_dim = embedding_dimension()
            if current_dim and current_dim != expected_dim:
                if info.points_count == 0:
                    client.delete_collection(col_name)
                    client.create_collection(
                        collection_name=col_name,
                        vectors_config=models.VectorParams(size=expected_dim, distance=models.Distance.COSINE),
                    )
                    logger.info(f"Recreated memory collection: {col_name}")
                else:
                    logger.warning(
                        "Memory collection dimension mismatch (%s != %s); existing memories may be incompatible",
                        current_dim, expected_dim,
                    )
        except Exception as e:
            logger.warning("Failed to validate memory collection: %s", e)
    _memory_collection_ready = True


async def store_long_term_memory(
    user_id: str,
    content: str,
    memory_type: str = "episodic",
    importance: int = 5,
    session_id: str = "",
):
    """Store a memory fragment and deduplicate similar ones."""
    def _store_sync():
        from app.core.embedding import embed_text
        from app.core.vector_store import _get_client
        from qdrant_client.http import models
        from uuid import uuid4
        from datetime import datetime, UTC

        try:
            reason = validate_memory_content(content)
            if reason:
                logger.info(f"Memory skipped ({reason}): {str(content)[:50]}")
                return
            _ensure_memory_collection()
            content_trimmed = content[:2000]
            vector = embed_text(content_trimmed)
            client = _get_client()

            existing = client.query_points(
                collection_name=_memory_collection_name(),
                query=vector,
                limit=1,
                query_filter=models.Filter(must=[
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id))
                ]),
                with_payload=True,
                score_threshold=0.92,
            )
            if existing.points:
                pt = existing.points[0]
                old_imp = pt.payload.get("importance", 5)
                new_imp = max(old_imp, importance)
                client.set_payload(
                    collection_name=_memory_collection_name(),
                    points=[pt.id],
                    payload={
                        "content": content_trimmed,
                        "importance": new_imp,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )
                logger.debug(f"Memory updated (dedup): {content[:60]}... importance {old_imp}->{new_imp}")
                return

            client.upsert(
                collection_name=_memory_collection_name(),
                points=[models.PointStruct(
                    id=str(uuid4()),
                    vector=vector,
                    payload={
                        "user_id": user_id,
                        "session_id": session_id,
                        "memory_type": memory_type,
                        "content": content_trimmed,
                        "importance": importance,
                        "timestamp": datetime.now(UTC).isoformat(),
                    },
                )],
            )
            logger.debug(f"Memory stored [{memory_type}][importance={importance}]: {content[:60]}...")
        except Exception as e:
            logger.warning(f"Failed to store memory: {e}")

    return await asyncio.to_thread(_store_sync)


async def retrieve_long_term_memory(
    query: str,
    user_id: str = "default",
    top_k: int = 5,
) -> list[dict]:
    """Retrieve memories ranked by semantic and recency importance."""
    def _retrieve_sync():
        from app.core.embedding import embed_text
        from app.core.vector_store import _get_client
        from qdrant_client.http import models
        from datetime import datetime, UTC

        try:
            _ensure_memory_collection()
            vector = embed_text(query)
            client = _get_client()

            result = client.query_points(
                collection_name=_memory_collection_name(),
                query=vector,
                limit=top_k * 3,
                query_filter=models.Filter(must=[
                    models.FieldCondition(key="user_id", match=models.MatchValue(value=user_id)),
                ]),
                with_payload=True,
                score_threshold=0.4,
            )

            now = datetime.now(UTC)
            memories = []
            for r in result.points:
                ts_str = r.payload.get("timestamp", "")
                imp = r.payload.get("importance", 5)
                sim_score = r.score

                time_score = 1.0
                if ts_str:
                    try:
                        ts = datetime.fromisoformat(ts_str)
                        age_days = (now - ts).total_seconds() / 86400
                        time_score = 0.5 ** (age_days / 7)
                    except Exception:
                        pass

                imp_norm = imp / 10.0
                composite = 0.5 * sim_score + 0.25 * time_score + 0.25 * imp_norm

                memories.append({
                    "content": r.payload.get("content", ""),
                    "memory_type": r.payload.get("memory_type", ""),
                    "importance": imp,
                    "timestamp": ts_str,
                    "score": round(composite, 4),
                })

            memories.sort(key=lambda m: m["score"], reverse=True)
            return memories[:top_k]
        except Exception as e:
            logger.warning(f"Memory retrieval failed: {e}")
            return []

    cache_key = (query, user_id, top_k)
    now = time.monotonic()
    with _MEMORY_RETRIEVE_CACHE_LOCK:
        hit = _MEMORY_RETRIEVE_CACHE.get(cache_key)
        if hit and now - hit[0] <= _MEMORY_RETRIEVE_TTL:
            return [dict(m) for m in hit[1]]

    memories = await asyncio.to_thread(_retrieve_sync)

    with _MEMORY_RETRIEVE_CACHE_LOCK:
        _MEMORY_RETRIEVE_CACHE[cache_key] = (time.monotonic(), [dict(m) for m in memories])
        while len(_MEMORY_RETRIEVE_CACHE) > _MEMORY_RETRIEVE_MAX:
            _MEMORY_RETRIEVE_CACHE.popitem(last=False)
    return memories


# ============================================================
# 第三层：会话总结（LLM 压缩）
# ============================================================



async def summarize_conversation(history: list[dict], user_id: str = "default") -> str:
    """使用 LLM 将对话历史压缩为摘要。"""
    client = OpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        timeout=httpx.Timeout(30.0, connect=5.0),
    )

    history_text = "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')[:300]}"
        for m in history[-20:]
    )

    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": SUMMARY_PROMPT.format(history=history_text)}],
            temperature=0.2,
            max_tokens=400,
        )
        summary = resp.choices[0].message.content or ""
        logger.info(f"Generated summary ({len(summary)} chars)")
        return summary.strip()
    except Exception as e:
        logger.warning(f"Summarization failed: {e}")
        return ""


async def should_summarize(turn_count: int) -> bool:
    return turn_count >= SUMMARY_TRIGGER and turn_count % SUMMARY_TRIGGER == 0


# ============================================================
# 第四层：重要性评分（LLM 评估记忆）
# ============================================================

# （提示词已移至 app.prompts）


async def score_importance(content: str) -> int:
    """LLM 对对话片段的重要性评分（1-10）。"""
    client = OpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        timeout=httpx.Timeout(15.0, connect=5.0),
    )
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": IMPORTANCE_PROMPT.format(content=content[:1500])}],
            temperature=0,
            max_tokens=100,
        )
        text = resp.choices[0].message.content or ""
        import re
        match = re.search(r"\{[^{}]*\}", text)
        if match:
            data = json.loads(match.group())
            return max(1, min(10, int(data.get("importance", 5))))
    except Exception:
        pass
    return 5  # default


# ============================================================
# 第五层：从对话中抽取关键事实
# ============================================================

# （提示词已移至 app.prompts）


async def extract_key_facts(history: list[dict]) -> list[dict]:
    """从对话中抽取用户关键事实/偏好，用于长期存储。"""
    client = OpenAI(
        base_url=settings.deepseek_base_url,
        api_key=settings.deepseek_api_key,
        timeout=httpx.Timeout(20.0, connect=5.0),
    )
    history_text = "\n".join(
        f"{'用户' if m.get('role') == 'user' else '助手'}: {m.get('content', '')[:200]}"
        for m in history[-12:]
    )
    try:
        resp = client.chat.completions.create(
            model=settings.deepseek_model,
            messages=[{"role": "user", "content": EXTRACT_FACTS_PROMPT.format(history=history_text)}],
            temperature=0.1,
            max_tokens=300,
        )
        text = resp.choices[0].message.content or ""
        import re
        match = re.search(r"\[.*\]", text, re.DOTALL)
        if match:
            return json.loads(match.group())
    except Exception:
        pass
    return []


# ============================================================
# 集成钩子：将记忆注入提示词
# ============================================================

# （提示词已移至 app.prompts）


def build_memory_context(memories: list[dict]) -> str:
    """格式化检索到的记忆，用于注入提示词。"""
    if not memories:
        return ""
    lines = []
    for m in memories[:5]:
        score_bar = "⭐" * min(5, m["importance"] // 2)
        lines.append(f"- {score_bar} {m['content']}")
    return MEMORY_PREFIX.format(memories="\n".join(lines))
