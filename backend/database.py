from pathlib import Path
import sqlite3
import asyncio
import uuid
from datetime import datetime, timezone
import json
from typing import Any, Dict, List, Optional

from backend.settings import get_settings

_DEFAULT_DB_PATH = get_settings().agent_db_path or str(Path(__file__).with_name("agent_history.db"))
DB_PATH = _DEFAULT_DB_PATH

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

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    session_id TEXT REFERENCES sessions(id),
    prompt TEXT NOT NULL,
    status TEXT NOT NULL,
    plan TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT
);

CREATE TABLE IF NOT EXISTS run_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    step_index INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT,
    action TEXT,
    selector TEXT,
    value TEXT,
    url TEXT,
    page_title TEXT,
    dom_summary TEXT,
    screenshot_before TEXT,
    screenshot_after TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prompt_template TEXT NOT NULL,
    steps_json TEXT NOT NULL,
    source_run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
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
        _ensure_column(conn, "runs", "plan", "TEXT")
        _ensure_column(conn, "run_steps", "metadata_json", "TEXT")
        conn.commit()
    finally:
        conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, column_type: str) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in existing:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_type}")


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


def create_run(run_id: str, prompt: str, session_id: str | None = None, status: str = "running", plan: str = "") -> Dict[str, Any]:
    conn = _get_connection()
    try:
        now = _now()
        conn.execute(
            "INSERT INTO runs (id, session_id, prompt, status, plan, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, session_id, prompt, status, plan, now),
        )
        conn.commit()
        return {"id": run_id, "session_id": session_id, "prompt": prompt, "status": status, "plan": plan, "started_at": now, "ended_at": None}
    finally:
        conn.close()


def update_run_status(run_id: str, status: str, plan: str | None = None) -> bool:
    conn = _get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not existing:
            return False
        if plan is None:
            conn.execute("UPDATE runs SET status = ?, ended_at = ? WHERE id = ?", (status, _now(), run_id))
        else:
            conn.execute("UPDATE runs SET status = ?, plan = ?, ended_at = ? WHERE id = ?", (status, plan, _now(), run_id))
        conn.commit()
        return True
    finally:
        conn.close()


def add_run_step(
    run_id: str,
    event_type: str,
    title: str,
    detail: str = "",
    action: str | None = None,
    selector: str | None = None,
    value: str | None = None,
    url: str | None = None,
    page_title: str | None = None,
    dom_summary: str | None = None,
    screenshot_before: str | None = None,
    screenshot_after: str | None = None,
    metadata: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT COALESCE(MAX(step_index), 0) + 1 AS next_index FROM run_steps WHERE run_id = ?", (run_id,)).fetchone()
        step_index = int(row["next_index"])
        now = _now()
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        cur = conn.execute(
            "INSERT INTO run_steps "
            "(run_id, step_index, event_type, title, detail, action, selector, value, url, page_title, dom_summary, "
            "screenshot_before, screenshot_after, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                step_index,
                event_type,
                title,
                detail,
                action,
                selector,
                value,
                url,
                page_title,
                dom_summary,
                screenshot_before,
                screenshot_after,
                metadata_json,
                now,
            ),
        )
        conn.commit()
        return {
            "id": cur.lastrowid,
            "run_id": run_id,
            "step_index": step_index,
            "event_type": event_type,
            "title": title,
            "detail": detail,
            "action": action,
            "selector": selector,
            "value": value,
            "url": url,
            "page_title": page_title,
            "dom_summary": dom_summary,
            "screenshot_before": screenshot_before,
            "screenshot_after": screenshot_after,
            "metadata": metadata or {},
            "created_at": now,
        }
    finally:
        conn.close()


def list_runs(limit: int = 20, offset: int = 0) -> List[Dict[str, Any]]:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, session_id, prompt, status, plan, started_at, ended_at FROM runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_runs() -> int:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()


