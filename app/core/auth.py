"""认证服务：JWT、密码哈希、用户管理"""
from __future__ import annotations
from datetime import datetime, timedelta, UTC
from jose import jwt, JWTError
import bcrypt
import secrets
from pathlib import Path
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

security = HTTPBearer(auto_error=False)

DEFAULT_JWT_SECRET = "change-me-in-production-use-a-strong-random-key"


def _load_jwt_secret() -> str:
    if settings.jwt_secret_key and settings.jwt_secret_key != DEFAULT_JWT_SECRET:
        return settings.jwt_secret_key
    path = Path(settings.data_dir) / "secret.key"
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        key = secrets.token_urlsafe(48)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        return key
    except Exception:
        return "insecure-fallback-do-not-use"


JWT_SECRET = _load_jwt_secret()


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _load_preset_users() -> dict[str, dict]:
    """从配置加载预设用户，哈希密码。"""
    users = {}
    for pair in settings.preset_users.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        parts = pair.split(":")
        u = parts[0].strip()
        p = parts[1].strip()
        users[u] = {"password": _hash_password(p)}
    return users


# 模块级缓存用户（导入时哈希一次）
_preset_users: dict[str, dict] = _load_preset_users()


def create_access_token(username: str, user_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[settings.jwt_algorithm])
        return {
            "user_id": payload["sub"],
            "username": payload["username"],
        }
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """FastAPI 依赖：从 Bearer 令牌解析用户，缺失时返回默认用户。"""
    if credentials is None:
        return {"user_id": "default", "username": "anonymous"}
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[settings.jwt_algorithm])
        return {
            "user_id": payload["sub"],
            "username": payload["username"],
        }
    except JWTError:
        raise HTTPException(401, "无效的认证令牌")


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """类似 get_current_user，但不会抛异常，失败时回退到默认用户。"""
    if credentials is None:
        return {"user_id": "default", "username": "anonymous"}
    decoded = decode_token(credentials.credentials)
    if decoded:
        return decoded
    return {"user_id": "default", "username": "anonymous"}


def authenticate_user(username: str, password: str) -> dict | None:
    """校验预设用户。返回用户信息字典或 None。"""
    if username not in _preset_users:
        return None
    if not _verify_password(password, _preset_users[username]["password"]):
        return None
    return {
        "user_id": username,
        "username": username,
    }
