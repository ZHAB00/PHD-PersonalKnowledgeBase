from __future__ import annotations
import asyncio
import logging
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path
from typing import Optional

from app.config import settings
from app.core.document_parser import parse_document
from app.core.chunker import build_chunks
from app.core import vector_store
from app.core import cache
from app.rag.graph_rag import ingest_chunk_to_graph
from app.models.document import DocumentInfo, DocumentStatus, DocumentType, DocumentUploadResponse

logger = logging.getLogger(__name__)

TASK_TTL = 86400  # 临时任务保留 1 天
READY_TTL = 2592000  # 已完成文档保留 30 天
MAX_CONCURRENT = 2       # 最大并发向量化任务（Ollama 限制）
RETRY_COUNT = 1          # 向量化失败重试次数
RETRY_DELAY = 2.0        # 重试间隔秒数

# 信号量：限制并发文档处理
_semaphore = asyncio.Semaphore(MAX_CONCURRENT)


def _task_key(kb_id: str, task_id: str) -> str:
    return f"kb:{kb_id}:doc:{task_id}"


async def ingest_document(filepath: str | Path, filename: str, kb_id: str = "default", tenant_id: str = "default") -> DocumentUploadResponse:
    task_id = str(uuid.uuid4())
    doc_info = DocumentInfo(
        id=task_id, filename=filename,
        doc_type=DocumentType(get_file_extension(filename)),
        status=DocumentStatus.PROCESSING, tenant_id=tenant_id, kb_id=kb_id,
    )
    await cache.set_json(_task_key(kb_id, task_id), doc_info.model_dump(mode="json"), ex=TASK_TTL)
    asyncio.create_task(_process_task(task_id, filepath, filename, kb_id, tenant_id))
    return DocumentUploadResponse(task_id=task_id, filename=filename, status=DocumentStatus.PROCESSING, message="Document submitted")


async def _process_task(task_id: str, filepath: str | Path, filename: str, kb_id: str, tenant_id: str):
    """处理单个文档：解析 -> 分块 -> 向量化 -> 写入，带并发控制和重试。"""
    async with _semaphore:
        loop = asyncio.get_running_loop()
        raw = await cache.get_json(_task_key(kb_id, task_id))
        if not raw:
            return
        doc_info = DocumentInfo(**raw)
        try:
            doc_info.error_message = ""
            t0 = time.perf_counter()
            # 步骤 1：解析文档（CPU 密集型，在线程池中执行）
            parse_result = await loop.run_in_executor(None, parse_document, filepath)
            doc_info.total_pages = parse_result.pages
            t_parse = time.perf_counter()

            # 步骤 2：分块
            chunks = build_chunks(parse_result=parse_result, doc_id=task_id, filename=filename, tenant_id=tenant_id)
            for c in chunks:
                c.metadata.kb_id = kb_id
            doc_info.total_chunks = len(chunks)
            t_chunk = time.perf_counter()

            # 步骤 2.5：GraphRAG 实体抽取（异步后台执行，尽力而为）
            asyncio.create_task(_ingest_graph_async(chunks, kb_id, task_id, filename))

            # 步骤 3：向量化并写入，失败自动重试
            count = await _upsert_with_retry(chunks, kb_id, filename)
            t_upsert = time.perf_counter()
            doc_info.status = DocumentStatus.READY
            await cache.delete(f"kb:{kb_id}:chunk_counts")  # 使分块计数缓存失效
            doc_info.updated_at = datetime.now(UTC)
            # 已完成文档保留 30 天，避免周末重启后状态丢失
            await cache.set_json(_task_key(kb_id, task_id), doc_info.model_dump(mode="json"), ex=READY_TTL)
            logger.info(
                f"Doc {filename}: {count} chunks -> kb:{kb_id} "
                f"parse={t_parse-t0:.2f}s chunk={t_chunk-t_parse:.2f}s upsert={t_upsert-t_chunk:.2f}s total={t_upsert-t0:.2f}s"
            )

            # 更新知识库文档数
            from app.core.kb_service import update_kb_doc_count
            await update_kb_doc_count(kb_id)

            # 重建 BM25 索引
            asyncio.create_task(_rebuild_bm25_async(kb_id))

        except Exception as e:
            logger.error(f"Doc processing failed {filename}: {e}")
            doc_info.status = DocumentStatus.FAILED
            await cache.delete(f"kb:{kb_id}:chunk_counts")  # 使分块计数缓存失效
            doc_info.error_message = str(e)[:200]
            await cache.set_json(_task_key(kb_id, task_id), doc_info.model_dump(mode="json"), ex=TASK_TTL)




