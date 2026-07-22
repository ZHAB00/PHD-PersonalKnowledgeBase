"""Memory system v2: 4-layer architecture per Agent Memory Best Practices

Layers:
  1. Short-term: Token-budgeted sliding window (Section 2)
  2. Long-term: Vector-based persistent memory with importance scoring (Section 3)
  3. Summarization: Periodic compression via LLM (Section 4)
  4. Importance scoring: LLM rates memories 1-10 (Section 3.2.3)
  5. User isolation: user_id filter on all memory operations (Section 8.2.1)
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
from datetime import datetime, UTC
from typing import Optional

from openai import OpenAI
import httpx

from app.core import cache
from app.config import settings
from app.prompts import SUMMARY_PROMPT, IMPORTANCE_PROMPT, EXTRACT_FACTS_PROMPT, MEMORY_PREFIX

logger = logging.getLogger(__name__)

# Budget config
TOKEN_BUDGET_CHARS = 6000
SYSTEM_BUDGET_CHARS = 800
SUMMARY_TRIGGER = 1          # summarize every turn
MEMORY_TTL = 86400 * 7       # 7 days

# Memory collection in Qdrant
MEMORY_COLLECTION = "kb_memory"


# ============================================================
# Layer 1: Short-term memory (Token-budgeted sliding window)
# ============================================================

def _estimate_chars(messages: list[dict]) -> int:
    return sum(len(m.get("content", "")) for m in messages)


def apply_token_budget(history: list[dict], max_chars: int = TOKEN_BUDGET_CHARS) -> list[dict]:
    """Sliding window with char budget. System messages preserved, user/assistant trimmed from head."""
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
# Layer 2: Long-term memory (vector store + user isolation)
# ============================================================

def _memory_collection_name() -> str:
    return MEMORY_COLLECTION


_memory_collection_ready = False

def _ensure_memory_collection():
    global _memory_collection_ready
    if _memory_collection_ready:
        return
    from app.core.vector_store import _get_client
    from qdrant_client.http import models
    client = _get_client()
    col_name = _memory_collection_name()
    existing = [c.name for c in client.get_collections().collections]
    if col_name not in existing:
        client.create_collection(
            collection_name=col_name,
            vectors_config=models.VectorParams(size=settings.embedding_dim, distance=models.Distance.COSINE),
        )
        client.create_payload_index(collection_name=col_name, field_name="user_id", field_schema=models.PayloadSchemaType.KEYWORD)
        client.create_payload_index(collection_name=col_name, field_name="memory_type", field_schema=models.PayloadSchemaType.KEYWORD)
        logger.info(f"Created memory collection: {col_name}")
    _memory_collection_ready = True


async def store_long_term_memory(
    user_id: str,
    content: str,
    memory_type: str = "episodic",
    importance: int = 5,
    session_id: str = "",
):
    """Store a memory fragment with dedup: update existing similar memory instead of duplicating."""
    from app.core.embedding import embed_text
    from app.core.vector_store import _get_client
    from qdrant_client.http import models
    from uuid import uuid4

    try:
        _ensure_memory_collection()
        content_trimmed = content[:2000]
        vector = embed_text(content_trimmed)
        client = _get_client()

        # Check for near-duplicate (same user, similar content)
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
            # Update existing memory instead of creating duplicate
            pt = existing.points[0]
            old_imp = pt.payload.get("importance", 5)
            new_imp = max(old_imp, importance)  # keep highest importance
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


async def retrieve_long_term_memory(
    query: str,
    user_id: str = "default",
    top_k: int = 5,
) -> list[dict]:
    """Retrieve relevant memories with multi-factor scoring (semantic + recency + importance)."""
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

            # Time decay: half-life = 7 days
            time_score = 1.0
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                    age_days = (now - ts).total_seconds() / 86400
                    time_score = 0.5 ** (age_days / 7)
                except Exception:
                    pass

            # Composite: 0.5 semantic + 0.25 recency + 0.25 importance
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


# ============================================================
# Layer 3: Session summarization (LLM compression)
# ============================================================



async def summarize_conversation(history: list[dict], user_id: str = "default") -> str:
    """Use LLM to compress conversation history into a summary."""
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
# Layer 4: Importance scoring (LLM rates memory)
# ============================================================

# (prompt moved to app.prompts)


async def score_importance(content: str) -> int:
    """LLM rates the importance of a conversation fragment (1-10)."""
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
# Layer 5: Extract key facts from conversation
# ============================================================

# (prompt moved to app.prompts)


async def extract_key_facts(history: list[dict]) -> list[dict]:
    """Extract key user facts/preferences from conversation for long-term storage."""
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
# Integration hook: inject memories into prompt
# ============================================================

# (prompt moved to app.prompts)


def build_memory_context(memories: list[dict]) -> str:
    """Format retrieved memories for prompt injection."""
    if not memories:
        return ""
    lines = []
    for m in memories[:5]:
        score_bar = "⭐" * min(5, m["importance"] // 2)
        lines.append(f"- {score_bar} {m['content']}")
    return MEMORY_PREFIX.format(memories="\n".join(lines))
