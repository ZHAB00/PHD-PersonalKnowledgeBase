"""Knowledge Base model"""
from __future__ import annotations
from datetime import datetime, UTC
from typing import Optional
from pydantic import BaseModel, Field


class KnowledgeBaseCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="知识库名称")
    description: str = Field(default="", max_length=256)


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64, description="知识库名称")
    description: Optional[str] = Field(None, max_length=256)


class KnowledgeBase(BaseModel):
    id: str
    name: str
    description: str = ""
    doc_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
