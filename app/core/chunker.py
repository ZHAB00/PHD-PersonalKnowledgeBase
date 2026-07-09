"""Chunking strategy v4: structural + parent-child with full parent content + adaptive sizing

References (RAG Best Practices Section 3):
  3.2 Recursive character splitting (retained as fallback)
  3.4 Structural chunking by Markdown headers (core strategy)
  3.6 Adaptive chunk_size per document type
  3.7 Parent-child chunking: small child for retrieval, large parent for LLM context

Parent-child design:
  - Child chunk = small precise segment (used for vector embedding & retrieval)
  - Parent chunk = larger context window containing the child (used for LLM prompt)
  - Both stored in Qdrant with parent_id linking
  - At retrieval time: hit a child -> expand to its parent content for richer context
"""
from __future__ import annotations
import re
from typing import Optional

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
)

from app.models.document import ChunkMetadata, DocumentChunk, ParseResult

# Default chunking config per file type
CHUNK_CONFIG = {
    ".pdf":  {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".docx": {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".md":   {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".txt":  {"chunk_size": 500, "chunk_overlap": 80,  "parent_multiplier": 2},
}

# Page marker pattern (Chinese: "Page N" or "Di N Ye")
PAGE_MARKER = re.compile(r"\[\u7b2c (\d+) \u9875[^\]]*\]")


def build_chunks(
    parse_result: ParseResult,
    doc_id: str,
    filename: str,
    tenant_id: str = "default",
    chunk_size: int = 800,
    chunk_overlap: int = 150,
    enable_parent_child: bool = True,
) -> list[DocumentChunk]:
    ext = _get_ext(filename)
    cfg = CHUNK_CONFIG.get(ext, CHUNK_CONFIG[".txt"])
    chunk_size = chunk_size or cfg["chunk_size"]
    chunk_overlap = chunk_overlap or cfg["chunk_overlap"]
    parent_multiplier = cfg["parent_multiplier"]

    content = parse_result.content
    table_markdowns = {t.get("markdown", "") for t in parse_result.tables}
    headings = _extract_headings(content)

    if headings:
        chunks = _structural_chunking(
            content=content, headings=headings,
            table_markdowns=table_markdowns,
            doc_id=doc_id, filename=filename,
            tenant_id=tenant_id, chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            enable_parent_child=enable_parent_child,
        )
    else:
        chunks = _page_based_chunking(
            content=content, table_markdowns=table_markdowns,
            doc_id=doc_id, filename=filename,
            tenant_id=tenant_id, chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    for i, c in enumerate(chunks):
        c.metadata.chunk_index = i
        c.metadata.tenant_id = tenant_id

    # Parent-child: build parent chunks that contain wider context
    if enable_parent_child and chunks:
        chunks = _build_parent_child_chunks(chunks, parent_multiplier)

    return chunks


def _build_parent_child_chunks(
    chunks: list[DocumentChunk],
    parent_multiplier: int,
) -> list[DocumentChunk]:
    """Build parent-child hierarchy: each child gets a parent_id linking to a larger context window.

    Strategy: group consecutive child chunks into parent windows of size parent_multiplier.
    Parent content = concatenation of all children in the window.
    Each child stores parent_id; parent chunks are also stored (marked as is_parent=True)
    so they can be looked up at retrieval time by ID.

    Returns: original child chunks (now with parent_id set) + parent chunks appended.
    """
    if len(chunks) <= 1:
        for c in chunks:
            c.metadata.parent_id = None
        return chunks

    window_size = max(2, parent_multiplier)
    total = len(chunks)
    all_chunks = list(chunks)  # start with children
    parent_start_index = len(all_chunks)

    for window_start in range(0, total, window_size):
        window_end = min(window_start + window_size, total)
        window_children = chunks[window_start:window_end]
        parent_id = f"parent_{window_start}"

        # Parent content: all children concatenated with section breaks
        parent_content_parts = []
        parent_page = window_children[0].metadata.page_number
        for wc in window_children:
            parent_content_parts.append(wc.content)

        parent_content = "\n\n---\n\n".join(parent_content_parts)

        # Tag each child with parent_id
        for wc in window_children:
            wc.metadata.parent_id = parent_id

        # Create a parent chunk (stored for lookup, not for vector search)
        parent_chunk = DocumentChunk(
            content=parent_content,
            metadata=ChunkMetadata(
                doc_id=window_children[0].metadata.doc_id,
                filename=window_children[0].metadata.filename,
                page_number=parent_page,
                chunk_index=parent_start_index + window_start // window_size,
                is_table=False,
                tenant_id=window_children[0].metadata.tenant_id,
                parent_id=None,
            ),
        )
        all_chunks.append(parent_chunk)

    return all_chunks


def _structural_chunking(
    content, headings, table_markdowns, doc_id, filename,
    tenant_id, chunk_size, chunk_overlap, enable_parent_child,
) -> list[DocumentChunk]:
    sections = _split_by_headings(content, headings, level=2)
    chunks = []

    for sec_heading, sec_text in sections:
        heading_ctx = sec_heading if sec_heading else ""
        page_number = _extract_page_number(sec_text)

        if len(sec_text) <= chunk_size * 2:
            is_table = sec_text in table_markdowns
            chunks.append(DocumentChunk(
                content=_prefix_heading(sec_text, heading_ctx),
                metadata=ChunkMetadata(
                    doc_id=doc_id, filename=filename,
                    page_number=page_number, is_table=is_table,
                    table_html=sec_text if is_table else None,
                ),
            ))
        else:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                separators=["\n\n", "\n", ".", " ", ""],
            )
            sub_parts = _split_preserving_tables(sec_text, table_markdowns)
            for part_text, part_is_table in sub_parts:
                if part_is_table:
                    chunks.append(DocumentChunk(
                        content=_prefix_heading(part_text, heading_ctx),
                        metadata=ChunkMetadata(
                            doc_id=doc_id, filename=filename,
                            page_number=page_number, is_table=True,
                            table_html=part_text,
                        ),
                    ))
                elif len(part_text) <= chunk_size * 1.2:
                    chunks.append(DocumentChunk(
                        content=_prefix_heading(part_text, heading_ctx),
                        metadata=ChunkMetadata(
                            doc_id=doc_id, filename=filename,
                            page_number=page_number,
                        ),
                    ))
                else:
                    for sub_text in splitter.split_text(part_text):
                        sub_text = sub_text.strip()
                        if sub_text:
                            chunks.append(DocumentChunk(
                                content=_prefix_heading(sub_text, heading_ctx),
                                metadata=ChunkMetadata(
                                    doc_id=doc_id, filename=filename,
                                    page_number=page_number,
                                ),
                            ))
    return chunks


def _page_based_chunking(
    content, table_markdowns, doc_id, filename,
    tenant_id, chunk_size, chunk_overlap,
) -> list[DocumentChunk]:
    sections = re.split(r"(\[\u7b2c \d+ \u9875[^\]]*\])", content)
    chunks = []
    current_page = 1

    for section in sections:
        section = section.strip()
        if not section:
            continue
        m = re.match(r"\[\u7b2c (\d+) \u9875", section)
        if m:
            current_page = int(m.group(1))
            continue

        paragraphs = re.split(r"\n\s*\n", section)
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            is_table = para in table_markdowns
            if is_table or len(para) <= chunk_size * 1.2:
                chunks.append(DocumentChunk(
                    content=para,
                    metadata=ChunkMetadata(
                        doc_id=doc_id, filename=filename,
                        page_number=current_page, is_table=is_table,
                        table_html=para if is_table else None,
                    ),
                ))
            else:
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=chunk_size, chunk_overlap=chunk_overlap,
                    separators=["\n\n", "\n", ".", " ", ""],
                )
                for t in splitter.split_text(para):
                    t = t.strip()
                    if t:
                        chunks.append(DocumentChunk(
                            content=t,
                            metadata=ChunkMetadata(
                                doc_id=doc_id, filename=filename,
                                page_number=current_page,
                            ),
                        ))
    return chunks


def _get_ext(filename: str) -> str:
    import pathlib
    return pathlib.Path(filename).suffix.lower()


def _extract_headings(content: str) -> list[dict]:
    headings = []
    for match in re.finditer(r"^(#{1,6})\s+(.+)$", content, re.MULTILINE):
        headings.append({
            "level": len(match.group(1)),
            "text": match.group(2).strip(),
            "pos": match.start(),
        })
    return headings


def _split_by_headings(content, headings, level=2):
    if not headings:
        return [("", content)]

    splits = []
    current_h1 = ""
    for h in headings:
        if h["level"] == 1:
            current_h1 = h["text"]
        if h["level"] == level:
            ctx_parts = [p for p in [current_h1, h["text"]] if p]
            heading_context = " > ".join(ctx_parts)
            splits.append((h["pos"], heading_context))

    if not splits:
        return [("", content)]

    sections = []
    for i, (pos, ctx) in enumerate(splits):
        next_pos = splits[i + 1][0] if i + 1 < len(splits) else len(content)
        section_text = content[pos:next_pos].strip()
        nl_idx = section_text.find("\n")
        if nl_idx > 0:
            section_text = section_text[nl_idx + 1:].strip()
        sections.append((ctx, section_text))

    if splits and splits[0][0] > 0:
        pre_text = content[:splits[0][0]].strip()
        if pre_text:
            sections.insert(0, ("", pre_text))

    return sections


def _split_preserving_tables(text, table_markdowns):
    parts = []
    paragraphs = re.split(r"\n\s*\n", text)
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        is_table = para in table_markdowns
        parts.append((para, is_table))
    return parts


def _extract_page_number(text):
    m = re.search(r"\u7b2c (\d+) \u9875", text)
    return int(m.group(1)) if m else 1


def _prefix_heading(text, heading):
    if heading and not text.startswith("[" + heading + "]"):
        return f"[{heading}]\n{text}"
    return text
