"""RAG 聊天引擎 v4：工具优先架构 —— RAG 作为工具，而非预取

流程：
  1. 用户消息到达
  2. 加载记忆上下文（缓存）
  3. LLM 接收：系统提示词 + 记忆 + 历史 + 用户消息 + 工具
  4. LLM 决定：直接回答（问候）或调用工具（search_knowledge_base 等）
  5. 智能体循环：调用工具 -> 执行 -> 返回结果 -> LLM 回答
  6. 回答后：总结、抽取事实、存储记忆
"""
from __future__ import annotations
import asyncio
import hashlib
import json
import logging
import re
import threading
import time
from typing import AsyncGenerator, Optional
import httpx
from openai import AsyncOpenAI

from app.config import settings
from app.rag.retriever import retrieve
from app.rag.memory import (
    CONTEXT_WINDOW_TURNS, TOOL_RESULT_CONTEXT_LIMIT, build_llm_history,
    find_reusable_answer, should_summarize, summarize_conversation,
    store_long_term_memory, retrieve_long_term_memory,
    extract_key_facts, score_importance, build_memory_context,
)
from app.rag.tools import tool_result_status
from app.models.chat import ChatResponse, SourceReference, ToolCallEvent
from app.core import cache
from app.core.user_settings import chat_config
from app.core.chat_store import save_history, load_history, load_history_paginated, delete_history as delete_chat_history
from app.prompts import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

CHAT_HISTORY_TTL = 86400
MAX_TOOL_ROUNDS = 5


def _background_task(coro, name=""):
    """后台任务：启动后即忘，并做好错误处理。"""
    async def _wrapped():
        try:
            await coro
        except Exception as e:
            logger.debug(f"Background task {name!r} failed (non-critical): {e}")
    asyncio.create_task(_wrapped())


def _background_thread(coro, name=""):
    """把含同步阻塞调用的协程放到独立线程，避免卡住事件循环。"""
    def _wrapped():
        try:
            asyncio.run(coro)
        except Exception as e:
            logger.debug(f"Background thread {name!r} failed (non-critical): {e}")
    threading.Thread(target=_wrapped, name=name or "bg-task", daemon=True).start()


def _get_deepseek_client(timeout: float = 120.0) -> AsyncOpenAI:
    from app.core.user_settings import chat_config
    cfg = chat_config()
    return AsyncOpenAI(base_url=cfg["base_url"], api_key=cfg["api_key"],
                       timeout=httpx.Timeout(timeout, connect=10.0))


# ============================================================
# 历史记录辅助函数
# ============================================================

def _history_key(session_id: str) -> str:
    return f"chat:history:{session_id}"


async def _get_history(session_id: str) -> list[dict]:
    data = load_history(session_id)
    # 保留 reasoning_content，供 deepseek-v4-flash 思考模式使用
    return data


async def _set_history(session_id: str, history: list[dict]):
    save_history(session_id, history)


def _count_turns(history: list[dict]) -> int:
    return sum(1 for m in history if m.get("role") == "user")


# ============================================================
# 工具调用事件构建
# ============================================================

def _extract_sources_from_result(result_str) -> list[dict]:
    """从工具结果字符串中提取可持久化的来源引用。"""
    sources = []
    try:
        data = json.loads(result_str) if isinstance(result_str, str) else result_str
        if not isinstance(data, dict):
            return sources
        if data.get("status") == "ok" and "data" in data:
            data = data["data"]
        for r in data.get("results", []):
            sources.append({
                "doc_id": r.get("doc_id", ""),
                "filename": r.get("filename", ""),
                "content": r.get("content", ""),
                "score": r.get("score", 0.0),
                "page_number": r.get("page"),
            })
    except Exception:
        pass
    return sources


