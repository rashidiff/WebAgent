import os
import sqlite3
import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

DB_PATH = os.getenv("AGENT_DB_PATH", os.path.join(os.path.dirname(__file__), "agent_history.db"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL REFERENCES sessions(id),
    action TEXT NOT NULL,
    selector TEXT,
    value TEXT,
    status TEXT NOT NULL,
    detail TEXT,
    created_at TEXT NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db() -> None:
    """Creates the SQLite schema on disk if it doesn't already exist."""
    conn = _get_connection()
    try:
        conn.executescript(_SCHEMA)
        conn.commit()
    finally:
        conn.close()


class HistoryStore:
    """Async-friendly wrapper that persists sessions, messages, and browser actions to SQLite."""

    def __init__(self):
        self.session_id = str(uuid.uuid4())

    async def start_session(self) -> None:
        await asyncio.to_thread(self._start_session_sync)

    def _start_session_sync(self) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO sessions (id, started_at) VALUES (?, ?)",
                (self.session_id, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    async def end_session(self) -> None:
        await asyncio.to_thread(self._end_session_sync)

    def _end_session_sync(self) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?",
                (_now(), self.session_id),
            )
            conn.commit()
        finally:
            conn.close()

    async def log_message(self, role: str, content: str) -> None:
        await asyncio.to_thread(self._log_message_sync, role, content)

    def _log_message_sync(self, role: str, content: str) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO messages (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                (self.session_id, role, content, _now()),
            )
            conn.commit()
        finally:
            conn.close()

    async def log_action(self, action: str, selector: Optional[str], value: Optional[str],
                          status: str, detail: str = "") -> None:
        await asyncio.to_thread(self._log_action_sync, action, selector, value, status, detail)

    def _log_action_sync(self, action: str, selector: Optional[str], value: Optional[str],
                          status: str, detail: str) -> None:
        conn = _get_connection()
        try:
            conn.execute(
                "INSERT INTO actions (session_id, action, selector, value, status, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (self.session_id, action, selector, value, status, detail, _now()),
            )
            conn.commit()
        finally:
            conn.close()


def get_session_history(session_id: str) -> Dict[str, Any]:
    """Returns the persisted messages and actions for a given session id."""
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        messages = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        actions = conn.execute(
            "SELECT action, selector, value, status, detail, created_at FROM actions "
            "WHERE session_id = ? ORDER BY id",
            (session_id,),
        ).fetchall()
        return {
            "messages": [dict(row) for row in messages],
            "actions": [dict(row) for row in actions],
        }
    finally:
        conn.close()


def list_sessions() -> List[Dict[str, Any]]:
    """Returns all recorded sessions, most recent first."""
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, started_at, ended_at FROM sessions ORDER BY started_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()
