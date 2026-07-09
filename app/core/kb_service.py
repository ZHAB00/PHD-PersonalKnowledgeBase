"""Knowledge Base management service"""
from __future__ import annotations
import uuid
import logging
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from app.core import cache
from app.models.kb import KnowledgeBase, KnowledgeBaseCreate
from app.config import settings

logger = logging.getLogger(__name__)

KB_LIST_KEY = "kb:list"
KB_KEY_PREFIX = "kb:"

# Default KB created at startup
DEFAULT_KB_ID = "default"


async def get_kb_list() -> list[KnowledgeBase]:
    """Get all knowledge bases with real-time doc counts from Qdrant."""
    data = await cache.get_json(KB_LIST_KEY)
    if not data:
        await _create_default_kb()
        data = await cache.get_json(KB_LIST_KEY) or []
    kbs = []
    for item in data:
        try:
            kb = KnowledgeBase(**item)
            # Refresh doc count from Qdrant
            await update_kb_doc_count(kb.id)
            refreshed = await get_kb(kb.id)
            if refreshed:
                kbs.append(refreshed)
            else:
                kbs.append(kb)
        except Exception:
            pass
    return kbs


async def get_kb(kb_id: str) -> Optional[KnowledgeBase]:
    """Get a single knowledge base by ID."""
    raw = await cache.get_json(f"{KB_KEY_PREFIX}{kb_id}")
    if raw:
        return KnowledgeBase(**raw)
    return None


async def create_kb(data: KnowledgeBaseCreate) -> KnowledgeBase:
    """Create a new knowledge base."""
    kb_id = str(uuid.uuid4())[:12]
    kb = KnowledgeBase(id=kb_id, name=data.name, description=data.description)

    # Persist to cache
    await cache.set_json(f"{KB_KEY_PREFIX}{kb_id}", kb.model_dump(mode="json"))
    kbs = await _load_kb_list_raw()
    kbs.append(kb.model_dump(mode="json"))
    await cache.set_json(KB_LIST_KEY, kbs)

    # Create file storage directory
    (Path(settings.data_dir) / "documents" / kb_id).mkdir(parents=True, exist_ok=True)

    logger.info(f"Created KB: {kb_id} ({data.name})")
    return kb


async def delete_kb(kb_id: str) -> bool:
    """Delete a knowledge base and all its data."""
    if kb_id == DEFAULT_KB_ID:
        raise ValueError("Cannot delete default knowledge base")

    kb = await get_kb(kb_id)
    if not kb:
        return False

    # Delete from Qdrant
    from app.core import vector_store
    try:
        vector_store.delete_collection(kb_id)
    except Exception as e:
        logger.warning(f"Failed to delete Qdrant collection for {kb_id}: {e}")

    # Delete Redis keys
    await cache.delete(f"{KB_KEY_PREFIX}{kb_id}")
    # Delete all doc task keys
    doc_keys = await cache.keys(f"kb:{kb_id}:doc:*")
    for k in doc_keys:
        await cache.delete(k)

    # Remove from list
    kbs = await _load_kb_list_raw()
    kbs = [k for k in kbs if k.get("id") != kb_id]
    await cache.set_json(KB_LIST_KEY, kbs)

    # Delete file directory
    import shutil
    kb_dir = Path(settings.data_dir) / "documents" / kb_id
    if kb_dir.exists():
        shutil.rmtree(kb_dir, ignore_errors=True)

    logger.info(f"Deleted KB: {kb_id}")
    return True


async def update_kb(kb_id: str, data) -> Optional[KnowledgeBase]:
    """Update a knowledge base's name and/or description."""
    from app.models.kb import KnowledgeBaseUpdate
    kb = await get_kb(kb_id)
    if not kb:
        return None

    if data.name is not None:
        kbs = await _load_kb_list_raw()
        for k in kbs:
            if k["name"] == data.name and k["id"] != kb_id:
                raise ValueError(f"知识库名称已存在: {data.name}")
        kb.name = data.name
    if data.description is not None:
        kb.description = data.description

    kb.updated_at = datetime.now(UTC)
    await cache.set_json(f"{KB_KEY_PREFIX}{kb_id}", kb.model_dump(mode="json"))

    kbs = await _load_kb_list_raw()
    for i, k in enumerate(kbs):
        if k["id"] == kb_id:
            kbs[i] = kb.model_dump(mode="json")
            break
    await cache.set_json(KB_LIST_KEY, kbs)

    logger.info(f"Updated KB: {kb_id}")
    return kb

async def update_kb_doc_count(kb_id: str):
    """Refresh kb doc count from Qdrant (real-time, no Redis dependency)."""
    try:
        from app.core import vector_store
        from qdrant_client import QdrantClient, models
        from app.config import settings as s
        client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, check_compatibility=False)
        col_name = f"kb_{kb_id}"
        # Count unique doc_ids in the collection
        result = client.scroll(
            collection_name=col_name, limit=10000, with_payload=True, with_vectors=False
        )
        doc_ids = set()
        for point in result[0]:
            did = point.payload.get("doc_id", "")
            if did and not did.startswith("parent_"):  # exclude parent chunks
                doc_ids.add(did)
        doc_count = len(doc_ids)
    except Exception:
        doc_count = 0

    kb = await get_kb(kb_id)
    if kb:
        kb.doc_count = doc_count
        kb.updated_at = datetime.now(UTC)
        await cache.set_json(f"{KB_KEY_PREFIX}{kb_id}", kb.model_dump(mode="json"))


async def _create_default_kb():
    kb = KnowledgeBase(id=DEFAULT_KB_ID, name="默认知识库", description="系统默认知识库")
    await cache.set_json(f"{KB_KEY_PREFIX}{DEFAULT_KB_ID}", kb.model_dump(mode="json"))
    await cache.set_json(KB_LIST_KEY, [kb.model_dump(mode="json")])
    (Path(settings.data_dir) / "documents" / DEFAULT_KB_ID).mkdir(parents=True, exist_ok=True)
    logger.info("Default KB created")


async def _load_kb_list_raw() -> list[dict]:
    data = await cache.get_json(KB_LIST_KEY)
    return data if data else []


def get_doc_dir(kb_id: str) -> Path:
    return Path(settings.data_dir) / "documents" / kb_id


