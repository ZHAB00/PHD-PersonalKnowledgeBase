import os
import sys

os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())

import pytest
from httpx import AsyncClient, ASGITransport

from app.main import app
from app.core.user_settings import chat_config, save_settings, UserSettings


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