def _guard_output(answer: str) -> str:
    """脱敏泄露的密钥，并记录疑似拒答/系统提示词泄露信号。"""
    import re
    if not answer:
        return answer
    redacted = re.sub(r"sk-[A-Za-z0-9]{8,}", "[REDACTED]", answer)
    redacted = redacted.replace("kb123456", "[REDACTED]")
    low = redacted.lower()
    if any(k in low for k in ["system prompt", "系统提示词", "当前知识库id", "PDH-PKG 智能助手"]):
        logger.warning("Output guard: possible system prompt leak detected")
    if any(k in low for k in ["i cannot", "sorry, i can't", "抱歉，我无法", "拒绝回答"]):
        logger.info("Output guard: refusal detected")
    return redacted


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
                result_str = result_msg.get("content", "")
                events.append(ToolCallEvent(
                    tool_name=tc["function"]["name"],
                    arguments=args,
                    result=result_str[:300],
                    status=tool_result_status(result_str),
                    sources=_extract_sources_from_result(result_str),
                ))
    return events


def _bounded_llm_tool_messages(tool_messages: list[dict]) -> list[dict]:
    """把 OpenAI 格式工具消息限制在短期上下文可承受的长度内。"""
    bounded = []
    for m in tool_messages:
        mc = dict(m)
        if mc.get("role") == "tool" and mc.get("content"):
            mc["content"] = mc["content"][:TOOL_RESULT_CONTEXT_LIMIT]
        bounded.append(mc)
    return bounded


# ============================================================
# 智能体循环
# ============================================================

