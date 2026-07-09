from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    """单条对话消息"""
    role: str  # user / assistant / system
    content: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ToolCallEvent(BaseModel):
    """单次工具调用事件，用于前端展示"""
    tool_name: str
    arguments: dict = Field(default_factory=dict)
    result: str = ""  # truncated result for display
    status: str = "ok"  # ok / error


class ChatRequest(BaseModel):
    """对话请求"""
    session_id: str = "default"
    message: str
    kb_id: str = "default"
    tenant_id: str = "default"
    user_id: str = "default"
    top_k: int = 5
    stream: bool = False
    rerank_strategy: str = "none"  # "mmr" | "cross_encoder" | "none"


class SourceReference(BaseModel):
    """检索来源引用"""
    doc_id: str
    filename: str
    page_number: Optional[int] = None
    chunk_index: int = 0
    content: str
    score: float = 0.0
    is_table: bool = False
    table_html: Optional[str] = None


class ChatResponse(BaseModel):
    """对话响应"""
    session_id: str
    answer: str
    sources: list[SourceReference] = Field(default_factory=list)
    tool_calls: list[ToolCallEvent] = Field(default_factory=list)
    token_usage: Optional[dict] = None


class ChatHistoryItem(BaseModel):
    """带来源的历史记录"""
    user_message: str
    assistant_message: str
    sources: list[SourceReference] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=datetime.utcnow)
