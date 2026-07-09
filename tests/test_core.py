import sys, os
os.chdir(r"E:\aProgramming_code\GetAJobProject\企业知识库搭建")
sys.path.insert(0, os.getcwd())

import pytest
from pathlib import Path
from app.core.document_parser import parse_document
from app.core.chunker import build_chunks
from app.models.document import ParseResult


def test_parse_txt():
    p = Path("data/documents/test_sample.txt")
    p.write_text("第一行内容\n\n第二行内容\n\n第三行内容", encoding="utf-8")
    try:
        result = parse_document(p)
        assert isinstance(result, ParseResult)
        assert "第一行" in result.content
        assert result.pages == 1
    finally:
        p.unlink(missing_ok=True)


def test_parse_md():
    p = Path("data/documents/test_sample.md")
    p.write_text("# 标题\n\n正文内容\n\n## 二级标题\n\n更多内容", encoding="utf-8")
    try:
        result = parse_document(p)
        assert isinstance(result, ParseResult)
        assert "# 标题" in result.content
    finally:
        p.unlink(missing_ok=True)


def test_chunker_basic():
    parse_result = ParseResult(
        content="[第 1 页]\n这是一段测试文本，用来验证分块功能。需要足够长的内容来触发分割。",
        tables=[],
        pages=1,
        metadata={},
    )
    chunks = build_chunks(parse_result, doc_id="test-1", filename="test.txt", chunk_size=50, chunk_overlap=10)
    assert len(chunks) > 0
    for c in chunks:
        assert c.metadata.doc_id == "test-1"
        assert c.metadata.filename == "test.txt"


def test_chunker_preserves_tables():
    table_md = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    parse_result = ParseResult(
        content=f"[第 1 页]\n文本内容\n\n[第 1 页 - 表格]\n{table_md}",
        tables=[{"page": 1, "markdown": table_md, "data": {"headers": ["A", "B"], "rows": [["1", "2"]]}}],
        pages=1,
        metadata={},
    )
    chunks = build_chunks(parse_result, doc_id="test-2", filename="test.txt")
    table_chunks = [c for c in chunks if c.metadata.is_table]
    assert len(table_chunks) == 1
    assert table_md in table_chunks[0].content


def test_parse_unknown_format():
    p = Path("data/documents/test.exe")
    p.write_text("fake", encoding="utf-8")
    try:
        with pytest.raises(ValueError, match="不支持的文档格式"):
            parse_document(p)
    finally:
        p.unlink(missing_ok=True)

# ============================================================
# Reranker tests
# ============================================================

def test_reranker_noop():
    """Reranker with too few sources should return as-is."""
    from app.rag.reranker import rerank
    from app.models.chat import SourceReference
    sources = [
        SourceReference(doc_id="d1", filename="a.txt", content="??A", score=0.9, chunk_index=0),
    ]
    result = rerank("??", sources, top_n=5, strategy="mmr")
    assert len(result) == 1
    assert result[0].doc_id == "d1"


def test_reranker_none_strategy():
    """Strategy 'none' should just truncate to top_n."""
    from app.rag.reranker import rerank
    from app.models.chat import SourceReference
    sources = [
        SourceReference(doc_id=f"d{i}", filename=f"f{i}.txt", content=f"??{i}", score=1.0 - 0.1 * i, chunk_index=0)
        for i in range(10)
    ]
    result = rerank("??", sources, top_n=3, strategy="none")
    assert len(result) == 3


def test_reranker_mmr_no_duplicate_doc_ids():
    """MMR should not reorder identically ranked items arbitrarily ? it handles diversity.
    Basic smoke test: MMR on diverse sources should complete without error."""
    from app.rag.reranker import rerank
    from app.models.chat import SourceReference
    sources = [
        SourceReference(doc_id=f"d{i}", filename=f"f{i}.txt", content=f"???{i+1}??????????????", score=0.8, chunk_index=0)
        for i in range(8)
    ]
    result = rerank("????", sources, top_n=5, strategy="mmr", lambda_mult=0.7)
    assert len(result) == 5
    # All doc_ids should be unique
    assert len(set(s.doc_id for s in result)) == 5
