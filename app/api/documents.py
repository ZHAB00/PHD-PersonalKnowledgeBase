from __future__ import annotations
import shutil
from pathlib import Path
from fastapi import APIRouter, UploadFile, File, Form, HTTPException, Query, Request
from app.models.document import DocumentUploadResponse, DocumentTaskStatus, DocumentStatus, DocumentInfo, DocumentType
from app.workers.ingestion import ingest_document
from app.core.kb_service import get_doc_dir
from app.workers.ingestion import get_file_extension as _get_ext_from_name

router = APIRouter(prefix="/api/documents", tags=["documents"])


@router.post("/upload", response_model=DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form("default"),
    tenant_id: str = Form("default"),
):
    if not file.filename:
        raise HTTPException(400, "文件名不能为空")

    ext = Path(file.filename).suffix.lower()
    if ext not in (".pdf", ".docx", ".md", ".txt"):
        raise HTTPException(400, f"不支持的格式: {ext}")

    data_dir = get_doc_dir(kb_id)
    data_dir.mkdir(parents=True, exist_ok=True)
    save_path = data_dir / file.filename

    counter = 1
    while save_path.exists():
        stem = Path(file.filename).stem
        save_path = data_dir / f"{stem}_{counter}{ext}"
        counter += 1

    with open(save_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    result = await ingest_document(save_path, save_path.name, kb_id=kb_id, tenant_id=tenant_id)
    return result


@router.get("/status/{task_id}", response_model=DocumentTaskStatus)
async def get_task_status(task_id: str, kb_id: str = Query("default")):
    from app.workers.ingestion import get_task_info
    info = await get_task_info(kb_id, task_id)
    if not info:
        raise HTTPException(404, "任务不存在")
    return DocumentTaskStatus(
        task_id=task_id, status=info.status,
        progress=f"{info.total_chunks} 个分块" if info.status == DocumentStatus.READY else "",
        doc_info=info,
    )


@router.get("/list")
async def list_documents(kb_id: str = Query("default"), tenant_id: str = Query("default")):
    """List documents in KB, including orphan files on disk not yet tracked."""
    from app.workers.ingestion import list_tasks
    tasks = await list_tasks(kb_id=kb_id, tenant_id=tenant_id)

    # Get chunk counts from Qdrant for each doc to fill in missing data
    chunk_counts = await _get_chunk_counts_from_qdrant(kb_id)

    task_filenames = {t.filename for t in tasks}
    data_dir = get_doc_dir(kb_id)
    orphans = []
    if data_dir.exists():
        for f in data_dir.iterdir():
            if f.is_file() and f.name not in task_filenames and f.suffix.lower() in (".pdf", ".docx", ".md", ".txt"):
                # Try to get real chunk count from Qdrant
                qdrant_chunks = chunk_counts.get(f.name, 0)
                orphans.append(DocumentInfo(
                    id=f"orphan_{f.name}",
                    filename=f.name,
                    doc_type=DocumentType(_get_ext_from_name(f.name)),
                    status=DocumentStatus.PENDING,
                    total_chunks=qdrant_chunks,
                    total_pages=0,
                    kb_id=kb_id,
                ))

    # Also update tasks that have 0 chunks with Qdrant counts
    enriched_tasks = []
    for t in tasks:
        d = t.model_dump(mode="json")
        if d["total_chunks"] == 0 and t.filename in chunk_counts:
            d["total_chunks"] = chunk_counts[t.filename]
        enriched_tasks.append(d)

    all_docs = enriched_tasks + [o.model_dump(mode="json") for o in orphans[:50]]  # cap orphans
    return {"documents": all_docs, "total": len(all_docs), "orphan_count": len(orphans)}


async def _get_chunk_counts_from_qdrant(kb_id: str) -> dict:
    """Get per-document chunk counts from Qdrant, cached in Redis for 30s."""
    from app.core import cache
    cache_key = f"kb:{kb_id}:chunk_counts"
    cached = await cache.get_json(cache_key)
    if cached is not None:
        return cached
    try:
        from qdrant_client import QdrantClient
        from app.config import settings as s
        client = QdrantClient(host=s.qdrant_host, port=s.qdrant_port, check_compatibility=False)
        col_name = f"kb_{kb_id}"
        result = client.scroll(
            collection_name=col_name, limit=10000,
            with_payload=["doc_id", "filename"], with_vectors=False
        )
        counts = {}
        for point in result[0]:
            doc_id = point.payload.get("doc_id", "")
            filename = point.payload.get("filename", "")
            if doc_id and not doc_id.startswith("parent_"):
                counts[filename] = counts.get(filename, 0) + 1
        await cache.set_json(cache_key, counts, ex=30)
        return counts
    except Exception:
        return {}


@router.delete("/{task_id}")
async def delete_document(task_id: str, kb_id: str = Query("default"), tenant_id: str = Query("default")):
    from app.workers.ingestion import get_task_info, delete_task
    from app.core import vector_store

    info = await get_task_info(kb_id, task_id)
    if info:
        # Delete from vector store
        try:
            vector_store.delete_document(task_id, kb_id, tenant_id)
        except Exception as e:
            raise HTTPException(500, f"向量库删除失败: {e}")

        # Delete file
        file_path = get_doc_dir(kb_id) / info.filename
        if file_path.exists():
            file_path.unlink()

        await delete_task(kb_id, task_id)
        asyncio.create_task(_cleanup_graph_for_doc(kb_id, info.filename))
        return {"status": "deleted", "task_id": task_id, "filename": info.filename}

    # Handle orphan deletion (no Redis record, just file)
    if task_id.startswith("orphan_"):
        filename = task_id[len("orphan_"):]
        file_path = get_doc_dir(kb_id) / filename
        if file_path.exists():
            file_path.unlink()
        asyncio.create_task(_cleanup_graph_for_doc(kb_id, filename))
        return {"status": "deleted", "task_id": task_id, "filename": filename}

    raise HTTPException(404, "任务不存在")


@router.post("/reindex/{filename}")
async def reindex_orphan(filename: str, kb_id: str = Query("default"), tenant_id: str = Query("default")):
    """Re-index an orphan or failed file on disk. Cleans up old records first."""
    file_path = get_doc_dir(kb_id) / filename
    if not file_path.exists():
        raise HTTPException(404, f"文件不存在: {filename}")

    # Delete old task records and vector data for this filename
    from app.workers.ingestion import list_tasks, delete_task
    from app.core import vector_store
    old_tasks = await list_tasks(kb_id=kb_id, tenant_id=tenant_id)
    for t in old_tasks:
        if t.filename == filename:
            try:
                vector_store.delete_document(t.id, kb_id, tenant_id)
            except Exception:
                pass
            await delete_task(kb_id, t.id)

    result = await ingest_document(file_path, filename, kb_id=kb_id, tenant_id=tenant_id)
    return result


async def _cleanup_graph_for_doc(kb_id: str, filename: str):
    """Remove chunks for deleted doc. Keep entities if referenced by other docs."""
    try:
        from app.rag.graph_rag import _get_driver
        from app.config import settings as s
        driver = _get_driver()
        with driver.session(database=s.neo4j_database) as session:
            # Find chunks belonging to this doc
            result = session.run(
                "MATCH (c:Chunk {kb_id: $kb}) WHERE c.doc_id IS NOT NULL AND c.text IS NOT NULL RETURN c.id AS cid, c.doc_id AS did",
                kb=kb_id
            )
            target_cids = set()
            for rec in result:
                did = rec["did"]
                # Try to find the doc by filename in chunk text or doc_id
                if _doc_matches(did, filename, rec.get("cid","")):
                    target_cids.add(rec["cid"])
            
            if not target_cids:
                return
            
            # Detach and delete only the target chunks
            for cid in target_cids:
                session.run(
                    "MATCH (c:Chunk {id: $cid}) DETACH DELETE c",
                    cid=cid
                )
            
            # Clean orphan entities (no MENTIONED_IN left)
            session.run(
                "MATCH (e:Entity {kb_id: $kb}) WHERE NOT (e)-[:MENTIONED_IN]->(:Chunk) DETACH DELETE e",
                kb=kb_id
            )
    except Exception:
        pass


def _doc_matches(doc_id: str, filename: str, chunk_id: str) -> bool:
    """Check if a chunk belongs to the given document."""
    fn_lower = filename.lower()
    did_lower = doc_id.lower()
    cid_lower = chunk_id.lower()
    return fn_lower in did_lower or fn_lower in cid_lower or did_lower.startswith(fn_lower.split(".")[0])

