"""智能体循环 v4：LangGraph StateGraph

使用 LangGraph 显式状态图管理 ReAct 循环：
  START -> agent -> tools -> agent -> ... -> END

- agent 节点保持 OpenAI 兼容流式调用，保留 DeepSeek reasoning_content
- tools 节点并行执行 LangChain 工具
- 通过 get_stream_writer 输出前端 SSE 事件
- recursion_limit 作为图执行深度上限，节点内再用 max_rounds 控制工具轮次
"""
from __future__ import annotations

import asyncio
import json
import logging
import operator
from typing import Annotated, Any, AsyncGenerator, TypedDict

logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 5


class _AgentState(TypedDict):
    """LangGraph 共享状态：消息累加、工具消息独立留存、用量统计与轮次。"""
    messages: Annotated[list[dict], operator.add]
    tool_messages: Annotated[list[dict], operator.add]
    usage: dict
    rounds: int


def _empty_usage() -> dict:
    return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def _find_pending_reasoning(messages: list[dict]) -> str:
    """从历史中找回最近一条带 reasoning_content 的助手消息。

    DeepSeek 思考模式要求带工具调用的助手消息必须携带思维链，
    不能使用空占位符，否则部分模型会返回 400。
    """
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            return m["reasoning_content"]
    return ""


def _accumulate_usage(chunk: Any, usage: dict) -> None:
    """尽量从流式/非流式响应中累加 token 用量。"""
    u = getattr(chunk, "usage", None)
    if not u:
        return
    usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
    usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
    usage["total_tokens"] += getattr(u, "total_tokens", 0) or 0


