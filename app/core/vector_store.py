"""Qdrant vector store with multi-collection KB isolation"""
from __future__ import annotations
import logging
from typing import Optional
from uuid import uuid4

from qdrant_client import QdrantClient
from qdrant_client.http import models
from qdrant_client.http.models import Distance, VectorParams

from app.config import settings
from app.models.document import DocumentChunk
from app.core.embedding import embedding_dimension

logger = logging.getLogger(__name__)

_client: QdrantClient | None = None
COLLECTION_PREFIX = "kb_"
DEFAULT_KB = "default"


def _get_client() -> QdrantClient:
    global _client
    if _client is None:
        _client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
    return _client


def _collection_name(kb_id: str) -> str:
    return f"{COLLECTION_PREFIX}{kb_id}"


def ensure_collection(kb_id: str = DEFAULT_KB):
    client = _get_client()
    col_name = _collection_name(kb_id)
    collections = client.get_collections().collections
    names = [c.name for c in collections]
    if col_name not in names:
        client.create_collection(
            collection_name=col_name,
            vectors_config=VectorParams(size=embedding_dimension(), distance=Distance.COSINE),
        )
        client.create_payload_index(collection_name=col_name, field_name="tenant_id", field_schema=models.PayloadSchemaType.KEYWORD)
        client.create_payload_index(collection_name=col_name, field_name="doc_id", field_schema=models.PayloadSchemaType.KEYWORD)
        logger.info(f"Created collection: {col_name}")


def delete_collection(kb_id: str):
    client = _get_client()
    col_name = _collection_name(kb_id)
    try:
        client.delete_collection(col_name)
        logger.info(f"Deleted collection: {col_name}")
    except Exception as e:
        logger.warning(f"Failed to delete collection {col_name}: {e}")


def upsert_chunks(chunks: list[DocumentChunk], kb_id: str = DEFAULT_KB) -> int:
    from app.core.embedding import embed_texts
    client = _get_client()
    col_name = _collection_name(kb_id)
    ensure_collection(kb_id)
    texts = [c.content for c in chunks]
    vectors = embed_texts(texts)
    points = []
    for i, (chunk, vector) in enumerate(zip(chunks, vectors)):
        chunk.embedding = vector
        points.append(models.PointStruct(
            id=str(uuid4()), vector=vector,
            payload={
                "doc_id": chunk.metadata.doc_id, "filename": chunk.metadata.filename,
                "page_number": chunk.metadata.page_number, "chunk_index": chunk.metadata.chunk_index,
                "tenant_id": chunk.metadata.tenant_id, "content": chunk.content,
                "is_table": chunk.metadata.is_table, "table_html": chunk.metadata.table_html or "",
                "kb_id": kb_id,
            },
        ))
    client.upsert(collection_name=col_name, points=points)
    return len(points)


def search(query_vector: list[float], top_k: int = 5, kb_id: str = DEFAULT_KB, tenant_id: Optional[str] = None, doc_id: Optional[str] = None) -> list[dict]:
    client = _get_client()
    col_name = _collection_name(kb_id)
    must_filters = []
    if tenant_id:
        must_filters.append(models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)))
    if doc_id:
        must_filters.append(models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id)))
    filter_cond = models.Filter(must=must_filters) if must_filters else None
    try:
        result = client.query_points(
            collection_name=col_name, query=query_vector,
            limit=top_k, query_filter=filter_cond, with_payload=True, score_threshold=0.3,
        )
        return sorted([
            {
                "doc_id": r.payload.get("doc_id"), "filename": r.payload.get("filename"),
                "page_number": r.payload.get("page_number"), "chunk_index": r.payload.get("chunk_index"),
                "content": r.payload.get("content"), "is_table": r.payload.get("is_table", False),
                "table_html": r.payload.get("table_html", ""), "tenant_id": r.payload.get("tenant_id"),
                  "parent_id": r.payload.get("parent_id", ""),
                "score": r.score, "kb_id": kb_id,
            }
            for r in result.points
        ], key=lambda x: x["score"], reverse=True)
    except Exception:
        return []


def delete_document(doc_id: str, kb_id: str = DEFAULT_KB, tenant_id: Optional[str] = None):
    client = _get_client()
    col_name = _collection_name(kb_id)
    must_filters = [models.FieldCondition(key="doc_id", match=models.MatchValue(value=doc_id))]
    if tenant_id:
        must_filters.append(models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)))
    try:
        client.delete(collection_name=col_name, points_selector=models.FilterSelector(filter=models.Filter(must=must_filters)))
    except Exception:
        pass


def count_documents(kb_id: str = DEFAULT_KB, tenant_id: Optional[str] = None) -> int:
    client = _get_client()
    col_name = _collection_name(kb_id)
    must_filters = []
    if tenant_id:
        must_filters.append(models.FieldCondition(key="tenant_id", match=models.MatchValue(value=tenant_id)))
    filter_cond = models.Filter(must=must_filters) if must_filters else None
    try:
        return client.count(collection_name=col_name, count_filter=filter_cond).count
    except Exception:
        return 0


def list_all_chunks(kb_id: str = DEFAULT_KB) -> list[dict]:
    """Get all chunks for BM25 rebuild (limit 10000)."""
    client = _get_client()
    col_name = _collection_name(kb_id)
    try:
        result = client.scroll(collection_name=col_name, limit=10000, with_payload=True, with_vectors=False)
        return [
            {
                "doc_id": r.payload.get("doc_id"), "content": r.payload.get("content"),
                "filename": r.payload.get("filename"), "page_number": r.payload.get("page_number"),
            }
            for r in result[0]
        ]
    except Exception:
        return []
