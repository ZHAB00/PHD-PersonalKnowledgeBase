from __future__ import annotations
import asyncio
import logging
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

TASK_TTL = 86400
MAX_CONCURRENT = 2       # max concurrent embedding tasks (Ollama limit)
RETRY_COUNT = 1          # retry on embedding failure
RETRY_DELAY = 2.0        # seconds between retries

# Semaphore to limit concurrent document processing
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
    """Process a single document: parse -> chunk -> embed -> upsert, with concurrency control and retry."""
    async with _semaphore:
        loop = asyncio.get_running_loop()
        raw = await cache.get_json(_task_key(kb_id, task_id))
        if not raw:
            return
        doc_info = DocumentInfo(**raw)
        try:
            # Step 1: Parse (CPU-bound, run in executor)
            parse_result = await loop.run_in_executor(None, parse_document, filepath)
            doc_info.total_pages = parse_result.pages

            # Step 2: Chunk
            chunks = build_chunks(parse_result=parse_result, doc_id=task_id, filename=filename, tenant_id=tenant_id)
            for c in chunks:
                c.metadata.kb_id = kb_id
            doc_info.total_chunks = len(chunks)

            # Step 2.5: GraphRAG entity extraction (async fire-and-forget, best-effort)
            asyncio.create_task(_ingest_graph_async(chunks, kb_id, task_id, filename))

            # Step 3: Embed + Upsert with retry
            count = await _upsert_with_retry(chunks, kb_id, filename)
            doc_info.status = DocumentStatus.READY
            await cache.delete(f"kb:{kb_id}:chunk_counts")  # invalidate cache
            doc_info.updated_at = datetime.now(UTC)
            await cache.set_json(_task_key(kb_id, task_id), doc_info.model_dump(mode="json"), ex=TASK_TTL)
            logger.info(f"Doc {filename}: {count} chunks -> kb:{kb_id}")

            # Update KB doc count
            from app.core.kb_service import update_kb_doc_count
            await update_kb_doc_count(kb_id)

            # Rebuild BM25
            asyncio.create_task(_rebuild_bm25_async(kb_id))

        except Exception as e:
            logger.error(f"Doc processing failed {filename}: {e}")
            doc_info.status = DocumentStatus.FAILED
            await cache.delete(f"kb:{kb_id}:chunk_counts")  # invalidate cache
            doc_info.error_message = str(e)[:200]
            await cache.set_json(_task_key(kb_id, task_id), doc_info.model_dump(mode="json"), ex=TASK_TTL)




async def _ingest_graph_async(chunks, kb_id: str, doc_id: str, filename: str):
    """Extract entities from a sample of chunks and store in Neo4j."""
    try:
        from app.config import settings
        if not settings.neo4j_enabled:
            return
        # Only process first N chunks to avoid excessive LLM calls
        max_graph_chunks = 20
        sample = chunks[:max_graph_chunks]
        logger.info(f"GraphRAG: extracting from {len(sample)}/{len(chunks)} chunks of {filename}")
        for c in sample:
            chunk_id = f"{doc_id}:{c.metadata.chunk_index}"
            await ingest_chunk_to_graph(
                chunk_text=c.content,
                chunk_id=chunk_id,
                doc_id=doc_id,
                kb_id=kb_id,
            )
        logger.info(f"GraphRAG: done for {filename} ({len(sample)} chunks processed)")
    except Exception as e:
        logger.warning(f"GraphRAG ingestion skipped for {filename}: {e}")

async def _upsert_with_retry(chunks, kb_id: str, filename: str, max_retries: int = RETRY_COUNT) -> int:
    """Embed and upsert chunks with automatic retry on failure."""
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
