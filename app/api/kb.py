"""知识库增删改查 API"""
from __future__ import annotations
from fastapi import APIRouter, HTTPException, Query

from app.models.kb import KnowledgeBase, KnowledgeBaseCreate, KnowledgeBaseUpdate
from app.core.kb_service import get_kb_list, get_kb, create_kb, delete_kb, update_kb

router = APIRouter(prefix="/api/kb", tags=["knowledge_base"])


@router.get("/list", response_model=list[KnowledgeBase])
async def list_kbs():
    """列出所有知识库。"""
    return await get_kb_list()


@router.get("/{kb_id}", response_model=KnowledgeBase)
async def get_kb_detail(kb_id: str):
    """获取知识库详情。"""
    kb = await get_kb(kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    return kb


@router.post("/create", response_model=KnowledgeBase)
async def create_kb_endpoint(data: KnowledgeBaseCreate):
    """创建新知识库。"""
    existing = await get_kb_list()
    for kb in existing:
        if kb.name == data.name:
            raise HTTPException(400, f"知识库名称已存在: {data.name}")
    return await create_kb(data)


@router.put("/{kb_id}", response_model=KnowledgeBase)
async def update_kb_endpoint(kb_id: str, data: KnowledgeBaseUpdate):
    """更新知识库名称或描述。"""
    try:
        kb = await update_kb(kb_id, data)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        return kb
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.delete("/{kb_id}")
async def delete_kb_endpoint(kb_id: str, confirmation: str = Query(..., description="输入 A1B2C3D4 确认删除")):
    """删除知识库，需要输入确认码 A1B2C3D4。"""
    if confirmation != "A1B2C3D4":
        raise HTTPException(400, "删除确认码错误，请输入 A1B2C3D4 确认删除")
    try:
        ok = await delete_kb(kb_id)
        if not ok:
            raise HTTPException(404, "知识库不存在")
        return {"status": "deleted", "kb_id": kb_id}
    except ValueError as e:
        raise HTTPException(400, str(e))
