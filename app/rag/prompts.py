"""RAG prompt builders — context compression, parent-child expansion, history formatting

Prompt strings (SYSTEM_PROMPT, REFUSAL_RESPONSE) moved to app.prompts.
"""
from __future__ import annotations
from typing import Optional
from app.prompts import SYSTEM_PROMPT, REFUSAL_RESPONSE

CHARS_PER_TOKEN = 3.0
MAX_CONTEXT_CHARS = 6000
MAX_SOURCE_CONTENT_CHARS = 1500


def _compress_context(sources: list[dict], max_chars: int = MAX_CONTEXT_CHARS,
                      max_per_source: int = MAX_SOURCE_CONTENT_CHARS) -> str:
    """Build context string with token budget control and parent-child expansion."""
    blocks = []
    for doc in sources:
        content = doc.get("content", "")
        if len(content) > max_per_source:
            content = content[:max_per_source]
            last_period = max(content.rfind("。"), content.rfind(". "), content.rfind("\n"))
            if last_period > max_per_source * 0.6:
                content = content[:last_period + 1]
        page = doc.get("page_number")
        page_str = f"第 {page} 页" if page else ""
        source_info = f"【来源：{doc['filename']}，{page_str}】"
        block = f"{source_info}\n{content}"
        blocks.append((doc.get("score", 0.0), block, len(block)))
    blocks.sort(key=lambda x: x[0], reverse=True)
    selected = []
    total = 0
    for _, block, blen in blocks:
        if total + blen > max_chars:
            remaining = max_chars - total
            if remaining > 200:
                selected.append(block[:remaining] + "\n...")
            break
        selected.append(block)
        total += blen
    return "\n\n---\n\n".join(selected)


def expand_parents(sources: list[dict], kb_id: str = "default") -> list[dict]:
    """Expand child chunks to parent chunks for richer LLM context."""
    from app.core import vector_store
    from qdrant_client import QdrantClient, models
    from app.config import settings as s

    parent_ids = set()
    for src in sources:
        pid = src.get("parent_id")
        if pid:
            parent_ids.add(pid)

    if not parent_ids:
        return sources

    client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, check_compatibility=False)
    col_name = f"kb_{kb_id}"
    parent_content_map = {}

    for pid in parent_ids:
        try:
            result = client.scroll(
                collection_name=col_name,
                scroll_filter=models.Filter(must=[
                    models.FieldCondition(key="doc_id", match=models.MatchValue(value=pid))
                ]),
                limit=1, with_payload=True, with_vectors=False,
            )
            if result[0]:
                parent_content_map[pid] = result[0][0].payload.get("content", "")
        except Exception:
            pass

    if not parent_content_map:
        return sources

    expanded = []
    seen_parents = set()
    for s in sources:
        pid = s.get("parent_id")
        if pid and pid in parent_content_map:
            if pid not in seen_parents:
                seen_parents.add(pid)
                expanded.append({
                    **s,
                    "content": parent_content_map[pid],
                    "score": max(s2["score"] for s2 in sources if s2.get("parent_id") == pid),
                })
        else:
            expanded.append(s)
    return expanded


def build_rag_prompt(query: str, context: list[dict],
                     max_context_chars: int = MAX_CONTEXT_CHARS,
                     kb_id: str = "default") -> str:
    """Build RAG prompt with parent-child expansion and compressed context."""
    expanded = expand_parents(context, kb_id)
    context_text = _compress_context(expanded, max_chars=max_context_chars)
    return f"""## 检索到的文档

{context_text}

## 用户问题
{query}

请基于上述文档内容回答。如果信息不足，请明确告知。"""


def build_history_prompt(history: list[dict], query: str, context: list[dict],
                         max_history_turns: int = 6,
                         max_context_chars: int = MAX_CONTEXT_CHARS,
                         kb_id: str = "default") -> str:
    """Build prompt with chat history, parent-child expansion, and compressed context."""
    expanded = expand_parents(context, kb_id)
    context_text = _compress_context(expanded, max_chars=max_context_chars)
    history_text = ""
    if history:
        recent = history[-(max_history_turns * 2):]
        history_lines = []
        for msg in recent:
            role = "用户" if msg.get("role") == "user" else "助手"
            content = msg.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            history_lines.append(f"{role}: {content}")
        history_text = "\n".join(history_lines)
    if history_text:
        return f"""## 检索到的文档

{context_text}

## 对话历史
{history_text}

## 用户最新问题
{query}

请结合对话历史和文档内容回答。如果信息不足，请明确告知。"""
    else:
        return f"""## 检索到的文档

{context_text}

## 用户问题
{query}

请基于上述文档内容回答。如果信息不足，请明确告知。"""
