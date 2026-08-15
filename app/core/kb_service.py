"""知识库管理服务"""
from __future__ import annotations
import json
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

# 启动时创建的默认知识库
DEFAULT_KB_ID = "default"


def _kb_file() -> Path:
    return Path(settings.data_dir) / "kbs.json"


async def _save_kb_file(kbs: list[dict]):
    try:
        _kb_file().parent.mkdir(parents=True, exist_ok=True)
        _kb_file().write_text(json.dumps(kbs, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to persist KB list to file: {e}")


async def _load_kb_list_raw() -> list[dict]:
    try:
        data = await cache.get_json(KB_LIST_KEY)
    except Exception:
        data = None
    if data:
        return data
    try:
        if _kb_file().exists():
            data = json.loads(_kb_file().read_text(encoding="utf-8"))
            if data:
                await cache.set_json(KB_LIST_KEY, data)
                for item in data:
                    await cache.set_json(f"{KB_KEY_PREFIX}{item.get('id', '')}", item)
                return data
    except Exception:
        pass
    return []


async def get_kb_list() -> list[KnowledgeBase]:
    """获取所有知识库，并实时从 Qdrant 刷新文档数。"""
    data = await _load_kb_list_raw()
    if not data:
        await _create_default_kb()
        data = await _load_kb_list_raw() or []
    kbs = []
    for item in data:
        try:
            kb = KnowledgeBase(**item)
            # 从 Qdrant 刷新文档数
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
    """按 ID 获取单个知识库。"""
    try:
        raw = await cache.get_json(f"{KB_KEY_PREFIX}{kb_id}")
    except Exception:
        raw = None
    if raw:
        return KnowledgeBase(**raw)
    try:
        if _kb_file().exists():
            data = json.loads(_kb_file().read_text(encoding="utf-8"))
            for item in data:
                if item.get("id") == kb_id:
                    await cache.set_json(f"{KB_KEY_PREFIX}{kb_id}", item)
                    return KnowledgeBase(**item)
    except Exception:
        pass
    return None


async def create_kb(data: KnowledgeBaseCreate) -> KnowledgeBase:
    """创建新知识库。"""
    kb_id = str(uuid.uuid4())[:12]
    kb = KnowledgeBase(id=kb_id, name=data.name, description=data.description)

    # 写入缓存
    await cache.set_json(f"{KB_KEY_PREFIX}{kb_id}", kb.model_dump(mode="json"))
    kbs = await _load_kb_list_raw()
    kbs.append(kb.model_dump(mode="json"))
    await cache.set_json(KB_LIST_KEY, kbs)
    await _save_kb_file(kbs)

    # 创建文件存储目录
    (Path(settings.data_dir) / "documents" / kb_id).mkdir(parents=True, exist_ok=True)

    logger.info(f"Created KB: {kb_id} ({data.name})")
    return kb


async def delete_kb(kb_id: str) -> bool:
    """删除知识库及其全部数据。"""
    kb = await get_kb(kb_id)
    if not kb:
        return False

    kbs = await _load_kb_list_raw()
    if len(kbs) <= 1:
        raise ValueError("至少保留一个知识库")

    # 从 Qdrant 删除
    from app.core import vector_store
    try:
        vector_store.delete_collection(kb_id)
    except Exception as e:
        logger.warning(f"Failed to delete Qdrant collection for {kb_id}: {e}")

    # 删除 Redis 键
    await cache.delete(f"{KB_KEY_PREFIX}{kb_id}")
    # 删除所有文档任务键
    doc_keys = await cache.keys(f"kb:{kb_id}:doc:*")
    for k in doc_keys:
        await cache.delete(k)

    # 从列表中移除
    kbs = [k for k in kbs if k.get("id") != kb_id]
    await cache.set_json(KB_LIST_KEY, kbs)
    await _save_kb_file(kbs)

    # 删除文件目录
    import shutil
    kb_dir = Path(settings.data_dir) / "documents" / kb_id
    if kb_dir.exists():
        shutil.rmtree(kb_dir, ignore_errors=True)

    logger.info(f"Deleted KB: {kb_id}")
    return True


async def update_kb(kb_id: str, data) -> Optional[KnowledgeBase]:
    """更新知识库名称或描述。"""
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
    await _save_kb_file(kbs)

    logger.info(f"Updated KB: {kb_id}")
    return kb

async def update_kb_doc_count(kb_id: str):
    """根据磁盘上的实际文件刷新知识库文档数。"""
    from pathlib import Path
    doc_count = 0
    try:
        doc_dir = get_doc_dir(kb_id)
        valid_exts = {".pdf", ".docx", ".md", ".txt"}
        if doc_dir.exists():
            doc_count = sum(1 for f in doc_dir.iterdir() if f.is_file() and f.suffix.lower() in valid_exts)
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
    await _save_kb_file([kb.model_dump(mode="json")])
    (Path(settings.data_dir) / "documents" / DEFAULT_KB_ID).mkdir(parents=True, exist_ok=True)
    logger.info("Default KB created")


def get_doc_dir(kb_id: str) -> Path:
    return Path(settings.data_dir) / "documents" / kb_id


