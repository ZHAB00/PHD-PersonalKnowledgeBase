"""Agent loop v2: LangChain 1.0+ create_agent

Replaces hand-rolled tool-calling loop with LangChain's built-in agent graph.
Falls back to manual loop for streaming and OpenAI-format compatibility.
"""
from __future__ import annotations
import json
import logging
from typing import AsyncGenerator

from app.config import settings

logger = logging.getLogger(__name__)
MAX_TOOL_ROUNDS = 5


def create_langchain_agent(model_name: str = None):
    """Create a LangChain 1.0+ agent with all builtin tools.

    Returns a CompiledStateGraph ready for ainvoke/astream.
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
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncGenerator[str, None]:
    """Legacy streaming agent loop with tool call events for frontend display.

    Uses the same OpenAI-format tool calling as graph.py._agent_loop.
    """
    from app.rag.tools import get_tools, execute_tool

    tools = get_tools()

    for round_idx in range(max_rounds):
        kwargs = {
            "model": settings.deepseek_model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 2048,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        stream = client.chat.completions.create(**kwargs)

        tool_calls_acc = {}
        finish_reason = None
        round_reasoning = ""

        for chunk in stream:
            delta = chunk.choices[0].delta
            finish_reason = chunk.choices[0].finish_reason

            # Capture reasoning_content for preservation in history
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

        if finish_reason != "tool_calls" or not tool_calls_acc:
            return

        sorted_tcs = sorted(tool_calls_acc.items(), key=lambda x: x[0])
        # Group all tool calls into a SINGLE assistant message (fixes reasoning_content 400 error)
        all_tool_calls = []
        tool_results = {}  # tc_id -> result string
        for _, tc_data in sorted_tcs:
            tool_name = tc_data["function"]["name"]
            try:
                args = json.loads(tc_data["function"]["arguments"])
            except json.JSONDecodeError:
                args = {}

            logger.info(f"Agent calls tool: {tool_name}({args})")
            yield f"__TOOL_CALL__:{json.dumps({'name': tool_name, 'args': args}, ensure_ascii=False)}"

            result = await execute_tool(tool_name, args, user_id=user_id)
            yield f"__TOOL_RESULT__:{json.dumps({'name': tool_name, 'result': result[:8000]}, ensure_ascii=False)}"

            all_tool_calls.append({
                "id": tc_data["id"], "type": "function",
                "function": {"name": tool_name, "arguments": tc_data["function"]["arguments"]}
            })
            tool_results[tc_data["id"]] = result

        # Single assistant message with all tool calls
        asst_msg = {"role": "assistant", "content": None, "tool_calls": all_tool_calls}
        if round_reasoning:
            asst_msg["reasoning_content"] = round_reasoning
        else:
            # DeepSeek thinking mode: ensure reasoning_content continuity in the chain
            has_prior_rc = any(
                m.get("role") == "assistant" and m.get("reasoning_content")
                for m in messages
            )
            if has_prior_rc:
                asst_msg["reasoning_content"] = ""
        messages.append(asst_msg)
        for _, tc_data in sorted_tcs:
            tc_id = tc_data["id"]
            messages.append({"role": "tool", "tool_call_id": tc_id, "content": tool_results.get(tc_id, "")})

    # Force final answer
    resp2 = client.chat.completions.create(
        model=settings.deepseek_model, messages=messages,
        temperature=0.3, max_tokens=2048, stream=True,
    )
    for chunk in resp2:
        delta = chunk.choices[0].delta
        if getattr(delta, "reasoning_content", None):
            yield "__REASONING__:" + delta.reasoning_content
        if delta.content:
            yield delta.content
