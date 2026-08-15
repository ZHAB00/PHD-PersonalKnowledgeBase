import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.user_settings import _normalize_local_url, chat_config, save_settings, UserSettings


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        login = await ac.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
        ac.headers.update({"Authorization": "Bearer " + login.json()["access_token"]})
        yield ac


@pytest.mark.asyncio
async def test_settings_get_does_not_leak_hash(client):
    r = await client.get("/api/settings")
    assert r.status_code == 200
    assert "password_hash" not in r.json()


@pytest.mark.asyncio
async def test_settings_save_password(client, tmp_path, monkeypatch):
    import app.core.user_settings as us
    monkeypatch.setattr(us, "SETTINGS_FILE", tmp_path / "settings.json")
    payload = {
        "chat_provider": "deepseek",
        "chat_base_url": "", "chat_api_key": "", "chat_model": "", "chat_thinking": True,
        "embedding_provider": "ollama", "embedding_model": "", "embedding_base_url": "", "embedding_api_key": "",
        "neo4j_enabled": False, "neo4j_uri": "", "neo4j_user": "", "neo4j_password": "", "neo4j_database": "",
        "require_password": True, "password": "abc123",
    }
    r = await client.put("/api/settings", json=payload)
    assert r.status_code == 200
    assert "password_hash" not in r.json()
    check = await client.post("/api/settings/verify-password", json={"password": "abc123"})
    assert check.json()["ok"] is True


def test_chat_config_lmstudio_default(monkeypatch):
    import app.core.user_settings as us
    monkeypatch.setattr(us, "SETTINGS_FILE", __import__("pathlib").Path("nonexistent-settings.json"))
    save_settings(UserSettings(chat_provider="lmstudio", configured=True))
    cfg = chat_config()
    assert cfg["base_url"] == "http://localhost:1234/v1"
    assert cfg["model"] == "local-model"


@pytest.mark.asyncio
async def test_login_local_password_flow(client, tmp_path, monkeypatch):
    import app.core.user_settings as us
    monkeypatch.setattr(us, "SETTINGS_FILE", tmp_path / "settings.json")
    r = await client.get("/api/settings/public")
    assert r.json()["require_password"] is False
    payload = {
        "chat_provider": "deepseek",
        "chat_base_url": "", "chat_api_key": "", "chat_model": "", "chat_thinking": True,
        "embedding_provider": "ollama", "embedding_model": "", "embedding_base_url": "", "embedding_api_key": "",
        "neo4j_enabled": False, "neo4j_uri": "", "neo4j_user": "", "neo4j_password": "", "neo4j_database": "",
        "require_password": True, "password": "abc123",
    }
    await client.put("/api/settings", json=payload)
    r = await client.get("/api/settings/public")
    assert r.json()["require_password"] is True
    bad = await client.post("/api/auth/login-local", json={"password": "wrong"})
    assert bad.status_code == 401
    ok = await client.post("/api/auth/login-local", json={"password": "abc123"})
    assert ok.status_code == 200
    assert ok.json()["access_token"]


def test_release_defaults_use_local_embedding():
    s = UserSettings()
    assert s.embedding_provider == "local"
    assert s.embedding_model == "BAAI/bge-small-zh-v1.5"
    assert s.app_port == 8001
    assert s.close_to_tray is True
    assert s.neo4j_enabled is False


def test_embedding_config_normalizes_localhost_to_ipv4():
    assert _normalize_local_url("http://localhost:11434/v1") == "http://127.0.0.1:11434/v1"
    assert _normalize_local_url("") == ""


@pytest.mark.asyncio
async def test_save_settings_normalizes_embedding_base_url(client, tmp_path, monkeypatch):
    import app.core.user_settings as us
    monkeypatch.setattr(us, "SETTINGS_FILE", tmp_path / "settings.json")
    payload = {
        "chat_provider": "deepseek",
        "chat_base_url": "", "chat_api_key": "", "chat_model": "", "chat_thinking": True,
        "embedding_provider": "ollama", "embedding_model": "qwen3-embedding:4b",
        "embedding_base_url": "http://localhost:11434/v1", "embedding_api_key": "",
        "neo4j_enabled": False, "neo4j_uri": "", "neo4j_user": "", "neo4j_password": "", "neo4j_database": "",
        "require_password": False, "password": "",
        "app_port": 8001, "close_to_tray": True,
    }
    r = await client.put("/api/settings", json=payload)
    assert r.status_code == 200
    assert r.json()["embedding_base_url"] == "http://127.0.0.1:11434/v1"


@pytest.mark.asyncio
async def test_settings_status_endpoint(client):
    r = await client.get("/api/settings/status")
    assert r.status_code == 200
    data = r.json()
    assert "debug_mode" in data
    assert "app_port" in data
    assert "ocr_available" in data
    assert "bundled_model" in data


@pytest.mark.asyncio
async def test_settings_rejects_invalid_port(client):
    payload = {
        "chat_provider": "deepseek",
        "chat_base_url": "", "chat_api_key": "", "chat_model": "", "chat_thinking": True,
        "embedding_provider": "local", "embedding_model": "BAAI/bge-small-zh-v1.5", "embedding_base_url": "", "embedding_api_key": "",
        "search_provider": "auto", "search_api_key": "", "search_base_url": "",
        "neo4j_enabled": False, "neo4j_uri": "", "neo4j_user": "", "neo4j_password": "", "neo4j_database": "",
        "require_password": False, "password": "",
        "app_port": 80, "close_to_tray": True,
    }
    r = await client.put("/api/settings", json=payload)
    assert r.status_code == 400
