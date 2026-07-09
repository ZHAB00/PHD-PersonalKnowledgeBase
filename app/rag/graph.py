"""RAG chat engine v4: Tool-first architecture — RAG as a tool, not pre-fetch

Flow:
  1. User message arrives
  2. Memory context loaded (cached)
  3. LLM receives: system prompt + memory + history + user message + TOOLS
  4. LLM decides: answer directly (greetings) OR call tools (search_knowledge_base, etc.)
  5. Agent loop: tool call -> execute -> feed result -> LLM answers
  6. Post-response: summarize, extract facts, store memories
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
from typing import AsyncGenerator, Optional
import httpx
from openai import OpenAI

from app.config import settings
from app.rag.retriever import retrieve
from app.rag.prompts import build_history_prompt
from app.rag.memory import (
    apply_token_budget, should_summarize, summarize_conversation,
    store_long_term_memory, retrieve_long_term_memory,
    extract_key_facts, score_importance, build_memory_context,
)
from app.rag.tools import get_tools, execute_tool
from app.models.chat import ChatResponse, SourceReference, ToolCallEvent
from app.core import cache
from app.prompts import SYSTEM_PROMPT, REFUSAL_RESPONSE

logger = logging.getLogger(__name__)

CHAT_HISTORY_TTL = 86400
MAX_TOOL_ROUNDS = 5


def _get_deepseek_client(timeout: float = 120.0) -> OpenAI:
    return OpenAI(base_url=settings.deepseek_base_url, api_key=settings.deepseek_api_key,
                   timeout=httpx.Timeout(timeout, connect=10.0))


# ============================================================
# History helpers
# ============================================================

def _history_key(session_id: str) -> str:
    return f"chat:history:{session_id}"


async def _get_history(session_id: str) -> list[dict]:
    data = await cache.get_json(_history_key(session_id))
    return data if data else []


async def _set_history(session_id: str, history: list[dict]):
    await cache.set_json(_history_key(session_id), history, ex=CHAT_HISTORY_TTL)


def _count_turns(history: list[dict]) -> int:
    return sum(1 for m in history if m.get("role") == "user")


# ============================================================
# Tool call events builder
# ============================================================

def _build_tool_call_events(tool_messages: list[dict]) -> list[ToolCallEvent]:
    events = []
    for i in range(0, len(tool_messages) - 1, 2):
        tc_msg = tool_messages[i]
        result_msg = tool_messages[i + 1] if i + 1 < len(tool_messages) else {}
        if tc_msg.get("tool_calls"):
            for tc in tc_msg["tool_calls"]:
                try:
                    args = json.loads(tc["function"]["arguments"])
                except (json.JSONDecodeError, KeyError):
                    args = {}
                result_str = result_msg.get("content", "")[:300]
                events.append(ToolCallEvent(
                    tool_name=tc["function"]["name"],
                    arguments=args,
                    result=result_str,
                    status="ok" if "error" not in result_str.lower() else "error",
                ))
    return events


# ============================================================
# Agent loop
# ============================================================

async def _agent_loop(
    client: OpenAI,
    messages: list[dict],
    kb_id: str = "default",
    user_id: str = "default",
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> tuple[str, list[dict]]:
    tools = get_tools()
    tool_messages = []

    for round_idx in range(max_rounds):
        kwargs = {
            "model": settings.deepseek_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        resp = client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message

        if not msg.tool_calls:
            return msg.content or "", tool_messages

        for tc in msg.tool_calls:
            tool_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except json.JSONDecodeError:
                args = {}

            logger.info(f"Agent calls tool: {tool_name}({args})")
            result = await execute_tool(tool_name, args, user_id=user_id)

            tool_msg_assistant = {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tool_name, "arguments": tc.function.arguments},
                }],
            }
            tool_msg_result = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            }
            messages.append(tool_msg_assistant)
            messages.append(tool_msg_result)
            tool_messages.append(tool_msg_assistant)
            tool_messages.append(tool_msg_result)

    return "抱歉，处理超时，请尝试简化您的问题。", tool_messages


# ============================================================
# After-response: summarize + extract facts
# ============================================================

async def _after_response_tasks(session_id: str, user_id: str, history: list[dict]):
    turn_count = _count_turns(history)
    if await should_summarize(turn_count):
        try:
            summary = await summarize_conversation(history, user_id)
            if summary:
                imp = await score_importance(summary)
                await store_long_term_memory(
                    user_id=user_id, content=f"[会话摘要] {summary}",
                    memory_type="summary", importance=imp, session_id=session_id,
                )
        except Exception as e:
            logger.warning(f"Summarize failed: {e}")

    try:
        facts = await extract_key_facts(history)
        for fact in facts:
            content = fact.get("fact", "")
            if content:
                imp = await score_importance(content)
                await store_long_term_memory(
                    user_id=user_id, content=content,
                    memory_type=fact.get("type", "fact"),
                    importance=imp, session_id=session_id,
                )
    except Exception as e:
        logger.warning(f"Fact extraction failed: {e}")


# ============================================================
# Source extraction from tool results
# ============================================================

def _extract_sources(tool_messages: list[dict]) -> list[SourceReference]:
    """Extract SourceReference objects from search_knowledge_base tool results."""
    sources = []
    for msg in tool_messages:
        if msg.get("role") != "tool":
            continue
        try:
            data = json.loads(msg["content"])
            if data.get("status") == "ok" and "data" in data:
                data = data["data"]
            for r in data.get("results", []):
                sources.append(SourceReference(
                    doc_id=r.get("filename", ""),
                    filename=r.get("filename", ""),
                    content=r.get("content", ""),
                    score=r.get("score", 0.0),
                    page_number=r.get("page"),
                ))
        except (json.JSONDecodeError, KeyError):
            pass
    return sources


# ============================================================
# Public API
# ============================================================

async def chat(
    session_id: str,
    message: str,
    kb_id: str = "default",
    tenant_id: str = "default",
    top_k: int = 5,
    rerank_strategy: str = "none",
    user_id: str = "default",
) -> ChatResponse:
    """Unified chat: LLM decides whether to call tools (RAG search, memory, etc.).

    No pre-fetch of RAG context. Greetings like "hello" go straight to LLM.
    Knowledge questions trigger search_knowledge_base tool automatically.
    """
    # Fast path: file listing queries bypass LLM (deterministic, no hallucination risk)
    file_list_patterns = ["有哪些文档", "有哪些文件", "文件列表", "文档列表", "几个文档",
                          "几个文件", "列出文件", "列出文档", "什么文件", "什么文档",
                          "哪些文档", "哪些文件", "工作区有", "文档统计", "文件统计",
                          "查一下有哪些", "看看有哪些"]
    q = message.strip().lower()
    if any(p in q for p in file_list_patterns):
        import json as _json
        from app.rag.tools import doc_stats as _doc_stats_tool
        result = _json.loads(await _doc_stats_tool.ainvoke({"kb_id": kb_id}))
        fl = result.get("file_list", [])
        if fl:
            fls = "\n".join(str(i+1) + ". " + f for i, f in enumerate(fl))
            ans = "当前知识库共有 " + str(result.get("doc_count", len(fl))) + " 个文件：\n\n" + fls
        else:
            ans = "当前知识库暂无文档。"
        history = await _get_history(session_id)
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": ans})
        await _set_history(session_id, history)
        return ChatResponse(session_id=session_id, answer=ans, sources=[], tool_calls=[])

    history = await _get_history(session_id)

    # Memory context (cached, 30min)
    mem_cache_key = f"mem:cache:{session_id}:{user_id}"
    memories = await cache.get_json(mem_cache_key)
    if memories is None:
        memories = await retrieve_long_term_memory(query=message, user_id=user_id, top_k=3)
        await cache.set_json(mem_cache_key, memories, ex=1800)
    memory_ctx = build_memory_context(memories)

    # Build messages: system prompt + tools available
    # Strip empty tool_calls from history (DeepSeek rejects tool_calls: [])
    for h in history:
        tc = h.get("tool_calls")
        if isinstance(tc, list):
            if len(tc) == 0:
                del h["tool_calls"]
            elif tc and "tool_name" in tc[0]:
                del h["tool_calls"]

    user_prompt = build_history_prompt(history, message, [], kb_id=kb_id)
    history_budgeted = apply_token_budget(history)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Inject current KB context so LLM knows which KB to query
    messages.append({"role": "system", "content": f"当前知识库ID: {kb_id}。调用 doc_stats 或 search_knowledge_base 时必须使用此 kb_id。"})
    if memory_ctx:
        messages.append({"role": "system", "content": memory_ctx})
    for h in history_budgeted:
        messages.append(h)
    messages.append({"role": "user", "content": user_prompt})

    # Agent loop: LLM drives everything
    client = _get_deepseek_client()
    answer, tool_msgs = await _agent_loop(client, messages, kb_id=kb_id, user_id=user_id)

    # Extract sources from tool results for frontend display
    sources = _extract_sources(tool_msgs)

    # Save history (with tool calls for frontend display)
    tc_data = [tc.model_dump() for tc in _build_tool_call_events(tool_msgs)]
    history.append({"role": "user", "content": message})
    history.append({"role": "assistant", "content": answer, "tool_calls": tc_data})
    await _set_history(session_id, history)

    # Background tasks
    asyncio.create_task(cache.delete(mem_cache_key))
    asyncio.create_task(_after_response_tasks(session_id, user_id, history))

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        sources=sources,
        tool_calls=_build_tool_call_events(tool_msgs),
    )


async def chat_stream(
    session_id: str,
    message: str,
    kb_id: str = "default",
    tenant_id: str = "default",
    top_k: int = 5,
    rerank_strategy: str = "none",
    user_id: str = "default",
) -> AsyncGenerator[str, None]:
    """Streaming chat: uses agent loop with tool call events for frontend."""
    history = await _get_history(session_id)

    # Memory
    mem_cache_key = f"mem:cache:{session_id}:{user_id}"
    memories = await cache.get_json(mem_cache_key)
    if memories is None:
        memories = await retrieve_long_term_memory(query=message, user_id=user_id, top_k=3)
        await cache.set_json(mem_cache_key, memories, ex=1800)
    memory_ctx = build_memory_context(memories)
    for h in history:
        tc = h.get("tool_calls")
        if isinstance(tc, list):
            if len(tc) == 0:
                del h["tool_calls"]
            elif tc and "tool_name" in tc[0]:
                del h["tool_calls"]

    user_prompt = build_history_prompt(history, message, [], kb_id=kb_id)
    history_budgeted = apply_token_budget(history)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # Inject current KB context so LLM knows which KB to query
    messages.append({"role": "system", "content": f"当前知识库ID: {kb_id}。调用 doc_stats 或 search_knowledge_base 时必须使用此 kb_id。"})
    if memory_ctx:
        messages.append({"role": "system", "content": memory_ctx})
    for h in history_budgeted:
        messages.append(h)
    messages.append({"role": "user", "content": user_prompt})

    # Yield sources placeholder
    yield "__SOURCES__:[]"

    # Use agent loop with tool call events
    from app.rag.agent_loop import agent_loop_stream
    client = _get_deepseek_client(timeout=120.0)
    tool_msgs = []
    full_answer = ""
    stream_tool_calls = []  # collect tool call info for history

    try:
        async for chunk in agent_loop_stream(client, messages, user_id=user_id):
            if chunk.startswith("__TOOL_CALL__:"):
                try:
                    tc_data = json.loads(chunk[len("__TOOL_CALL__:"):])
                    stream_tool_calls.append(ToolCallEvent(
                        tool_name=tc_data["name"],
                        arguments=tc_data.get("args", {}),
                        result="",
                        status="ok",
                    ))
                except Exception:
                    pass
                yield chunk
            elif chunk.startswith("__TOOL_RESULT__:"):
                # Update last tool call with result
                if stream_tool_calls:
                    try:
                        tr_data = json.loads(chunk[len("__TOOL_RESULT__:"):])
                        stream_tool_calls[-1].result = tr_data.get("result", "")[:300]
                    except Exception:
                        pass
                yield chunk
            else:
                full_answer += chunk
                yield chunk

        # Save history with tool calls
        tc_list = [tc.model_dump() for tc in stream_tool_calls]
        history.append({"role": "user", "content": message})
        history.append({"role": "assistant", "content": full_answer, "tool_calls": tc_list})
        await _set_history(session_id, history)

        # Background
        asyncio.create_task(cache.delete(mem_cache_key))
        asyncio.create_task(_after_response_tasks(session_id, user_id, history))
    except Exception as e:
        logger.error(f"Stream error: {e}")
        yield f"AI 服务暂时不可用: {str(e)}"


async def get_chat_history(session_id: str) -> list[dict]:
    return await _get_history(session_id)


async def clear_chat_history(session_id: str):
    await cache.delete(_history_key(session_id))