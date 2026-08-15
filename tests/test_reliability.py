import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import json
import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core import kb_service
from app.rag import hybrid_retriever
from app.rag import retriever
from app.rag.hybrid_retriever import HybridRetriever
from app.models.chat import SourceReference


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        ac.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})
        yield ac


async def _admin_headers(client):
    login = await client.post(
        "/api/auth/login",
        json={"username": "admin", "password": "admin123"},
    )
    return {"Authorization": "Bearer " + login.json()["access_token"]}


@pytest.mark.asyncio
async def test_upload_rejects_kb_id_traversal(client):
    headers = await _admin_headers(client)
    files = {"file": ("a.txt", b"hello")}
    r = await client.post(
        "/api/documents/upload",
        files=files,
        data={"kb_id": "../../escape", "tenant_id": "default"},
        headers=headers,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_upload_rejects_filename_traversal(client):
    headers = await _admin_headers(client)
    files = {"file": ("../evil.txt", b"hello")}
    r = await client.post(
        "/api/documents/upload",
        files=files,
        data={"kb_id": "default", "tenant_id": "default"},
        headers=headers,
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_get_kb_falls_back_to_file(tmp_path, monkeypatch):
    kb_file = tmp_path / "kbs.json"
    kb_file.write_text(
        json.dumps([{"id": "restored-kb", "name": "restored", "description": "", "doc_count": 0}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb_service, "_kb_file", lambda: kb_file)

    async def fake_get_json(key):
        return None

    monkeypatch.setattr(kb_service.cache, "get_json", fake_get_json)
    kb = await kb_service.get_kb("restored-kb")
    assert kb is not None
    assert kb.id == "restored-kb"
    assert kb.name == "restored"


@pytest.mark.asyncio
async def test_get_kb_list_restores_file_without_overwrite(tmp_path, monkeypatch):
    kb_file = tmp_path / "kbs.json"
    kb_file.write_text(
        json.dumps([
            {"id": "kb-a", "name": "A", "description": "", "doc_count": 0},
            {"id": "kb-b", "name": "B", "description": "", "doc_count": 0},
        ]),
        encoding="utf-8",
    )
    monkeypatch.setattr(kb_service, "_kb_file", lambda: kb_file)

    async def fake_get_json(key):
        return None

    monkeypatch.setattr(kb_service.cache, "get_json", fake_get_json)
    kbs = await kb_service.get_kb_list()
    assert [k.id for k in kbs] == ["kb-a", "kb-b"]
    restored = json.loads(kb_file.read_text(encoding="utf-8"))
    assert [item["id"] for item in restored] == ["kb-a", "kb-b"]


@pytest.mark.asyncio
async def test_list_documents_rejects_kb_id_traversal(client):
    r = await client.get("/api/documents/list?kb_id=../../evil&tenant_id=default")
    assert r.status_code == 400


def test_bm25_preserves_metadata(monkeypatch):
    monkeypatch.setattr(
        hybrid_retriever,
        "_tokenize",
        lambda text: text.lower().split(),
    )
    retriever = HybridRetriever(kb_id="test-kb", tenant_id="t")
    retriever.build_bm25_index(
        ["hello world content", "other text", "unrelated thing"],
        ["d1", "d2", "d3"],
        [
            {"filename": "a.md", "page_number": 3, "chunk_index": 0},
            {"filename": "b.md", "page_number": 5, "chunk_index": 1},
            {"filename": "c.md", "page_number": 7, "chunk_index": 2},
        ],
    )
    results = retriever._bm25_search("hello", 1)
    assert results
    assert results[0].filename == "a.md"
    assert results[0].page_number == 3
    assert results[0].chunk_index == 0


def test_retrieve_graphrag_flag(monkeypatch):
    class FakeRetriever:
        _bm25 = None

        def search(self, query, top_k, tenant_id, kb_id=None):
            return []

    monkeypatch.setattr(retriever, "get_hybrid_retriever", lambda kb_id, tid: FakeRetriever())

    docs = [
        SourceReference(doc_id="d1", filename="a.md", page_number=1, chunk_index=0, content="alpha", score=0.9),
        SourceReference(doc_id="d2", filename="b.md", page_number=2, chunk_index=1, content="beta", score=0.8),
    ]
    monkeypatch.setattr(retriever, "_vector_only_retrieve", lambda query, top_k, kb_id, tenant_id: docs)
    graph = [
        SourceReference(doc_id="graph_rag", filename="[graph]", page_number=None, chunk_index=0, content="graph", score=0.85),
    ]
    monkeypatch.setattr(retriever, "_graph_retrieve", lambda query, kb_id, tid: graph)

    off = retriever.retrieve(
        "query", top_k=2, kb_id="kb", tenant_id="t",
        enable_rewrite=False, rerank_strategy="none", enable_graphrag=False,
    )
    assert [s.doc_id for s in off] == ["d1", "d2"]

    on = retriever.retrieve(
        "query", top_k=2, kb_id="kb", tenant_id="t",
        enable_rewrite=False, rerank_strategy="none", enable_graphrag=True,
    )
    assert [s.doc_id for s in on] == ["d1", "d2", "graph_rag"]


def test_graph_retrieve_keeps_document_filename(monkeypatch):
    from app.rag import retriever

    evidence = [
        {
            "source": "graph_chunk", "entity": "RAG", "type": "文档片段",
            "relation": "提及", "related_entity": "", "doc_id": "doc1",
            "filename": "03-RAG技术.md", "description": "RAG 是检索增强生成。",
        },
        {
            "source": "graph", "entity": "RAG", "type": "架构",
            "relation": "匹配查询", "related_entity": "", "description": "架构说明",
        },
    ]
    monkeypatch.setattr(retriever, "retrieve_graph_evidence", lambda query, kb_id: evidence)
    sources = retriever._graph_retrieve("RAG", "kb", "default")
    filenames = [s.filename for s in sources]
    assert "03-RAG技术.md" in filenames
    assert "[知识图谱]" in filenames
    doc_source = next(s for s in sources if s.filename == "03-RAG技术.md")
    assert doc_source.content.startswith("【图谱证据")


def test_retrieve_multi_query_passes_kb_and_tenant(monkeypatch):
    class FakeRetriever:
        _bm25 = None

        def search(self, query, top_k, tenant_id, kb_id=None):
            return []

    monkeypatch.setattr(retriever, "get_hybrid_retriever", lambda kb_id, tid: FakeRetriever())
    monkeypatch.setattr(
        retriever,
        "rewrite_query_with_history",
        lambda query, history: query + " expanded",
    )
    monkeypatch.setattr(retriever, "_run_async_in_thread", lambda coro_factory: False)
    monkeypatch.setattr(retriever, "_graph_retrieve", lambda query, kb_id, tid: [])

    captured = {}

    def fake_vector(query, top_k, kb_id, tenant_id):
        captured["kb_id"] = kb_id
        captured["tenant_id"] = tenant_id
        return [
            SourceReference(
                doc_id="d1", filename="a.md", page_number=1,
                chunk_index=0, content="alpha", score=0.9,
            )
        ]

    monkeypatch.setattr(retriever, "_vector_only_retrieve", fake_vector)
    results = retriever.retrieve(
        "query", top_k=2, kb_id="kb", tenant_id="t",
        enable_rewrite=True, rerank_strategy="none", enable_graphrag=False,
    )
    assert captured["kb_id"] == "kb"
    assert captured["tenant_id"] == "t"
    assert results


def test_build_tool_call_events_pairs_by_order():
    from app.rag.graph import _build_tool_call_events

    tool_msgs = [
        {"role": "assistant", "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "calculator", "arguments": "{\"expression\": \"1+1\"}"}}]},
        {"role": "tool", "tool_call_id": "call_1", "content": "{\"result\": 2}"},
        {"role": "assistant", "tool_calls": [{"id": "call_2", "type": "function", "function": {"name": "search_knowledge_base", "arguments": "{\"query\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "call_2", "content": "{\"results\": [{\"filename\": \"a.md\", \"content\": \"hello\"}]}"},
    ]
    events = _build_tool_call_events(tool_msgs)
    assert [e.tool_name for e in events] == ["calculator", "search_knowledge_base"]
    assert events[0].result == "{\"result\": 2}"
    assert events[1].sources and events[1].sources[0]["filename"] == "a.md"

def test_tool_result_status_uses_structure_not_keywords():
    from app.rag.tools import tool_result_status

    ok_with_keywords = json.dumps({
        "count": 1,
        "results": [{"content": "包含 error handling 和失败回退等正文关键词"}],
        "is_empty": False,
    })
    assert tool_result_status(ok_with_keywords) == "ok"
    assert tool_result_status(json.dumps({"count": 0, "results": [], "is_empty": True})) == "ok"
    assert tool_result_status(json.dumps({"error": "检索失败: timeout"})) == "error"
    assert tool_result_status(json.dumps({"status": "error", "error": "tool boom"})) == "error"


def test_build_tool_call_events_status_ok_for_search_result_with_keywords():
    from app.rag.graph import _build_tool_call_events

    tool_msgs = [
        {"role": "assistant", "tool_calls": [{"id": "call_k", "type": "function", "function": {"name": "search_knowledge_base", "arguments": "{\"query\": \"x\"}"}}]},
        {"role": "tool", "tool_call_id": "call_k", "content": json.dumps({
            "count": 1,
            "results": [{"filename": "03-RAG技术.md", "content": "讨论 error handling、失败重试和错误兜底策略"}],
            "is_empty": False,
        }, ensure_ascii=False)},
    ]
    events = _build_tool_call_events(tool_msgs)
    assert events[0].status == "ok"
    assert events[0].sources and events[0].sources[0]["filename"] == "03-RAG技术.md"


def test_retrieve_cache_serves_second_call(monkeypatch):
    from app.rag import retriever
    retriever.clear_retrieve_cache()

    class FakeRetriever:
        _bm25 = None

        def search(self, query, top_k, tenant_id, kb_id=None):
            return []

    calls = {"n": 0}

    def fake_vector(query, top_k, kb_id, tenant_id):
        calls["n"] += 1
        return [
            SourceReference(
                doc_id="d1", filename="a.md", page_number=1,
                chunk_index=0, content="alpha", score=0.9,
            )
        ]

    monkeypatch.setattr(retriever, "get_hybrid_retriever", lambda kb_id, tid: FakeRetriever())
    monkeypatch.setattr(retriever, "_vector_only_retrieve", fake_vector)
    monkeypatch.setattr(retriever, "_graph_retrieve", lambda query, kb_id, tid: [])

    first = retriever.retrieve(
        "cache-unique", top_k=2, kb_id="kb", tenant_id="t",
        enable_rewrite=False, rerank_strategy="none", enable_graphrag=False,
    )
    second = retriever.retrieve(
        "cache-unique", top_k=2, kb_id="kb", tenant_id="t",
        enable_rewrite=False, rerank_strategy="none", enable_graphrag=False,
    )
    assert first and second
    assert calls["n"] == 1


def test_mmr_rerank_caches_document_embeddings(monkeypatch):
    from app.rag import reranker
    reranker.clear_rerank_cache()
    calls = {"n": 0}

    def fake_embed_texts(texts):
        calls["n"] += 1
        return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr(reranker, "embed_texts", fake_embed_texts)
    monkeypatch.setattr(reranker, "embed_text", lambda text: [0.0, 1.0])

    sources = [
        SourceReference(doc_id=f"d{i}", filename=f"f{i}.md", page_number=1, chunk_index=i, content=f"content {i}", score=0.9 - i * 0.1)
        for i in range(5)
    ]
    r1 = reranker._mmr_rerank("query", sources, 3, 0.7)
    r2 = reranker._mmr_rerank("query", sources, 3, 0.7)
    assert [s.doc_id for s in r1] == [s.doc_id for s in r2]
    assert calls["n"] == 1
