import sys, os
os.chdir(r"E:\aProgramming_code\GetAJobProject\企业知识库搭建")
sys.path.insert(0, os.getcwd())

import pytest
from app.models.document import ChunkMetadata, DocumentChunk


class TestParentChild:
    def test_parent_chunks_created(self):
        """Verify parent-child chunks are created and linked."""
        from app.core.chunker import build_chunks, _build_parent_child_chunks
        from app.models.document import ParseResult

        # Create 6 child chunks
        chunks = [
            DocumentChunk(
                content=f"Section {i} content with enough text to be meaningful.",
                metadata=ChunkMetadata(
                    doc_id="test-doc", filename="test.txt",
                    page_number=1, chunk_index=i, tenant_id="default",
                )
            )
            for i in range(6)
        ]

        result = _build_parent_child_chunks(chunks, parent_multiplier=3)

        # Should have 6 children + 2 parents (windows of 3)
        assert len(result) >= 8
        children = [c for c in result if c.metadata.parent_id is not None]
        parents = [c for c in result if c.metadata.parent_id is None and c.metadata.chunk_index >= 6]
        assert len(children) == 6
        assert len(parents) >= 2

        # Parents should have longer content than children
        for parent in parents:
            assert len(parent.content) > len(children[0].content)

    def test_parent_child_ids_consistent(self):
        """Same parent window -> same parent_id."""
        from app.core.chunker import _build_parent_child_chunks
        from app.models.document import ParseResult

        chunks = [
            DocumentChunk(
                content=f"Chunk {i}",
                metadata=ChunkMetadata(
                    doc_id="test", filename="t.txt",
                    page_number=1, chunk_index=i,
                )
            )
            for i in range(5)
        ]

        result = _build_parent_child_chunks(chunks, parent_multiplier=2)
        children = [c for c in result if c.metadata.parent_id]

        # First 2 children share parent_0, next 2 share parent_2, last 1 shares parent_4
        assert children[0].metadata.parent_id == children[1].metadata.parent_id
        assert children[2].metadata.parent_id == children[3].metadata.parent_id


class TestHyDE:
    def test_trigger_short_query(self):
        """Short queries should trigger HyDE."""
        from app.rag.hyde import should_trigger_hyde

        # Short query
        import asyncio
        result = asyncio.run(should_trigger_hyde("什么是RAG"))
        assert result is True

        # Long query with high score
        result = asyncio.run(should_trigger_hyde(
            "请详细解释RAG技术的原理和在企业中的应用场景",
            best_score=0.8
        ))
        assert result is False

    def test_hypothetical_doc_format(self):
        """HyDE prompt should generate a document-like passage, not an answer."""
        from app.rag.hyde import HYDE_PROMPT
        prompt = HYDE_PROMPT.format(query="RAG是什么")
        # Should ask to write as document, not answer
        assert "document would say" in prompt
        assert "Do NOT answer" in prompt
