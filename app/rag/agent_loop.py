"""智能体循环 v2：LangChain 1.0+ create_agent

使用 LangChain 内置的智能体图替换手写工具循环。
流式与 OpenAI 格式兼容场景回退到手动循环。
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 5


def create_langchain_agent(model_name: str = None):
    """创建包含全部内置工具的 LangChain 1.0+ 智能体。

    返回可用于 ainvoke/astream 的 CompiledStateGraph。
    """
    from langchain.agents import create_agent
    from langchain.chat_models import init_chat_model
    from app.rag.tools import BUILTIN_TOOLS_LC
    from app.prompts import SYSTEM_PROMPT

    model_str = model_name or f"openai:{settings.deepseek_model}"
    model = init_chat_model(
        model_str,
        openai_api_key=settings.deepseek_api_key,
        openai_api_base=settings.deepseek_base_url,
    )

    agent = create_agent(
        model=model,
        tools=BUILTIN_TOOLS_LC,
        system_prompt=SYSTEM_PROMPT,
    )
    return agent


async def agent_loop_stream(
    client,
    messages: list[dict],
    user_id: str = "default",
    kb_id: str = "default",
    enable_graphrag: bool = True,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncGenerator[str, None]:
    """旧版流式智能体循环，输出工具调用事件供前端展示。

    与 graph.py._agent_loop 使用相同的 OpenAI 格式工具调用。
    """
    from app.rag.tools import get_tools, execute_tool, tool_result_status
    from app.core.user_settings import chat_config
    cfg = chat_config()

    tools = get_tools()
    pending_reasoning = ""
    for m in reversed(messages):
        if m.get("role") == "assistant" and m.get("reasoning_content"):
            pending_reasoning = m["reasoning_content"]
            break

    for round_idx in range(max_rounds):
        kwargs = {
            "model": cfg["model"],
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": True,
        }
        if cfg.get("extra_body"):
            kwargs["extra_body"] = cfg["extra_body"]
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = await client.chat.completions.create(**kwargs)

        tool_calls_acc = {}
        finish_reason = None
        round_reasoning = ""

        async for chunk in stream:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # 捕获 reasoning_content 以保存到历史记录
            if getattr(delta, "reasoning_content", None):
                round_reasoning += delta.reasoning_content
                yield "__REASONING__:" + delta.reasoning_content

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_acc:
                        tool_calls_acc[idx] = {"id": tc.id or "", "function": {"name": "", "arguments": ""}}
                    if tc.id:
                        tool_calls_acc[idx]["id"] = tc.id
                    if tc.function and tc.function.name:
                        tool_calls_acc[idx]["function"]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_acc[idx]["function"]["arguments"] += tc.function.arguments

            if delta.content:
                yield delta.content

        if round_reasoning:
            pending_reasoning = round_reasoning

        if finish_reason != "tool_calls" or not tool_calls_acc:
            return

        sorted_tcs = sorted(tool_calls_acc.items(), key=lambda x: x[0])
        # 将所有工具调用合并为单条 assistant 消息（修复 reasoning_content 400 错误）
        all_tool_calls = []
        tool_results = {}  # tc_id -> 结果字符串
        prepared = []
        for _, tc_data in sorted_tcs:
            tool_name = tc_data["function"]["name"]
            try:
                args = json.loads(tc_data["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}
            logger.info(f"Agent calls tool: {tool_name}({args})")
            yield f"__TOOL_CALL__:{json.dumps({'name': tool_name, 'args': args}, ensure_ascii=False)}"
            prepared.append((tc_data, tool_name, args))

        results = await asyncio.gather(
            *(
                execute_tool(tool_name, args, user_id=user_id, kb_id=kb_id, enable_graphrag=enable_graphrag)
                for _, tool_name, args in prepared
            ),
            return_exceptions=True,
        )

        for (tc_data, tool_name, args), result in zip(prepared, results):
            if isinstance(result, Exception):
                result = json.dumps({"error": f"工具执行失败: {result}", "count": 0, "is_empty": True}, ensure_ascii=False)
            _status = tool_result_status(result)
            yield f"__TOOL_RESULT__:{json.dumps({'name': tool_name, 'result': result[:8000], 'status': _status}, ensure_ascii=False)}"

            all_tool_calls.append({
                "id": tc_data["id"], "type": "function",
                "function": {"name": tool_name, "arguments": tc_data["function"]["arguments"]}
            })
            tool_results[tc_data["id"]] = result

        # 包含全部工具调用的单条 assistant 消息
        asst_msg = {"role": "assistant", "content": None, "tool_calls": all_tool_calls}
        if round_reasoning:
            asst_msg["reasoning_content"] = round_reasoning
        elif pending_reasoning:
            # DeepSeek 思考模式：每条带工具调用的 assistant 消息都必须
            # 携带本轮思维链，而不是空占位符。
            asst_msg["reasoning_content"] = pending_reasoning
        messages.append(asst_msg)
        for _, tc_data in sorted_tcs:
            tc_id = tc_data["id"]
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": tool_results.get(tc_id, "")})

    # 强制生成最终回答
    resp2 = await client.chat.completions.create(
        model=cfg["model"], messages=messages,
        temperature=0.3, max_tokens=2048, stream=True,
    )
    async for chunk in resp2:
        delta = chunk.choices[0].delta
        if getattr(delta, "reasoning_content", None):
            yield "__REASONING__:" + delta.reasoning_content
        if delta.content:
            yield delta.content
