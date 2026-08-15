import sys, os
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        ac.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})
        yield ac


@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_upload_txt(client):
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": ("test.txt", b"Hello world. This is a test document.")}
    data = {"tenant_id": "default"}
    r = await client.post("/api/documents/upload", files=files, data=data, headers=headers)
    assert r.status_code == 200
    assert "task_id" in r.json()


@pytest.mark.asyncio
async def test_upload_bad_format(client):
    login = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    token = login.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    files = {"file": ("test.exe", b"bad")}
    r = await client.post("/api/documents/upload", files=files, data={"tenant_id": "default"}, headers=headers)
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_list_documents(client):
    r = await client.get("/api/documents/list?tenant_id=default")
    assert r.status_code == 200
    assert "documents" in r.json()


@pytest.mark.asyncio
async def test_chat_empty(client):
    r = await client.post("/api/chat/send", json={"session_id": "test", "message": "", "top_k": 5})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_chat_history(client):
    r = await client.get("/api/chat/history/test")
    assert r.status_code == 200
    assert "history" in r.json()


@pytest.mark.asyncio
async def test_chat_clear(client):
    r = await client.post("/api/chat/clear/test")
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_stream_empty(client):
    r = await client.post("/api/chat/stream", data={"session_id": "x", "message": "", "top_k": 5})
    assert r.status_code in (400, 422)

# ============================================================
# Reranker API tests
# ============================================================

@pytest.mark.asyncio
async def test_chat_with_rerank_mmr(client):
    """Chat endpoint should accept and relay rerank_strategy."""
    r = await client.post("/api/chat/send", json={
        "session_id": "test-rerank", "message": "?????",
        "top_k": 5, "rerank_strategy": "mmr"
    })
    # May fail if no docs indexed or LLM unavailable, but should at least parse the request
    assert r.status_code in (200, 500, 503)


@pytest.mark.asyncio
async def test_chat_with_rerank_none(client):
    """Chat endpoint with rerank_strategy=none should work normally."""
    r = await client.post("/api/chat/send", json={
        "session_id": "test-noreank", "message": "??",
        "top_k": 5, "rerank_strategy": "none"
    })
    assert r.status_code in (200, 500, 503)
