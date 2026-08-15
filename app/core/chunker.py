"""分块策略 v4：结构化 + 父子分块 + 自适应尺寸

参考（RAG 最佳实践第 3 节）：
  3.2 递归字符切分（保留为回退方案）
  3.4 按 Markdown 标题结构化分块（核心策略）
  3.6 按文档类型自适应分块尺寸
  3.7 父子分块：小子块用于检索，大父块用于 LLM 上下文

父子分块设计：
  - 子块 = 小且精确的片段（用于向量化与检索）
  - 父块 = 包含子块的更大上下文窗口（用于 LLM 提示词）
  - 两者都存入 Qdrant，通过 parent_id 关联
  - 检索命中子块后展开父块内容以获得更丰富的上下文
"""
from __future__ import annotations
import re
from typing import Optional

from langchain_text_splitters import (
    RecursiveCharacterTextSplitter,
)

from app.models.document import ChunkMetadata, DocumentChunk, ParseResult

# 各文件类型的默认分块配置
CHUNK_CONFIG = {
    ".pdf":  {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".docx": {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".md":   {"chunk_size": 800, "chunk_overlap": 150, "parent_multiplier": 3},
    ".txt":  {"chunk_size": 500, "chunk_overlap": 80,  "parent_multiplier": 2},
}

# 页码标记模式（中文："第 N 页" 或 "Di N Ye"）
PAGE_MARKER = re.compile(r"\[\u7b2c (\d+) \u9875[^\]]*\]")


def build_chunks(
    parse_result: ParseResult,
    doc_id: str,
    filename: str,
    tenant_id: str = "default",
    chunk_size: int = 800,
    chunk_overlap: int = 150,
    enable_parent_child: bool = False,
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

    # 父子分块：构建包含更广上下文的父块
    if enable_parent_child and chunks:
        chunks = _build_parent_child_chunks(chunks, parent_multiplier)

    return chunks


def _build_parent_child_chunks(
    chunks: list[DocumentChunk],
    parent_multiplier: int,
) -> list[DocumentChunk]:
    """构建父子层级：每个子块通过 parent_id 关联更大的上下文窗口。

    策略：按 parent_multiplier 大小将连续子块分组成父窗口。
    父块内容 = 窗口内所有子块拼接。
    每个子块保存 parent_id；父块也一并存储（标记 is_parent=True），
    以便检索时按 ID 查询。

    返回：原始子块（已设置 parent_id）+ 追加的父块。
    """
    if len(chunks) <= 1:
        for c in chunks:
            c.metadata.parent_id = None
        return chunks

    window_size = max(2, parent_multiplier)
    total = len(chunks)
    all_chunks = list(chunks)  # 先放入子块
    parent_start_index = len(all_chunks)

    for window_start in range(0, total, window_size):
        window_end = min(window_start + window_size, total)
        window_children = chunks[window_start:window_end]
        parent_id = f"parent_{window_start}"

        # 父块内容：所有子块以分隔线拼接
        parent_content_parts = []
        parent_page = window_children[0].metadata.page_number
        for wc in window_children:
            parent_content_parts.append(wc.content)

        parent_content = "\n\n---\n\n".join(parent_content_parts)

        # 为每个子块标记 parent_id
        for wc in window_children:
            wc.metadata.parent_id = parent_id

        # 创建父块（用于查询，不参与向量检索）
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
