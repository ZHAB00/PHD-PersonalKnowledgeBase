"""认证模型"""
from __future__ import annotations
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    user_id: str


class UserInfo(BaseModel):
    user_id: str
    username: str
