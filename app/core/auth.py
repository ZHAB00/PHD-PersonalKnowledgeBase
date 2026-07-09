"""Auth service: JWT, password hashing, user management"""
from __future__ import annotations
from datetime import datetime, timedelta, UTC
from jose import jwt, JWTError
import bcrypt
from fastapi import HTTPException, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from app.config import settings

security = HTTPBearer(auto_error=False)


def _hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _verify_password(plain: str, hashed: str) -> bool:
    return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def _load_preset_users() -> dict[str, str]:
    """Load preset users from config, hash passwords."""
    users = {}
    for pair in settings.preset_users.split(","):
        pair = pair.strip()
        if ":" not in pair:
            continue
        u, p = pair.split(":", 1)
        users[u.strip()] = _hash_password(p.strip())
    return users


# Module-level cached users (hashed once at import)
_preset_users: dict[str, str] = _load_preset_users()


def create_access_token(username: str, user_id: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": user_id,
        "username": username,
        "exp": expire,
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return {"user_id": payload["sub"], "username": payload["username"]}
    except JWTError:
        return None


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """FastAPI dependency: extract user from Bearer token, or return default."""
    if credentials is None:
        return {"user_id": "default", "username": "anonymous"}
    token = credentials.credentials
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
        return {"user_id": payload["sub"], "username": payload["username"]}
    except JWTError:
        raise HTTPException(401, "无效的认证令牌")


async def get_optional_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
) -> dict:
    """Like get_current_user but never raises - falls back to default."""
    if credentials is None:
        return {"user_id": "default", "username": "anonymous"}
    decoded = decode_token(credentials.credentials)
    if decoded:
        return decoded
    return {"user_id": "default", "username": "anonymous"}


def authenticate_user(username: str, password: str) -> dict | None:
    """Check preset users. Returns user info dict or None."""
    if username not in _preset_users:
        return None
    if not _verify_password(password, _preset_users[username]):
        return None
    return {"user_id": username, "username": username}
