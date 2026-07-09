"""Tool Calling Framework v2: LangChain 1.0+ @tool decorator + create_agent

References:
  LangChain 1.0 create_agent: https://docs.langchain.com/oss/python/langchain/agents
  @tool decorator: langchain_core.tools.tool
"""
from __future__ import annotations
import json
import logging
import time
from typing import Optional

from langchain_core.tools import tool as lc_tool

logger = logging.getLogger(__name__)

# Global tool registry (for non-LangChain consumers that need raw access)
_tools_list: list[dict] = []


def _to_openai_schema(lc_tool_instance) -> dict:
    """Convert a LangChain tool to OpenAI function-calling schema."""
    return {
        "type": "function",
        "function": {
            "name": lc_tool_instance.name,
            "description": lc_tool_instance.description,
            "parameters": lc_tool_instance.args_schema.model_json_schema() if lc_tool_instance.args_schema else {"type": "object", "properties": {}},
        },
    }


# ============================================================
# Tool implementations (LangChain 1.0+ @tool decorator)
# ============================================================

@lc_tool
async def search_knowledge_base(query: str, kb_id: str = "default", top_k: int = 5) -> str:
    """搜索企业知识库中的文档内容。

    当用户询问需要文档支撑的问题时调用此工具。
    例如："什么是RAG"、"怎么做Prompt工程"、"介绍一下多智能体"等。
    不要用于：简单问候闲聊、数学计算、文件列表统计、历史记忆查询。
    """
    from app.rag.retriever import retrieve
    try:
        sources = retrieve(query=query, top_k=top_k, kb_id=kb_id, rerank_strategy="mmr", recall_multiplier=4)
        if not sources:
            return json.dumps({"count": 0, "results": [], "is_empty": True}, ensure_ascii=False)
        return json.dumps({
            "count": len(sources),
            "results": [
                {"index": i + 1, "filename": s.filename, "content": s.content[:300],
                 "score": round(s.score, 3), "page": s.page_number}
                for i, s in enumerate(sources)
            ],
            "is_empty": False,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"检索失败: {e}", "count": 0, "results": [], "is_empty": True}, ensure_ascii=False)


@lc_tool
async def memory_search(query: str, top_k: int = 3) -> str:
    """搜索对话历史记忆。

    当用户问"上次聊过什么"、"之前讨论过...吗"、"还记得...吗"时调用此工具。
    不要用于：普通知识问答、数学计算、文件统计。
    """
    from app.rag.memory import retrieve_long_term_memory
    memories = await retrieve_long_term_memory(query=query, user_id="default", top_k=top_k)
    if not memories:
        return json.dumps({"count": 0, "memories": [], "is_empty": True}, ensure_ascii=False)
    return json.dumps({
        "count": len(memories),
        "memories": [{"index": i + 1, "content": m["content"][:200], "type": m["memory_type"],
                       "importance": m["importance"]} for i, m in enumerate(memories)],
        "is_empty": False,
    }, ensure_ascii=False)


@lc_tool
async def doc_stats(kb_id: str = "default") -> str:
    """查看知识库的文档统计信息和完整文件列表。

    当用户问"有哪些文件"、"几个文档"、"文件列表"、"工作区有什么文件"时调用此工具。
    返回 JSON 包含 file_list（完整文件名数组）和 file_count。你必须逐条列出 file_list 中的每个文件名。
    不要用于：搜索文档内容、查询历史记忆、计算。
    """
    from qdrant_client import QdrantClient
    from app.config import settings
    try:
        client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
        col_name = f"kb_{kb_id}"
        result = client.scroll(collection_name=col_name, limit=10000, with_payload=True, with_vectors=False)
        doc_ids = set()
        filenames = set()
        total_chunks = 0
        for point in result[0]:
            did = point.payload.get("doc_id", "")
            if did and not did.startswith("parent_"):
                doc_ids.add(did)
            fn = point.payload.get("filename", "")
            if fn:
                filenames.add(fn)
            total_chunks += 1
        return json.dumps({
            "kb_id": kb_id, "doc_count": len(doc_ids), "file_count": len(filenames),
            "total_chunks": total_chunks, "file_list": sorted(list(filenames))[:30],
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"统计失败: {e}", "kb_id": kb_id, "doc_count": 0, "file_list": []}, ensure_ascii=False)


@lc_tool
async def calculator(expression: str) -> str:
    """执行数学计算。用户要求计算数值时使用。

    不要用于：列举文件、搜索记忆、普通数学概念解释。
    """
    import re
    if not re.match(r"^[\d\s+\-*/().%^]+$", expression):
        return json.dumps({"error": "表达式包含非法字符", "result": None}, ensure_ascii=False)
    try:
        result = eval(expression, {"__builtins__": {}}, {})
        return json.dumps({"expression": expression, "result": result, "computed": True}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"计算错误: {e}", "result": None}, ensure_ascii=False)


@lc_tool
async def remember(content: str, importance: int = 7, memory_type: str = "fact") -> str:
    """让系统记住重要信息。用户说"记住这个"、"别忘了"、"帮我记一下"时调用此工具。

    不要用于：临时查询、文件操作。
    """
    from app.rag.memory import store_long_term_memory
    importance = max(1, min(10, importance))
    await store_long_term_memory(
        user_id="default", content=content,
        memory_type=memory_type, importance=importance,
        session_id="explicit",
    )
    return json.dumps({"action": "stored", "content_preview": content[:100], "importance": importance, "type": memory_type}, ensure_ascii=False)


# ============================================================
# Tool collections
# ============================================================

BUILTIN_TOOLS_LC = [
    search_knowledge_base,
    memory_search,
    doc_stats,
    calculator,
    remember,
]


def get_langchain_tools() -> list:
    """Get tools in LangChain format (for create_agent)."""
    return BUILTIN_TOOLS_LC


def get_tools_openai() -> list[dict]:
    """Get tools in OpenAI function-calling format."""
    if not _tools_list:
        for t in BUILTIN_TOOLS_LC:
            _tools_list.append(_to_openai_schema(t))
    return _tools_list


# Legacy compat: register_builtin_tools, get_tools, execute_tool
def register_builtin_tools():
    """No-op: tools are auto-registered via @tool decorator."""
    logger.info(f"LangChain 1.0+ tools ready: {len(BUILTIN_TOOLS_LC)} tools")


def get_tools() -> list[dict]:
    """Legacy: return OpenAI-format tools."""
    return get_tools_openai()


async def execute_tool(name: str, arguments: dict, user_id: str = "default") -> str:
    """Legacy: execute a tool by name. Used by hand-rolled agent loop."""
    t0 = time.time()
    tool_map = {t.name: t for t in BUILTIN_TOOLS_LC}
    if name not in tool_map:
        return json.dumps({"status": "error", "error": f"工具 '{name}' 未注册"}, ensure_ascii=False)
    try:
        result = await tool_map[name].ainvoke(arguments)
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(f"Tool {name} completed in {elapsed_ms}ms")
        return result
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return json.dumps({"status": "error", "error": f"工具 {name} 执行失败: {str(e)}"}, ensure_ascii=False)
