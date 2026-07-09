from __future__ import annotations
from datetime import datetime, UTC
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field


class DocumentStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class DocumentType(str, Enum):
    PDF = "pdf"
    DOCX = "docx"
    MD = "md"
    TXT = "txt"


class ChunkMetadata(BaseModel):
    doc_id: str
    filename: str
    page_number: Optional[int] = None
    chunk_index: int = 0
    is_table: bool = False
    table_html: Optional[str] = None
    tenant_id: str = "default"
    kb_id: str = "default"
    parent_id: Optional[str] = None


class DocumentChunk(BaseModel):
    content: str
    metadata: ChunkMetadata
    embedding: Optional[list[float]] = None


class DocumentInfo(BaseModel):
    id: str
    filename: str
    doc_type: DocumentType
    status: DocumentStatus
    total_chunks: int = 0
    total_pages: int = 0
    error_message: Optional[str] = None
    tenant_id: str = "default"
    kb_id: str = "default"
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class DocumentUploadResponse(BaseModel):
    task_id: str
    filename: str
    status: DocumentStatus
    message: str


class DocumentTaskStatus(BaseModel):
    task_id: str
    status: DocumentStatus
    progress: str = ""
    doc_info: Optional[DocumentInfo] = None


class ParseResult(BaseModel):
    content: str
    tables: list[dict] = Field(default_factory=list)
    pages: int = 0
    metadata: dict = Field(default_factory=dict)