def get_run(run_id: str) -> Dict[str, Any] | None:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        run = conn.execute(
            "SELECT id, session_id, prompt, status, plan, started_at, ended_at FROM runs WHERE id = ?",
            (run_id,),
        ).fetchone()
        if not run:
            return None
        steps = conn.execute(
            "SELECT id, run_id, step_index, event_type, title, detail, action, selector, value, url, page_title, "
            "dom_summary, screenshot_before, screenshot_after, metadata_json, created_at "
            "FROM run_steps WHERE run_id = ? ORDER BY step_index",
            (run_id,),
        ).fetchall()
        payload = dict(run)
        payload["steps"] = [_step_row_to_dict(row) for row in steps]
        return payload
    finally:
        conn.close()


def delete_run(run_id: str) -> bool:
    conn = _get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM runs WHERE id = ?", (run_id,)).fetchone()
        if not existing:
            return False
        conn.execute("DELETE FROM run_steps WHERE run_id = ?", (run_id,))
        conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def create_workflow(name: str, prompt_template: str, steps: List[Dict[str, Any]], source_run_id: str | None = None) -> Dict[str, Any]:
    conn = _get_connection()
    try:
        workflow_id = str(uuid.uuid4())
        now = _now()
        steps_json = json.dumps(steps, ensure_ascii=False, sort_keys=True)
        conn.execute(
            "INSERT INTO workflows (id, name, prompt_template, steps_json, source_run_id, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (workflow_id, name, prompt_template, steps_json, source_run_id, now, now),
        )
        conn.commit()
        return {
            "id": workflow_id,
            "name": name,
            "prompt_template": prompt_template,
            "steps": steps,
            "source_run_id": source_run_id,
            "created_at": now,
            "updated_at": now,
        }
    finally:
        conn.close()


def list_workflows() -> List[Dict[str, Any]]:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, name, prompt_template, steps_json, source_run_id, created_at, updated_at FROM workflows ORDER BY updated_at DESC"
        ).fetchall()
        return [_workflow_row_to_dict(row) for row in rows]
    finally:
        conn.close()


def get_workflow(workflow_id: str) -> Dict[str, Any] | None:
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, name, prompt_template, steps_json, source_run_id, created_at, updated_at FROM workflows WHERE id = ?",
            (workflow_id,),
        ).fetchone()
        return _workflow_row_to_dict(row) if row else None
    finally:
        conn.close()


def delete_workflow(workflow_id: str) -> bool:
    conn = _get_connection()
    try:
        cur = conn.execute("DELETE FROM workflows WHERE id = ?", (workflow_id,))
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()


def _step_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    metadata_json = item.pop("metadata_json", None)
    try:
        item["metadata"] = json.loads(metadata_json or "{}")
    except json.JSONDecodeError:
        item["metadata"] = {}
    return item


def _workflow_row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    steps_json = item.pop("steps_json", "[]")
    try:
        item["steps"] = json.loads(steps_json or "[]")
    except json.JSONDecodeError:
        item["steps"] = []
    return item


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
            "session_id": session_id,
            "messages": [dict(row) for row in messages],
            "actions": [dict(row) for row in actions],
        }
    finally:
        conn.close()


def delete_session(session_id: str) -> bool:
    """Deletes one recorded session and its child records."""
    conn = _get_connection()
    try:
        existing = conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if not existing:
            return False
        conn.execute("DELETE FROM actions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        conn.commit()
        return True
    finally:
        conn.close()


def clear_sessions() -> int:
    """Deletes all recorded sessions and returns the number removed."""
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        deleted = int(row[0] if row else 0)
        conn.execute("DELETE FROM actions")
        conn.execute("DELETE FROM messages")
        conn.execute("DELETE FROM sessions")
        conn.commit()
        return deleted
    finally:
        conn.close()


def list_sessions(limit: int = 50, offset: int = 0) -> List[Dict[str, Any]]:
    """Returns recorded sessions ordered by most recent first."""
    conn = _get_connection()
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, started_at, ended_at FROM sessions ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def count_sessions() -> int:
    conn = _get_connection()
    try:
        row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
        return int(row[0] if row else 0)
    finally:
        conn.close()
