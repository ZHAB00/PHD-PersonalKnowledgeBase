"""工具调用框架 v2：LangChain 1.0+ @tool 装饰器 + LangGraph StateGraph

参考：
  LangGraph StateGraph：https://langchain-ai.github.io/langgraph/
  @tool 装饰器：langchain_core.tools.tool
"""
from __future__ import annotations
import asyncio
import contextvars
import json
import logging
import time
from typing import Optional

from langchain_core.tools import tool as lc_tool

from app.rag.web_search import web_search

logger = logging.getLogger(__name__)

# 单条检索结果送入模型的最大字符数。800 字足够覆盖长分块的关键信息，避免重要后半段被截断。
SEARCH_RESULT_CONTENT_LIMIT = 800

_current_user: contextvars.ContextVar[str] = contextvars.ContextVar("kb_current_user", default="default")
_current_kb_id: contextvars.ContextVar[str] = contextvars.ContextVar("kb_current_kb_id", default="default")
_current_graphrag: contextvars.ContextVar[bool] = contextvars.ContextVar("kb_current_graphrag", default=True)

# 全局工具注册表（供非 LangChain 调用方直接访问）
_tools_list: list[dict] = []


def _to_openai_schema(lc_tool_instance) -> dict:
    """将 LangChain 工具转换为 OpenAI 函数调用格式。"""
    return {
        "type": "function",
        "function": {
            "name": lc_tool_instance.name,
            "description": lc_tool_instance.description,
            "parameters": lc_tool_instance.args_schema.model_json_schema() if lc_tool_instance.args_schema else {"type": "object", "properties": {}},
        },
    }


# ============================================================
# 工具实现（LangChain 1.0+ @tool 装饰器）
# ============================================================

@lc_tool
async def search_knowledge_base(query: str, kb_id: str = "default", top_k: int = 5) -> str:
    """搜索 PDH-PKG 中的文档内容。

    当用户询问需要文档支撑的问题时调用此工具。
    例如："什么是RAG"、"怎么做Prompt工程"、"介绍一下多智能体"等。
    不要用于：简单问候闲聊、数学计算、文件列表统计、历史记忆查询。
    """
    from app.rag.retriever import retrieve
    from app.core.vector_store import check_embedding_consistency
    from app.core.embedding import embedding_dimension
    kb_id = _current_kb_id.get() or kb_id
    try:
        if not check_embedding_consistency(kb_id):
            return json.dumps({
                "status": "error",
                "error": f"向量维度不匹配：当前嵌入模型为 {embedding_dimension()} 维，"
                         f"但知识库 {kb_id} 的向量索引不是该维度。请在设置中切换回与索引一致的嵌入模型，或重建该知识库。",
                "count": 0, "results": [], "is_empty": True,
            }, ensure_ascii=False)
        sources = await asyncio.to_thread(
            retrieve, query=query, top_k=top_k, kb_id=kb_id,
            rerank_strategy="mmr", recall_multiplier=4,
            enable_graphrag=_current_graphrag.get(), enable_rewrite=False,
        )
        if not sources:
            return json.dumps({"count": 0, "results": [], "is_empty": True}, ensure_ascii=False)
        return json.dumps({
            "count": len(sources),
            "results": [
                {"index": i + 1, "filename": s.filename, "content": s.content[:SEARCH_RESULT_CONTENT_LIMIT],
                 "score": round(s.score, 3), "page": s.page_number}
                for i, s in enumerate(sources)
            ],
            "is_empty": False,
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"检索失败: {e}", "count": 0, "results": [], "is_empty": True}, ensure_ascii=False)


@lc_tool
async def memory_search(query: str, top_k: int = 3) -> str:
    """搜索当前用户自己的对话历史记忆。

    当用户问"上次聊过什么"、"之前讨论过...吗"、"还记得...吗"时调用此工具。
    不要用于：普通知识问答、数学计算、文件统计。
    """
    from app.rag.memory import retrieve_long_term_memory
    memories = await retrieve_long_term_memory(query=query, user_id=_current_user.get(), top_k=top_k)
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
    kb_id = _current_kb_id.get() or kb_id
    def _stats_sync():
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
                "total_chunks": total_chunks, "file_list": sorted(list(filenames)),
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"error": f"统计失败: {e}", "kb_id": kb_id, "doc_count": 0, "file_list": []}, ensure_ascii=False)

    return await asyncio.to_thread(_stats_sync)


