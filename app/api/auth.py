"""Auth API: login, token refresh"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Depends

from app.models.auth import LoginRequest, TokenResponse
from app.core.auth import authenticate_user, create_access_token, get_current_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(req: LoginRequest):
    """Login with preset credentials, get JWT token."""
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
    """Get current user info from token."""
    return {"user_id": user["user_id"], "username": user["username"]}
