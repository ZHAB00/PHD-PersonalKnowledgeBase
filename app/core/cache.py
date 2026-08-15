"""Redis 客户端封装（兼容 Redis 3.x+，不可用时回退内存）"""
from __future__ import annotations
import json
import logging
from typing import Optional

from app.config import settings

logger = logging.getLogger(__name__)

try:
    import redis.asyncio as redis
    _REDIS_AVAILABLE = True
except ImportError:
    _REDIS_AVAILABLE = False

_pool = None
_client = None
_enabled = False
_fallback_store: dict[str, str] = {}


def _disable_redis(reason: str):
    """Redis 运行中异常时降级到内存存储，避免接口 500。"""
    global _enabled
    if _enabled:
        _enabled = False
        logger.warning(f"Redis 不可用，切换到内存存储: {reason}")


async def _init():
    global _pool, _enabled
    if not _REDIS_AVAILABLE:
        logger.warning("redis 库未安装，使用内存存储")
        return
    try:
        _pool = redis.ConnectionPool.from_url(
            settings.redis_url,
            decode_responses=True,
            protocol=2,
        )
        global _client
        _client = redis.Redis(connection_pool=_pool)
        await _client.ping()
        _enabled = True
        logger.info("Redis 连接就绪")
    except Exception as e:
        _pool = None
        _enabled = False
        logger.warning(f"Redis 连接失败，使用内存存储: {e}")


async def get_redis():
    if _enabled and _pool:
        return redis.Redis(connection_pool=_pool)
    raise RuntimeError("Redis not available")


async def set(key: str, value: str, ex: int | None = None):
    """写入原始字符串值。"""
    if _enabled and _client:
        try:
            await _client.set(key, value, ex=ex)
            return
        except Exception as e:
            _disable_redis(str(e))
    _fallback_store[key] = value


async def get(key: str) -> Optional[str]:
    """读取原始字符串值。"""
    if _enabled and _client:
        try:
            return await _client.get(key)
        except Exception as e:
            _disable_redis(str(e))
    return _fallback_store.get(key)


async def set_json(key: str, value, ex: int | None = None):
    if _enabled and _client:
        try:
            await _client.set(key, json.dumps(value, ensure_ascii=False, default=str), ex=ex)
            return
        except Exception as e:
            _disable_redis(str(e))
    _fallback_store[key] = json.dumps(value, ensure_ascii=False, default=str)


async def get_json(key: str) -> Optional[dict | list]:
    if _enabled and _client:
        try:
            raw = await _client.get(key)
            if raw:
                return json.loads(raw)
            return None
        except Exception as e:
            _disable_redis(str(e))
    raw = _fallback_store.get(key)
    if raw:
        return json.loads(raw)
    return None


async def delete(key: str):
    if _enabled and _client:
        try:
            await _client.delete(key)
            return
        except Exception as e:
            _disable_redis(str(e))
    _fallback_store.pop(key, None)


async def keys(pattern: str) -> list[str]:
    if _enabled and _client:
        try:
            return await _client.keys(pattern)
        except Exception as e:
            _disable_redis(str(e))
    return [k for k in _fallback_store if k.startswith(pattern.split(":")[0])]


async def close():
    global _pool, _client
    if _client:
        await _client.aclose()
        _client = None
    if _pool:
        await _pool.disconnect()
        _pool = None
