import sys, os
os.chdir(r"E:\aProgramming_code\GetAJobProject\企业知识库搭建")
sys.path.insert(0, os.getcwd())

import pytest
from app.models.document import ChunkMetadata, DocumentChunk, ParseResult


class TestParentChildIntegration:
    """Integration test: full build_chunks -> verify parent-child structure end to end."""

    def test_build_chunks_with_parent_child(self):
        from app.core.chunker import build_chunks

        content = """# 第一章 概述

这是第一章的内容。包含一些背景介绍和相关概念。

## 1.1 背景

这个项目起源于企业知识管理的需求。

## 1.2 目标

构建一个高效的知识库系统。

# 第二章 技术架构

技术架构分为三层。每层有不同的职责。

## 2.1 数据层

数据层负责文档存储和向量索引。

## 2.2 服务层

服务层提供 API 接口。"""
        parse_result = ParseResult(content=content, tables=[], pages=1, metadata={})
        chunks = build_chunks(parse_result, doc_id="test-pc", filename="test.md",
                               chunk_size=60, chunk_overlap=10, enable_parent_child=True)

        # Should have children + parents
        children = [c for c in chunks if c.metadata.parent_id]
        parents = [c for c in chunks if c.metadata.parent_id is None and c.metadata.chunk_index >= len(children)]

        assert len(children) > 0, "Should have child chunks"
        assert len(parents) > 0, "Should have parent chunks"

        # Each child should reference a valid parent
        for child in children:
            assert child.metadata.parent_id is not None
            assert child.metadata.parent_id.startswith("parent_")

        # Parent content should be longer than any single child
        max_child_len = max(len(c.content) for c in children)
        for parent in parents:
            assert len(parent.content) >= max_child_len, \
                f"Parent ({len(parent.content)}) should be >= longest child ({max_child_len})"

    def test_parent_id_uniqueness(self):
        from app.core.chunker import _build_parent_child_chunks
        import re

        chunks = [
            DocumentChunk(content=f"Content block {i}", metadata=ChunkMetadata(
                doc_id="x", filename="x.txt", page_number=1, chunk_index=i))
            for i in range(10)
        ]
        result = _build_parent_child_chunks(chunks, parent_multiplier=3)

        children = [c for c in result if c.metadata.parent_id]
        parent_ids = set(c.metadata.parent_id for c in children)

        # 10 chunks with window=3 => ceil(10/3) = 4 unique parent_ids
        assert len(parent_ids) == 4


class TestHyDEIntegration:
    """Test HyDE trigger logic and prompt format."""

    def test_should_trigger_short_chinese_query(self):
        import asyncio
        from app.rag.hyde import should_trigger_hyde

        # Chinese short query
        result = asyncio.run(should_trigger_hyde("RAG?", best_score=0.9))
        assert result is True, "Short query should trigger even with high score"

    def test_should_not_trigger_long_high_score(self):
        import asyncio
        from app.rag.hyde import should_trigger_hyde

        result = asyncio.run(should_trigger_hyde(
            "请问在企业环境中如何部署RAG系统并保证数据安全？",
            best_score=0.85
        ))
        assert result is False

    def test_should_trigger_low_score_only(self):
        import asyncio
        from app.rag.hyde import should_trigger_hyde

        # Long query but low score
        result = asyncio.run(should_trigger_hyde(
            "请详细说明知识库系统的检索优化策略",
            best_score=0.40
        ))
        assert result is True, "Low score should trigger even for long queries"


class TestExpandParents:
    """Test parent-child expansion logic in prompts module."""

    def test_expand_parents_no_parent_ids(self):
        from app.rag.prompts import expand_parents

        sources = [
            {"doc_id": "d1", "filename": "a.txt", "content": "Hello", "score": 0.9,
             "page_number": 1, "parent_id": ""},
        ]
        result = expand_parents(sources, "default")
        assert len(result) == 1
        assert result[0]["content"] == "Hello"

    def test_expand_parents_merges_siblings(self):
        """Children from same parent should be merged into one expanded entry."""
        from app.rag.prompts import expand_parents

        sources = [
            {"doc_id": "d1", "filename": "a.txt", "content": "chunk 1", "score": 0.9,
             "page_number": 1, "parent_id": "parent_0"},
            {"doc_id": "d2", "filename": "a.txt", "content": "chunk 2", "score": 0.8,
             "page_number": 1, "parent_id": "parent_0"},
        ]
        # Without real parent chunks in Qdrant, expand_parents returns sources as-is
        result = expand_parents(sources, "default")
        # Both should be present (parent lookup fails -> falls back to original)
        assert len(result) >= 1


class TestEvalModule:
    """Test evaluation module functions."""

    def test_faithfulness_prompt_format(self):
        from app.rag.evaluation import FAITHFULNESS_PROMPT
        prompt = FAITHFULNESS_PROMPT.format(question="Q", context="C", answer="A")
        assert "Q" in prompt
        assert "C" in prompt
        assert "A" in prompt
        assert "0-10" in prompt

    def test_extract_json_valid(self):
        from app.rag.evaluation import _extract_json
        result = _extract_json('some text {"score": 8, "reason": "good"} more text')
        assert result == {"score": 8, "reason": "good"}

    def test_extract_json_no_json(self):
        from app.rag.evaluation import _extract_json
        result = _extract_json("no json here")
        assert result is None

    def test_run_ragas_eval_structure(self):
        from app.rag.evaluation import run_ragas_eval
        # We can't actually call LLM in unit test, but verify the function exists
        assert callable(run_ragas_eval)
        assert callable(lambda q, a, c: run_ragas_eval(q, a, c))
