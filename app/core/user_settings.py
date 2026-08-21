"""用户设置：集中管理对话模型、向量化、知识图谱与访问密码。"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Optional

from pydantic import BaseModel

from app.config import settings as env

DEBUG_MODE = os.environ.get("PDH_PKG_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
SETTINGS_FILE = Path(env.data_dir) / ("settings.debug.json" if DEBUG_MODE else "settings.json")
_lock = threading.Lock()
_cache: Optional["UserSettings"] = None
_cache_mtime: Optional[float] = None


class UserSettings(BaseModel):
    """用户可配置项。"""
    configured: bool = False
    chat_provider: str = "deepseek"  # deepseek | openai_compatible | ollama | lmstudio
    chat_base_url: str = ""
    chat_api_key: str = ""
    chat_model: str = ""
    chat_thinking: bool = True
    chat_context_window: int = 0  # 0 表示按 provider 自动
    embedding_provider: str = "local"  # local | ollama | openai_compatible
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_base_url: str = ""
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
    password_hash: str = ""
    app_port: int = 8001
    close_to_tray: bool = True


def _defaults() -> UserSettings:
    if DEBUG_MODE:
        return UserSettings(
            configured=True,
            chat_provider="deepseek",
            chat_base_url=env.deepseek_base_url,
            chat_api_key=env.deepseek_api_key,
            chat_model=env.deepseek_model,
            embedding_provider="ollama",
            embedding_model=env.embedding_model,
            embedding_base_url=env.embedding_base_url,
            neo4j_enabled=True,
            neo4j_uri="bolt://localhost:7687",
            neo4j_user="neo4j",
            neo4j_password=env.neo4j_password,
            neo4j_database="neo4j",
            app_port=8001,
            close_to_tray=True,
        )
    return UserSettings(
        chat_base_url=env.deepseek_base_url,
        chat_api_key=env.deepseek_api_key,
        chat_model=env.deepseek_model,
        embedding_provider="local",
        embedding_model="BAAI/bge-small-zh-v1.5",
        embedding_base_url="",
        neo4j_enabled=False,
        neo4j_uri=env.neo4j_uri,
        neo4j_user=env.neo4j_user,
        neo4j_password=env.neo4j_password,
        neo4j_database=env.neo4j_database,
        app_port=8001,
        close_to_tray=True,
    )


def _load() -> UserSettings:
    global _cache, _cache_mtime
    if SETTINGS_FILE.exists():
        try:
            mtime = SETTINGS_FILE.stat().st_mtime
            if _cache is not None and _cache_mtime == mtime:
                return _cache
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            _cache = UserSettings(**data)
            _cache_mtime = mtime
            return _cache
        except Exception:
            pass
    return _defaults()


def get_settings() -> UserSettings:
    if DEBUG_MODE:
        return _defaults()
    with _lock:
        return _load()


def save_settings(settings: UserSettings) -> UserSettings:
    if DEBUG_MODE:
        return _defaults()
    global _cache, _cache_mtime
    with _lock:
        settings.configured = True
        SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(settings.model_dump(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        _cache = settings
        _cache_mtime = SETTINGS_FILE.stat().st_mtime
    try:
        from app.core.embedding import reset_embedding
        reset_embedding()
        from app.rag.retriever import clear_retrieve_cache
        clear_retrieve_cache()
        from app.rag.memory import clear_memory_retrieve_cache
        clear_memory_retrieve_cache()
        from app.rag.reranker import clear_rerank_cache
        clear_rerank_cache()
    except Exception:
        pass
    return settings


def mask_settings(settings: UserSettings) -> dict:
    """返回脱敏后的设置，供前端展示。"""
    data = settings.model_dump()
    data["chat_api_key"] = "***" if settings.chat_api_key else ""
    data["embedding_api_key"] = "***" if settings.embedding_api_key else ""
    data["search_api_key"] = "***" if settings.search_api_key else ""
    data["neo4j_password"] = "***" if settings.neo4j_password else ""
    data["data_dir"] = str(SETTINGS_FILE.parent)
    data.pop("password_hash", None)
    return data


def chat_config() -> dict:
    """返回当前聊天模型的连接配置。"""
    s = get_settings()
    if s.chat_provider == "deepseek":
        base = s.chat_base_url or env.deepseek_base_url
        key = s.chat_api_key or env.deepseek_api_key
        model = s.chat_model or env.deepseek_model
        extra = {"thinking": {"type": "enabled" if s.chat_thinking else "disabled"}}
    elif s.chat_provider == "ollama":
        base = s.chat_base_url or "http://localhost:11434/v1"
        key = s.chat_api_key or "ollama"
        model = s.chat_model or "qwen2.5:7b"
        extra = {}
    elif s.chat_provider == "lmstudio":
        base = s.chat_base_url or "http://localhost:1234/v1"
        key = s.chat_api_key or "lm-studio"
        model = s.chat_model or "local-model"
        extra = {}
    else:
        base = s.chat_base_url or env.deepseek_base_url
        key = s.chat_api_key or env.deepseek_api_key
        model = s.chat_model or env.deepseek_model
        extra = {}
    return {
        "provider": s.chat_provider,
        "base_url": base,
        "api_key": key,
        "model": model,
        "extra_body": extra,
        "context_window": s.chat_context_window,
    }


def _normalize_local_url(url: str) -> str:
    """Windows 下 localhost 可能解析到异常的 IPv6 Ollama 实例，统一走 IPv4。"""
    if not url:
        return url
    return url.replace("://localhost:", "://127.0.0.1:")


def embedding_config() -> dict:
    """返回当前向量化配置。"""
    s = get_settings()
    return {
        "provider": s.embedding_provider,
        "model": s.embedding_model,
        "base_url": _normalize_local_url(s.embedding_base_url),
        "api_key": s.embedding_api_key,
    }
