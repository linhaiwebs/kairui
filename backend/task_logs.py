"""
Task log store for GMC automation (SQLite-backed, safe across gunicorn workers).

Tables:
  - task_sessions(task_id, task_type, site_id, status, result_json, created_at, updated_at)
  - task_log_entries(task_id, log_index, timestamp, level, message, step)

Frontend polls GET /api/tasks/<task_id>/logs?after=<last_index>
"""

import json as _json
import time
from datetime import datetime

from models import get_db


def create_task(task_type: str, site_id: int) -> str:
    """Create a new task session, return its ID."""
    task_id = f"{task_type}-{site_id}-{int(time.time() * 1000)}"
    now = datetime.now().isoformat()
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO task_sessions (task_id, task_type, site_id, status, result_json, created_at, updated_at) "
            "VALUES (?, ?, ?, 'running', '', ?, ?)",
            (task_id, task_type, site_id, now, now),
        )
        conn.commit()
    finally:
        conn.close()
    return task_id


def add_log(task_id: str, level: str, message: str, step: str = ""):
    """Append a log entry (committed immediately for cross-worker visibility)."""
    conn = get_db()
    try:
        # Get next log_index — count existing entries for this task
        row = conn.execute(
            "SELECT COALESCE(MAX(log_index), -1) + 1 FROM task_log_entries WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        idx = row[0]
        conn.execute(
            "INSERT INTO task_log_entries (task_id, log_index, timestamp, level, message, step) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (task_id, idx, datetime.now().strftime("%H:%M:%S"), level, message, step),
        )
        # Touch session updated_at
        conn.execute(
            "UPDATE task_sessions SET updated_at = ? WHERE task_id = ?",
            (datetime.now().isoformat(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def complete_task(task_id: str, success: bool, result: dict = None):
    """Mark a task as completed with its result."""
    conn = get_db()
    try:
        conn.execute(
            "UPDATE task_sessions SET status = ?, result_json = ?, updated_at = ? WHERE task_id = ?",
            ("success" if success else "failed", _json.dumps(result or {}), datetime.now().isoformat(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_task_logs(task_id: str, after: int = 0):
    """
    Return (new_logs, status, result_json_parsed) for the given task.
    after: only return logs with log_index >= after.
    """
    conn = get_db()
    try:
        # Look up session
        sess = conn.execute(
            "SELECT status, result_json FROM task_sessions WHERE task_id = ?",
            (task_id,),
        ).fetchone()
        if sess is None:
            return [], "not_found", None

        # Fetch new log entries
        rows = conn.execute(
            "SELECT log_index AS i, timestamp AS t, level, message AS msg, step "
            "FROM task_log_entries WHERE task_id = ? AND log_index >= ? ORDER BY log_index",
            (task_id, after),
        ).fetchall()

        logs = [dict(r) for r in rows]
        result = _json.loads(sess["result_json"]) if sess["result_json"] else None
        return logs, sess["status"], result
    finally:
        conn.close()