def _safe_eval_ast(node, depth=0):
    import ast
    if depth > 8:
        raise ValueError("expression too deep")
    if isinstance(node, ast.Expression):
        return _safe_eval_ast(node.body, depth)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, (int, float)):
            return node.value
        raise ValueError("unsupported constant")
    if isinstance(node, ast.BinOp):
        left = _safe_eval_ast(node.left, depth + 1)
        right = _safe_eval_ast(node.right, depth + 1)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            if right == 0:
                raise ValueError("divide by zero")
            return left / right
        if isinstance(node.op, ast.FloorDiv):
            if right == 0:
                raise ValueError("divide by zero")
            return left // right
        if isinstance(node.op, ast.Mod):
            if right == 0:
                raise ValueError("modulo by zero")
            return left % right
        if isinstance(node.op, ast.Pow):
            if isinstance(right, (int, float)) and (abs(right) > 1000 or abs(left) > 1e6):
                raise ValueError("power too large")
            result = left ** right
            if abs(result) > 1e15:
                raise ValueError("result too large")
            return result
        raise ValueError("unsupported operator")
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _safe_eval_ast(node.operand, depth + 1)
        return value if isinstance(node.op, ast.UAdd) else -value
    raise ValueError("unsupported expression")


@lc_tool
async def calculator(expression: str) -> str:
    """\u6267\u884c\u6570\u5b66\u8ba1\u7b97\u3002\u7528\u6237\u8981\u6c42\u8ba1\u7b97\u6570\u503c\u65f6\u4f7f\u7528\u3002"""
    import ast
    try:
        tree = ast.parse(expression, mode="eval")
        result = _safe_eval_ast(tree.body)
    except Exception as e:
        return json.dumps({"error": "\u8ba1\u7b97\u5931\u8d25: " + str(e), "result": None}, ensure_ascii=False)
    if result is None or abs(result) > 1e15:
        return json.dumps({"error": "\u8ba1\u7b97\u7ed3\u679c\u8d85\u51fa\u8303\u56f4", "result": None}, ensure_ascii=False)
    return json.dumps({"expression": expression, "result": result, "computed": True}, ensure_ascii=False)


@lc_tool
async def remember(content: str, importance: int = 7, memory_type: str = "fact") -> str:
    """让系统记住当前用户的重要信息。用户说"记住这个"、"别忘了"、"帮我记一下"时调用此工具。

    不要用于：临时查询、文件操作。
    """
    from app.rag.memory import store_long_term_memory, validate_memory_content
    reason = validate_memory_content(content)
    if reason:
        return json.dumps({"action": "blocked", "reason": reason}, ensure_ascii=False)
    importance = max(1, min(10, importance))
    await store_long_term_memory(
        user_id=_current_user.get(), content=content,
        memory_type=memory_type, importance=importance,
        session_id="explicit",
    )
    return json.dumps({"action": "stored", "content_preview": content[:100], "importance": importance, "type": memory_type}, ensure_ascii=False)


# ============================================================
# 工具集合
# ============================================================

def tool_result_status(result_str) -> str:
    """根据结构化工具结果判断成功/失败，避免把正文里的 error 词误判。"""
    data = None
    if isinstance(result_str, str):
        try:
            data = json.loads(result_str)
        except Exception:
            return "ok"
    else:
        data = result_str
    if isinstance(data, dict) and (data.get("status") == "error" or data.get("error")):
        return "error"
    return "ok"


# ============================================================
# 工具集合
# ============================================================
BUILTIN_TOOLS_LC = [
    search_knowledge_base,
    web_search,
    memory_search,
    doc_stats,
    calculator,
    remember,
]


def get_langchain_tools() -> list:
    """获取 LangChain 格式的工具（用于 LangGraph 标准图）。"""
    return BUILTIN_TOOLS_LC


def get_tools_openai() -> list[dict]:
    """获取 OpenAI 函数调用格式的工具。"""
    if not _tools_list:
        for t in BUILTIN_TOOLS_LC:
            _tools_list.append(_to_openai_schema(t))
    return _tools_list


# 旧版兼容：register_builtin_tools、get_tools、execute_tool
def register_builtin_tools():
    """空操作：工具通过 @tool 装饰器自动注册。"""
    logger.info(f"LangChain 1.0+ tools ready: {len(BUILTIN_TOOLS_LC)} tools")


def get_tools() -> list[dict]:
    """旧版兼容：返回 OpenAI 格式的工具。"""
    return get_tools_openai()


async def execute_tool(name: str, arguments: dict, user_id: str = "default", kb_id: str = "default", enable_graphrag: bool = True) -> str:
    """旧版兼容：按名称执行工具，供 LangGraph 工具节点使用。"""
    t0 = time.time()
    tool_map = {t.name: t for t in BUILTIN_TOOLS_LC}
    if name not in tool_map:
        return json.dumps({"status": "error", "error": f"工具 '{name}' 未注册"}, ensure_ascii=False)
    try:
        _current_user.set(user_id)
        _current_kb_id.set(kb_id)
        _current_graphrag.set(enable_graphrag)
        result = await tool_map[name].ainvoke(arguments)
        elapsed_ms = int((time.time() - t0) * 1000)
        logger.info(f"Tool {name} completed in {elapsed_ms}ms")
        return result
    except Exception as e:
        logger.error(f"Tool {name} failed: {e}")
        return json.dumps({"status": "error", "error": f"工具 {name} 执行失败: {str(e)}"}, ensure_ascii=False)
