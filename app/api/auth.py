"""认证 API：登录、获取当前用户"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Depends, Request

from app.models.auth import LoginRequest, TokenResponse
from app.core.auth import authenticate_user, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """使用预设账号登录，获取 JWT 令牌。"""
    if not req.username or not req.password:
        raise HTTPException(400, "用户名和密码不能为空")
    user = authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(401, "用户名或密码错误")
    token = create_access_token(user["username"], user["user_id"])
    return TokenResponse(
        access_token=token,
        username=user["username"],
        user_id=user["user_id"],
    )


@router.get("/me")
async def me(user: dict = Depends(get_current_user)):
    """从令牌获取当前用户信息。"""
    return {
        "user_id": user["user_id"],
        "username": user["username"],
    }


@router.get("/local-token")
async def local_token(request: Request):
    """无密码模式下获取本地令牌。"""
    from app.core.user_settings import get_settings
    host = request.client.host if request.client else "127.0.0.1"
    if host not in ("127.0.0.1", "::1", "localhost"):
        raise HTTPException(403, "仅本机可获取本地令牌")
    s = get_settings()
    if s.require_password:
        raise HTTPException(403, "访问密码已开启")
    token = create_access_token("local", "local")
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": "local",
        "user_id": "local",
    }


@router.post("/login-local")
async def login_local(data: dict):
    """访问密码模式：校验单密码并发放本地令牌。"""
    from app.core.user_settings import get_settings
    from app.core.auth import _verify_password
    s = get_settings()
    if not s.require_password:
        token = create_access_token("local", "local")
    elif not s.password_hash or not _verify_password(data.get("password", ""), s.password_hash):
        raise HTTPException(401, "密码错误")
    else:
        token = create_access_token("local", "local")
    return {
        "access_token": token,
        "token_type": "bearer",
        "username": "local",
        "user_id": "local",
    }
