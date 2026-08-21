"""设置 API：读取、保存、测试连接。"""
from __future__ import annotations

import time

import httpx
from fastapi import APIRouter, HTTPException
from openai import AsyncOpenAI
from pydantic import BaseModel

from app.config import settings as env_settings
from app.core.user_settings import (
    UserSettings,
    _normalize_local_url,
    chat_config,
    embedding_config,
    get_settings,
    mask_settings,
    save_settings,
)
from app.core.auth import _hash_password, _verify_password

router = APIRouter(prefix="/api/settings", tags=["settings"])


class SettingsUpdate(BaseModel):
    chat_provider: str = "deepseek"
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    chat_thinking: bool = True
    chat_context_window: int = 0
    embedding_provider: str = "ollama"
    embedding_model: str = "qwen3-embedding:4b"
    embedding_base_url: str = "http://127.0.0.1:11434/v1"
    embedding_api_key: str = ""
    search_provider: str = "auto"  # auto | tavily | searxng | bing | duckduckgo
    search_api_key: str = ""
    search_base_url: str = ""
    neo4j_enabled: bool = False
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = "neo4j"
    require_password: bool = False
    password: str = ""
    app_port: int = 8001
    close_to_tray: bool = True


@router.get("/public")
async def public_settings():
    """公开设置：仅返回是否需要访问密码。"""
    s = get_settings()
    return {"require_password": s.require_password, "configured": s.configured}


@router.get("")
async def read_settings():
    """读取设置（密钥脱敏）。"""
    return mask_settings(get_settings())


@router.put("")
async def write_settings(data: SettingsUpdate):
    """保存设置。密码为空表示不修改现有密码。"""
    current = get_settings()
    payload = data.model_dump()
    # 密钥字段：前端传 *** 时保留原值
    for field in ("chat_api_key", "embedding_api_key", "search_api_key", "neo4j_password"):
        if payload.get(field) in ("", "***"):
            payload[field] = getattr(current, field)
    payload["embedding_base_url"] = _normalize_local_url(payload.get("embedding_base_url") or "")
    try:
        port = int(payload.get("app_port", 8001))
    except (TypeError, ValueError):
        raise HTTPException(400, "端口必须是数字")
    if not (1024 <= port <= 65535):
        raise HTTPException(400, "端口必须在 1024-65535 之间")
    if int(payload.get("chat_context_window", 0) or 0) < 0:
        raise HTTPException(400, "上下文窗口不能为负数")
    if port in (6333, 6379, 11434, 7687):
        raise HTTPException(400, f"端口 {port} 已被其他服务占用")
    payload["app_port"] = port
    if payload.get("password"):
        payload["password_hash"] = _hash_password(payload["password"])
    else:
        payload["password_hash"] = current.password_hash
    payload.pop("password", None)
    if payload.get("require_password") and not payload.get("password_hash"):
        raise HTTPException(400, "开启访问密码必须设置密码")
    settings = UserSettings(**payload)
    save_settings(settings)
    return mask_settings(settings)


class ChatTestRequest(BaseModel):
    provider: str = "deepseek"
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    thinking: bool = True


@router.post("/test/chat")
async def test_chat(req: ChatTestRequest):
    """测试聊天模型连接。"""
    cfg = chat_config()
    base = req.base_url or cfg["base_url"]
    key = req.api_key if req.api_key not in ("", "***") else cfg["api_key"]
    model = req.model or cfg["model"]
    extra = cfg["extra_body"] if req.provider == "deepseek" else {}
    try:
        client = AsyncOpenAI(base_url=base, api_key=key, timeout=httpx.Timeout(30.0, connect=5.0))
        t0 = time.perf_counter()
        kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": "ping"}],
            "max_tokens": 8,
        }
        if extra:
            kwargs["extra_body"] = extra
        resp = await client.chat.completions.create(**kwargs)
        elapsed = round(time.perf_counter() - t0, 2)
        return {"ok": True, "elapsed": elapsed, "reply": (resp.choices[0].message.content or "")[:50]}
    except Exception as e:
        return {"ok": False, "error": f"连接失败: {e}"}

class SearchTestRequest(BaseModel):
    query: str = "最新 AI 新闻"
    provider: str = ""
    api_key: str = ""
    base_url: str = ""


@router.post("/test/search")
async def test_search(req: SearchTestRequest):
    """测试联网搜索配置。"""
    from app.rag.web_search import perform_web_search

    try:
        key = req.api_key if req.api_key not in ("", "***") else ""
        data = await perform_web_search(
            req.query,
            top_k=3,
            provider=req.provider,
            api_key=key,
            base_url=req.base_url,
        )
        if data.get("error"):
            return {"ok": False, "error": data["error"]}
        first = data.get("results", [{}])[0] if data.get("results") else {}
        return {
            "ok": True,
            "count": data.get("count", 0),
            "provider": data.get("provider", ""),
            "first": first.get("title", ""),
        }
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}




@router.post("/test/embedding")
async def test_embedding():
    """测试向量化模型连接。"""
    from app.core.embedding import embed_text
    try:
        t0 = time.perf_counter()
        vec = embed_text("测试")
        elapsed = round(time.perf_counter() - t0, 2)
        return {"ok": True, "elapsed": elapsed, "dim": len(vec), "config": embedding_config()}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.post("/test/neo4j")
async def test_neo4j(uri: str = "", user: str = "", password: str = ""):
    """测试 Neo4j 连接。"""
    try:
        from neo4j import GraphDatabase
        s = get_settings()
        uri = uri or s.neo4j_uri
        user = user or s.neo4j_user
        if password in ("", "***"):
            password = s.neo4j_password
        driver = GraphDatabase.driver(uri, auth=(user, password))
        driver.verify_connectivity()
        driver.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:300]}


@router.get("/status")
async def system_status():
    """系统运行状态：调试模式、端口、OCR 与内置模型可用性。"""
    from app.core.user_settings import DEBUG_MODE
    from app.core.ocr import is_tesseract_available
    from app.core.embedding import _bundled_model_dir
    s = get_settings()
    return {
        "debug_mode": DEBUG_MODE,
        "app_port": s.app_port,
        "service_port": env_settings.service_port,
        "close_to_tray": s.close_to_tray,
        "ocr_available": is_tesseract_available(),
        "bundled_model": _bundled_model_dir() is not None,
    }


@router.post("/apply-port")
async def apply_port():
    """请求桌面启动器在保存的新端口上重启后端。"""
    from app.core.user_settings import DEBUG_MODE
    from app.core.app_control import request_restart
    if DEBUG_MODE:
        raise HTTPException(400, "调试模式下端口由 .env 的 SERVICE_PORT 控制，不支持热重启")
    s = get_settings()
    request_restart(s.app_port)
    return {"ok": True, "port": s.app_port}


@router.post("/verify-password")
async def verify_password(data: dict):
    """校验访问密码（用于开启密码模式后重新登录）。"""
    s = get_settings()
    if not s.require_password:
        return {"ok": True}
    if not s.password_hash:
        return {"ok": False}
    return {"ok": _verify_password(data.get("password", ""), s.password_hash)}