async def _agent_loop(
    client: AsyncOpenAI,
    messages: list[dict],
    kb_id: str = "default",
    user_id: str = "default",
    enable_graphrag: bool = True,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> tuple[str, list[dict], dict]:
    """LangGraph 状态图驱动的非流式智能体循环。"""
    from app.rag.agent_loop import langgraph_agent_run
    return await langgraph_agent_run(
        client, messages, user_id=user_id, kb_id=kb_id,
        enable_graphrag=enable_graphrag, max_rounds=max_rounds,
    )


# ============================================================
# 回答后处理：总结 + 抽取事实
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
                # 仅在标题仍为默认值时用总结更新会话标题
                try:
                    import re
                    from app.core.chat_store import get_session_title, save_session
                    cur = get_session_title(session_id)
                    is_default = (
                        not cur or cur == "???" or cur == "新建对话"
                        or cur.startswith("sess_")
                    )
                    if is_default:
                        title = re.sub(r'[#*_`~>|]', '', summary[:40]).replace('"', '').replace("'", '').strip()
                        if len(title) > 20:
                            title = title[:20]
                        if title:
                            save_session(session_id, title, "default")
                            logger.debug(f"Session {session_id[:12]} title updated: {title}")
                except Exception:
                    pass
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
# 从工具结果中提取来源
# ============================================================

def _extract_sources(tool_messages: list[dict]) -> list[SourceReference]:
    """从 search_knowledge_base 工具结果中提取 SourceReference 对象。"""
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
# 对外 API
# ============================================================

async def chat(
    session_id: str,
    message: str,
    kb_id: str = "default",
    tenant_id: str = "default",
    top_k: int = 5,
    rerank_strategy: str = "none",
    enable_graphrag: bool = True,
    user_id: str = "default",
) -> ChatResponse:
    """统一聊天入口：LLM 决定是否调用工具（RAG 检索、记忆等）。

    不预取 RAG 上下文。像 "hello" 这样的问候直接交给 LLM。
    知识类问题会自动触发 search_knowledge_base 工具。
    """
    # 快速路径：文件列表类查询绕过 LLM（确定性输出，无幻觉风险）
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
        history.append({"role": "user", "content": message, "kb_id": kb_id})
        history.append({"role": "assistant", "content": ans, "kb_id": kb_id})
        await _set_history(session_id, history)
        return ChatResponse(session_id=session_id, answer=ans, sources=[], tool_calls=[])

    history = await _get_history(session_id)

    # 记忆上下文（缓存 30 分钟）
    mem_cache_key = f"mem:cache:{session_id}:{user_id}"
    memories = await cache.get_json(mem_cache_key)
    if memories is None:
        memories = await retrieve_long_term_memory(query=message, user_id=user_id, top_k=3)
        await cache.set_json(mem_cache_key, memories, ex=1800)
    memory_ctx = build_memory_context(memories)

    # 构建消息：系统提示词 + 可用工具
    # 从历史记录中剔除空 tool_calls（DeepSeek 拒绝 tool_calls: []）
    # 从历史副本构建 LLM 消息，保留原始历史用于保存
    # 短期上下文：最近 N 轮 + 75% 上下文预算 + 上一轮 tool message
    history_budgeted = build_llm_history(history, cfg=chat_config(), max_turns=CONTEXT_WINDOW_TURNS)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 注入当前知识库上下文，让 LLM 知道要检索哪个知识库
    messages.append({"role": "system", "content": f"\u5f53\u524d\u77e5\u8bc6\u5e93ID: {kb_id}\u3002\u8c03\u7528 doc_stats \u6216 search_knowledge_base \u65f6\u5fc5\u987b\u4f7f\u7528\u6b64 kb_id\u3002"})
    if find_reusable_answer(history, message, kb_id=kb_id):
        messages.append({"role": "system", "content": "检测到最近对话已就相同或高度相似的问题完成回答。除非用户要求最新/更新/重新检索，否则直接基于上述历史回答，不要重复调用 search_knowledge_base。"})
    untrusted_parts = []
    if memory_ctx:
        untrusted_parts.append("[\u957f\u671f\u8bb0\u5fc6]\n" + memory_ctx)
    for h in history_budgeted:
        messages.append(h)
    user_content = message
    if untrusted_parts:
        user_content += "\n\n<untrusted>\n" + "\n\n".join(untrusted_parts) + "\n</untrusted>"
    messages.append({"role": "user", "content": user_content})

    # 智能体循环：由 LLM 驱动
    client = _get_deepseek_client()
    answer, tool_msgs, usage = await _agent_loop(client, messages, kb_id=kb_id, user_id=user_id, enable_graphrag=enable_graphrag)
    answer = _guard_output(answer)

    # 从工具结果中提取来源，供前端展示
    sources = _extract_sources(tool_msgs)

    # 保存历史记录（含工具调用，供前端展示；tool message 单独保留给下一轮 LLM）
    tc_data = [tc.model_dump() for tc in _build_tool_call_events(tool_msgs)]
    ts = time.time()
    history.append({"role": "user", "content": message, "ts": ts, "kb_id": kb_id})
    msg = {"role": "assistant", "content": answer, "ts": ts, "kb_id": kb_id}
    if tc_data:
        msg["tool_calls"] = tc_data
    if tool_msgs:
        msg["llm_tool_messages"] = _bounded_llm_tool_messages(tool_msgs)
    history.append(msg)
    await _set_history(session_id, history)

    # 后台任务（启动后即忘，带错误处理）
    _background_task(cache.delete(mem_cache_key), "cache.delete(mem_cache_key)")
    _background_thread(_after_response_tasks(session_id, user_id, history), "after_response_tasks")

    return ChatResponse(
        session_id=session_id,
        answer=answer,
        sources=sources,
        tool_calls=_build_tool_call_events(tool_msgs),
        token_usage=usage,
    )



def _yield_sources(stream_tool_calls: list) -> str:
    """从工具调用结果提取来源，并输出 __SOURCES__: JSON。"""
    import json as _json
    sources_data = []
    for tc in stream_tool_calls:
        if tc.tool_name == "search_knowledge_base" and tc.result:
            for r in _extract_sources_from_result(tc.result):
                sources_data.append({
                    "filename": r.get("filename", ""),
                    "content": r.get("content", "")[:200],
                    "score": r.get("score", 0.0),
                })
    logger.info(f"Yield sources: {len(sources_data)} items from {len(stream_tool_calls)} tool calls")
    return "__SOURCES__:" + _json.dumps(sources_data, ensure_ascii=False)


async def chat_stream(
    session_id: str,
    message: str,
    kb_id: str = "default",
    tenant_id: str = "default",
    top_k: int = 5,
    rerank_strategy: str = "none",
    enable_graphrag: bool = True,
    user_id: str = "default",
) -> AsyncGenerator[str, None]:
    """流式聊天：使用智能体循环并输出工具调用事件供前端展示。"""
    t0 = time.perf_counter()
    history = await _get_history(session_id)

    # 记忆
    mem_cache_key = f"mem:cache:{session_id}:{user_id}"
    memories = await cache.get_json(mem_cache_key)
    if memories is None:
        memories = await retrieve_long_term_memory(query=message, user_id=user_id, top_k=3)
        await cache.set_json(mem_cache_key, memories, ex=1800)
    memory_ctx = build_memory_context(memories)
    # 从历史副本构建 LLM 消息，保留原始历史用于保存
    # 短期上下文：最近 N 轮 + 75% 上下文预算 + 上一轮 tool message
    history_budgeted = build_llm_history(history, cfg=chat_config(), max_turns=CONTEXT_WINDOW_TURNS)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    # 注入当前知识库上下文，让 LLM 知道要检索哪个知识库
    messages.append({"role": "system", "content": f"\u5f53\u524d\u77e5\u8bc6\u5e93ID: {kb_id}\u3002\u8c03\u7528 doc_stats \u6216 search_knowledge_base \u65f6\u5fc5\u987b\u4f7f\u7528\u6b64 kb_id\u3002"})
    if find_reusable_answer(history, message, kb_id=kb_id):
        messages.append({"role": "system", "content": "检测到最近对话已就相同或高度相似的问题完成回答。除非用户要求最新/更新/重新检索，否则直接基于上述历史回答，不要重复调用 search_knowledge_base。"})
    untrusted_parts = []
    if memory_ctx:
        untrusted_parts.append("[\u957f\u671f\u8bb0\u5fc6]\n" + memory_ctx)
    for h in history_budgeted:
        messages.append(h)
    user_content = message
    if untrusted_parts:
        user_content += "\n\n<untrusted>\n" + "\n\n".join(untrusted_parts) + "\n</untrusted>"
    messages.append({"role": "user", "content": user_content})

    # 使用带工具调用事件的智能体循环
    from app.rag.agent_loop import langgraph_agent_stream
    client = _get_deepseek_client(timeout=120.0)
    tool_msgs = []
    full_answer = ""
    reasoning_content_acc = ""
    stream_tool_calls = []  # collect tool call info for history
    llm_rounds = []
    current_llm_round = None
    pending_tool_results = 0
    reasoning_buf = ""
    round_reasoning = ""
    last_tool_reasoning = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            last_tool_reasoning = m["reasoning_content"]
            break

    try:
        async for chunk in langgraph_agent_stream(client, messages, user_id=user_id, kb_id=kb_id, enable_graphrag=enable_graphrag):
            if chunk.startswith("__TOOL_CALL__:"):
                try:
                    tc_data = json.loads(chunk[len("__TOOL_CALL__:"):])
                    stream_tool_calls.append(ToolCallEvent(
                        tool_name=tc_data["name"],
                        arguments=tc_data.get("args", {}),
                        result="",
                        status="ok",
                    ))
                    if current_llm_round is None or pending_tool_results == 0:
                        current_llm_round = {"assistant": None, "tools": [], "ids": []}
                        llm_rounds.append(current_llm_round)
                        round_reasoning = reasoning_buf or last_tool_reasoning
                        last_tool_reasoning = round_reasoning
                        reasoning_buf = ""
                    if current_llm_round["assistant"] is None:
                        current_llm_round["assistant"] = {"role": "assistant", "content": None, "tool_calls": []}
                        if round_reasoning:
                            current_llm_round["assistant"]["reasoning_content"] = round_reasoning
                    tc_id = f"call_{len(llm_rounds)-1}_{len(current_llm_round['assistant']['tool_calls'])}"
                    current_llm_round["assistant"]["tool_calls"].append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": tc_data["name"],
                            "arguments": json.dumps(tc_data.get("args", {}), ensure_ascii=False),
                        },
                    })
                    current_llm_round["ids"].append(tc_id)
                    pending_tool_results += 1
                except Exception as ex:
                    logger.warning(f"Failed to parse tool call: {ex}")
                yield chunk
            elif chunk.startswith("__TOOL_RESULT__:"):
                # 用结果更新最后一条工具调用
                if stream_tool_calls:
                    try:
                        tr_data = json.loads(chunk[len("__TOOL_RESULT__:"):])
                        stream_tool_calls[-1].result = tr_data.get("result", "")[:8000]
                        stream_tool_calls[-1].status = tr_data.get("status") or tool_result_status(tr_data.get("result", ""))
                        stream_tool_calls[-1].sources = _extract_sources_from_result(tr_data.get("result", ""))
                        pending_tool_results = max(0, pending_tool_results - 1)
                        if current_llm_round and current_llm_round.get("ids"):
                            tc_id = current_llm_round["ids"].pop(0)
                            current_llm_round["tools"].append({
                                "role": "tool",
                                "tool_call_id": tc_id,
                                "name": tr_data.get("name", ""),
                                "content": (tr_data.get("result") or "")[:TOOL_RESULT_CONTEXT_LIMIT],
                            })
                    except Exception as ex:
                        logger.warning(f"Failed to parse tool result: {ex}")
                yield chunk
                # 增量输出来源，即使后续调用失败前端也能看到
                yield _yield_sources(stream_tool_calls)
            elif chunk.startswith("__REASONING__:"):
                reasoning_text = chunk[len("__REASONING__:"):]
                reasoning_content_acc += reasoning_text
                reasoning_buf += reasoning_text
            else:
                full_answer += chunk
                yield chunk

        # 最终输出来源（在所有工具结果处理完之后）
        yield _yield_sources(stream_tool_calls)

        # 保存带工具调用的历史记录（tool message 单独保留给下一轮 LLM）
        full_answer = _guard_output(full_answer)
        tc_list = [tc.model_dump() for tc in stream_tool_calls]
        llm_tool_messages = []
        for r in llm_rounds:
            if r.get("assistant"):
                llm_tool_messages.append(r["assistant"])
                llm_tool_messages.extend(r.get("tools", []))
        ts = time.time()
        history.append({"role": "user", "content": message, "ts": ts, "kb_id": kb_id})
        msg = {"role": "assistant", "content": full_answer, "ts": ts, "kb_id": kb_id}
        if reasoning_content_acc:
            msg["reasoning_content"] = reasoning_content_acc
        if tc_list:
            msg["tool_calls"] = tc_list
        if llm_tool_messages:
            msg["llm_tool_messages"] = llm_tool_messages
        history.append(msg)
        await _set_history(session_id, history)
        logger.info(
            f"Chat stream {session_id[:12]}: total={time.perf_counter()-t0:.2f}s "
            f"tools={len(stream_tool_calls)} chars={len(full_answer)}"
        )

        # 后台任务（启动后即忘，带错误处理）
        _background_task(cache.delete(mem_cache_key), "cache.delete(mem_cache_key)")
        _background_thread(_after_response_tasks(session_id, user_id, history), "after_response_tasks")
    except Exception as e:
        logger.error(f"Stream error: {e}")
        # 发送错误前先输出已收集的来源
        yield _yield_sources(stream_tool_calls)
        yield f"AI 服务暂时不可用: {str(e)}"


def _strip_internal_fields(history: list[dict]) -> list[dict]:
    """历史接口只返回前端展示字段，不暴露内部 tool message。"""
    for m in history:
        m.pop("llm_tool_messages", None)
    return history


async def get_chat_history(session_id: str) -> list[dict]:
    return _strip_internal_fields(await _get_history(session_id))


async def get_chat_history_paginated(session_id: str, offset: int = 0, limit: int = 20):
    """返回 (消息列表, 是否还有更多)，用于无限滚动。"""
    import asyncio
    loop = asyncio.get_running_loop()
    messages, has_more = await loop.run_in_executor(None, lambda: load_history_paginated(session_id, offset, limit))
    return _strip_internal_fields(messages), has_more


async def clear_chat_history(session_id: str):
    delete_chat_history(session_id)
