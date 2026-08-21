import os, json, sqlite3, threading
from pathlib import Path

DB_PATH = Path(os.environ.get("KB_DATA_DIR", "./data")) / "chat_history.db"
_local = threading.local()

def _get_conn():
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH))
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("CREATE TABLE IF NOT EXISTS history (session_id TEXT PRIMARY KEY, data TEXT, updated_at REAL)")
        _local.conn.execute("CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, title TEXT, created_at REAL, user_id TEXT)")
        _local.conn.commit()
    return _local.conn

def save_history(session_id: str, history: list):
    conn = _get_conn()
    conn.execute("INSERT OR REPLACE INTO history (session_id, data, updated_at) VALUES (?,?,?)",
                 (session_id, json.dumps(history, ensure_ascii=False), __import__("time").time()))
    conn.commit()

def load_history(session_id: str) -> list:
    conn = _get_conn()
    row = conn.execute("SELECT data FROM history WHERE session_id = ?", (session_id,)).fetchone()
    if row:
        return json.loads(row[0])
    return []

def delete_history(session_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM history WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.commit()

def save_session(session_id: str, title: str, user_id: str = "default"):
    conn = _get_conn()
    conn.execute("INSERT OR REPLACE INTO sessions (id, title, created_at, user_id) VALUES (?,?,?,?)",
                 (session_id, title, __import__("time").time(), user_id))
    conn.commit()

def get_session_title(session_id: str) -> str:
    """返回当前会话标题；未设置或仍是占位符时返回空字符串。"""
    conn = _get_conn()
    row = conn.execute("SELECT title FROM sessions WHERE id = ?", (session_id,)).fetchone()
    title = row[0] if row else ""
    return "" if title == "???" else (title or "")

def list_sessions(user_id: str = "default") -> list:
    conn = _get_conn()
    rows = conn.execute("SELECT id, title, created_at FROM sessions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)).fetchall()
    return [{"id": r[0], "title": "" if r[1] == "???" else (r[1] or ""), "created_at": r[2]} for r in rows]

def load_history_paginated(session_id: str, offset: int, limit: int) -> list:
    """按偏移量/数量加载历史记录，用于无限滚动。返回 (消息列表, 是否还有更多)。"""
    conn = _get_conn()
    row = conn.execute("SELECT data FROM history WHERE session_id = ?", (session_id,)).fetchone()
    if not row:
        return [], False
    full = json.loads(row[0])
    total = len(full)
    start = max(0, total - offset - limit)
    end = total - offset
    chunk = full[start:end]
    has_more = start > 0
    return chunk, has_more


def delete_session(session_id: str):
    conn = _get_conn()
    conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
    conn.execute("DELETE FROM history WHERE session_id = ?", (session_id,))
    conn.commit()