def _build_agent_graph(
    client,
    *,
    user_id: str,
    kb_id: str,
    enable_graphrag: bool,
    max_rounds: int,
    stream_events: bool,
):
    """构建 LangGraph 状态图。

    stream_events=True 时 agent/tools 节点通过 get_stream_writer 输出
    前端 SSE 事件；False 时用于非流式 ainvoke，只保留状态与用量。
    """
    from langgraph.config import get_stream_writer
    from langgraph.graph import END, START, StateGraph

    from app.core.user_settings import chat_config
    from app.rag.tools import execute_tool, get_tools, tool_result_status

    tools = get_tools()

    async def agent_node(state: _AgentState) -> dict:
        cfg = chat_config()
        messages = list(state["messages"])
        rounds = int(state.get("rounds", 0))
        pending_reasoning = _find_pending_reasoning(messages)
        usage = dict(state.get("usage") or _empty_usage())

        kwargs = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": stream_events,
        }
        if cfg.get("extra_body"):
            kwargs["extra_body"] = cfg["extra_body"]
        # 达到上限后不再给模型工具，强制生成最终回答
        if rounds < max_rounds and tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        if not stream_events:
            resp = await client.chat.completions.create(**kwargs)
            msg = resp.choices[0].message
            _accumulate_usage(resp, usage)
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                pending_reasoning = rc
            if getattr(msg, "tool_calls", None):
                all_tool_calls = []
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    logger.info(f"Agent calls tool: {tool_name}({args})")
                    all_tool_calls.append({
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tool_name, "arguments": tc.function.arguments},
                    })
                asst_msg = {"role": "assistant", "content": getattr(msg, "content", None), "tool_calls": all_tool_calls}
                if pending_reasoning:
                    asst_msg["reasoning_content"] = pending_reasoning
                return {
                    "messages": [asst_msg],
                    "tool_messages": [asst_msg],
                    "rounds": rounds + 1,
                    "usage": usage,
                }
            asst_msg = {"role": "assistant", "content": getattr(msg, "content", "") or ""}
            if rc:
                asst_msg["reasoning_content"] = rc
            return {"messages": [asst_msg], "rounds": rounds + 1, "usage": usage}

        # 流式分支：逐 token 输出内容，工具调用在完整接收后统一输出
        writer = get_stream_writer()
        stream = await client.chat.completions.create(**kwargs)

        tool_calls_acc: dict[int, dict] = {}
        finish_reason = None
        content_parts: list[str] = []
        round_reasoning = ""

        async for chunk in stream:
            if not getattr(chunk, "choices", None):
                _accumulate_usage(chunk, usage)
                continue
            choice = chunk.choices[0]
            delta = choice.delta
            finish_reason = choice.finish_reason

            _accumulate_usage(chunk, usage)

            if getattr(delta, "reasoning_content", None):
                round_reasoning += delta.reasoning_content
                writer("__REASONING__:" + delta.reasoning_content)

            if getattr(delta, "tool_calls", None):
                for tc in delta.tool_calls:
                    idx = tc.index
                    acc = tool_calls_acc.setdefault(
                        idx, {"id": tc.id or "", "function": {"name": "", "arguments": ""}}
                    )
                    if tc.id:
                        acc["id"] = tc.id
                    if tc.function and tc.function.name:
                        acc["function"]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        acc["function"]["arguments"] += tc.function.arguments

            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                writer(delta.content)

        if round_reasoning:
            pending_reasoning = round_reasoning

        if finish_reason == "tool_calls" and tool_calls_acc:
            sorted_tcs = sorted(tool_calls_acc.items(), key=lambda item: item[0])
            all_tool_calls = []
            for _, tc_data in sorted_tcs:
                tool_name = tc_data["function"]["name"]
                try:
                    args = json.loads(tc_data["function"]["arguments"])
                except json.JSONDecodeError:
                    args = {}
                logger.info(f"Agent calls tool: {tool_name}({args})")
                writer(f"__TOOL_CALL__:{json.dumps({'name': tool_name, 'args': args}, ensure_ascii=False)}")
                all_tool_calls.append({
                    "id": tc_data["id"],
                    "type": "function",
                    "function": {"name": tool_name, "arguments": tc_data["function"]["arguments"]},
                })
            asst_msg = {"role": "assistant", "content": None, "tool_calls": all_tool_calls}
            if pending_reasoning:
                asst_msg["reasoning_content"] = pending_reasoning
            return {
                "messages": [asst_msg],
                "tool_messages": [asst_msg],
                "rounds": rounds + 1,
                "usage": usage,
            }

        asst_msg = {"role": "assistant", "content": "".join(content_parts)}
        if round_reasoning:
            asst_msg["reasoning_content"] = round_reasoning
        return {"messages": [asst_msg], "rounds": rounds + 1, "usage": usage}

    async def tools_node(state: _AgentState) -> dict:
        messages = state.get("messages") or []
        last = messages[-1]
        prepared = []
        for tc in last.get("tool_calls") or []:
            function = tc.get("function") or {}
            tool_name = function.get("name", "")
            try:
                args = json.loads(function.get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {}
            prepared.append((tc.get("id", ""), tool_name, args))

        results = await asyncio.gather(
            *(
                execute_tool(tool_name, args, user_id=user_id, kb_id=kb_id, enable_graphrag=enable_graphrag)
                for _, tool_name, args in prepared
            ),
            return_exceptions=True,
        )

        out_messages = []
        out_tool_messages = []
        writer = get_stream_writer() if stream_events else None
        for (tc_id, tool_name, args), result in zip(prepared, results):
            if isinstance(result, Exception):
                result = json.dumps(
                    {"status": "error", "error": f"工具执行失败: {result}", "count": 0, "is_empty": True},
                    ensure_ascii=False,
                )
            status = tool_result_status(result)
            if writer:
                writer(
                    "__TOOL_RESULT__:"
                    + json.dumps({"name": tool_name, "result": result[:8000], "status": status}, ensure_ascii=False)
                )
            tool_msg = {"role": "tool", "tool_call_id": tc_id, "content": result}
            out_messages.append(tool_msg)
            out_tool_messages.append(tool_msg)
        return {"messages": out_messages, "tool_messages": out_tool_messages}

    def route_after_agent(state: _AgentState) -> str:
        messages = state.get("messages") or []
        if not messages:
            return END
        last = messages[-1]
        # 工具轮次上限由 agent 节点控制：达到上限后不再绑定工具，
        # 这里再按 rounds 兜底一次，防止异常响应越过上限
        if last.get("tool_calls") and int(state.get("rounds", 0)) <= max_rounds:
            return "tools"
        return END

    graph = StateGraph(_AgentState)
    graph.add_node("agent", agent_node)
    graph.add_node("tools", tools_node)
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", route_after_agent, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def _graph_config(max_rounds: int) -> dict:
    # agent 与 tools 各算一次递归，预留最终回答与安全余量
    return {"recursion_limit": max_rounds * 2 + 4}


async def langgraph_agent_stream(
    client,
    messages: list[dict],
    user_id: str = "default",
    kb_id: str = "default",
    enable_graphrag: bool = True,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncGenerator[str, None]:
    """LangGraph 驱动的主链路流式入口，输出前端 SSE 事件。"""
    graph = _build_agent_graph(
        client,
        user_id=user_id,
        kb_id=kb_id,
        enable_graphrag=enable_graphrag,
        max_rounds=max_rounds,
        stream_events=True,
    )
    initial = {"messages": list(messages), "tool_messages": [], "usage": _empty_usage(), "rounds": 0}
    async for event in graph.astream(initial, config=_graph_config(max_rounds), stream_mode="custom"):
        if isinstance(event, str):
            yield event


async def langgraph_agent_run(
    client,
    messages: list[dict],
    user_id: str = "default",
    kb_id: str = "default",
    enable_graphrag: bool = True,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> tuple[str, list[dict], dict]:
    """LangGraph 驱动的非流式入口，返回 (回答, 工具消息对, token 用量)。"""
    graph = _build_agent_graph(
        client,
        user_id=user_id,
        kb_id=kb_id,
        enable_graphrag=enable_graphrag,
        max_rounds=max_rounds,
        stream_events=False,
    )
    initial = {"messages": list(messages), "tool_messages": [], "usage": _empty_usage(), "rounds": 0}
    result = await graph.ainvoke(initial, config=_graph_config(max_rounds))
    tool_messages = result.get("tool_messages", [])
    usage = result.get("usage") or _empty_usage()
    answer = ""
    for m in reversed(result.get("messages", [])):
        if m.get("role") == "assistant":
            answer = m.get("content") or ""
            break
    return answer, tool_messages, usage


async def agent_loop_stream(
    client,
    messages: list[dict],
    user_id: str = "default",
    kb_id: str = "default",
    enable_graphrag: bool = True,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncGenerator[str, None]:
    """兼容旧调用方的入口，内部全部走 LangGraph 状态图。"""
    async for event in langgraph_agent_stream(
        client, messages, user_id=user_id, kb_id=kb_id,
        enable_graphrag=enable_graphrag, max_rounds=max_rounds,
    ):
        yield event


def create_langchain_agent(model_name: str = None):
    """备用入口：基于 LangGraph create_react_agent 构建标准 ReAct 图。

    主链路不使用此入口（为了保留 DeepSeek reasoning_content 与自定义 SSE 事件），
    保留给需要在标准 LangGraph 图上做二次开发或对比测试的场景。
    """
    from langchain_openai import ChatOpenAI
    from langgraph.prebuilt import create_react_agent

    from app.core.user_settings import chat_config
    from app.prompts import SYSTEM_PROMPT
    from app.rag.tools import BUILTIN_TOOLS_LC

    cfg = chat_config()
    model = ChatOpenAI(
        model=model_name or cfg["model"],
        api_key=cfg["api_key"],
        base_url=cfg["base_url"],
        temperature=0.3,
        max_tokens=2048,
        extra_body=cfg.get("extra_body") or None,
    )
    return create_react_agent(model=model, tools=BUILTIN_TOOLS_LC, prompt=SYSTEM_PROMPT)