async def _ingest_graph_async(chunks, kb_id: str, doc_id: str, filename: str):
    """并发抽取一部分分块的实体并写入 Neo4j。"""
    try:
        from app.core.user_settings import get_settings
        if not get_settings().neo4j_enabled:
            return
        max_graph_chunks = settings.graph_ingest_max_chunks
        sample = chunks if max_graph_chunks <= 0 else chunks[:max_graph_chunks]
        logger.info(
            f"GraphRAG: extracting from {len(sample)}/{len(chunks)} chunks of {filename} "
            f"(sample={max_graph_chunks}, concurrent=4)"
        )
        sem = asyncio.Semaphore(4)

        async def one(c):
            async with sem:
                await ingest_chunk_to_graph(
                    chunk_text=c.content,
                    chunk_id=f"{doc_id}:{c.metadata.chunk_index}",
                    doc_id=doc_id,
                    kb_id=kb_id,
                    filename=filename,
                )

        await asyncio.gather(*(one(c) for c in sample))
        logger.info(f"GraphRAG: done for {filename} ({len(sample)} chunks processed)")
    except Exception as e:
        logger.warning(f"GraphRAG ingestion skipped for {filename}: {e}")

async def _upsert_with_retry(chunks, kb_id: str, filename: str, max_retries: int = RETRY_COUNT) -> int:
    """向量化并写入分块，失败时自动重试。"""
    last_error = None
    for attempt in range(max_retries):
        try:
            loop = asyncio.get_running_loop()
            count = await loop.run_in_executor(None, vector_store.upsert_chunks, chunks, kb_id)
            if attempt > 0:
                logger.info(f"Doc {filename}: upsert succeeded on retry {attempt}")
            return count
        except Exception as e:
            last_error = e
            if attempt < max_retries - 1:
                delay = RETRY_DELAY * (attempt + 1)
                logger.warning(f"Doc {filename}: upsert attempt {attempt+1} failed, retrying in {delay}s...")
                await asyncio.sleep(delay)
            else:
                logger.error(f"Doc {filename}: upsert failed after {max_retries} attempts")
    raise last_error


async def get_task_info(kb_id: str, task_id: str) -> Optional[DocumentInfo]:
    raw = await cache.get_json(_task_key(kb_id, task_id))
    if raw:
        return DocumentInfo(**raw)
    return None


async def list_tasks(kb_id: str = "default", tenant_id: Optional[str] = None) -> list[DocumentInfo]:
    all_keys = await cache.keys(f"kb:{kb_id}:doc:*")
    tasks = []
    for key in all_keys:
        raw = await cache.get_json(key)
        if raw:
            t = DocumentInfo(**raw)
            if tenant_id and t.tenant_id != tenant_id:
                continue
            tasks.append(t)
    return sorted(tasks, key=lambda t: t.created_at, reverse=True)


async def delete_task(kb_id: str, task_id: str):
    await cache.delete(_task_key(kb_id, task_id))
    from app.core.kb_service import update_kb_doc_count
    await update_kb_doc_count(kb_id)
    asyncio.create_task(_rebuild_bm25_async(kb_id))


async def _rebuild_bm25_async(kb_id: str = "default"):
    try:
        from app.rag.retriever import rebuild_bm25_index
        await asyncio.get_running_loop().run_in_executor(None, rebuild_bm25_index, kb_id)
    except Exception as e:
        logger.warning(f"BM25 rebuild failed for kb:{kb_id}: {e}")


def get_file_extension(filename: str) -> str:
    ext_map = {".pdf": "pdf", ".docx": "docx", ".md": "md", ".txt": "txt"}
    return ext_map.get(Path(filename).suffix.lower(), "txt")
