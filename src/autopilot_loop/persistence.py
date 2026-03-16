"""SQLite persistence for task state, reviews, and agent runs.

Database lives at ~/.autopilot-loop/state.db.
"""

import json
import logging
import os
import sqlite3
import time

logger = logging.getLogger(__name__)

__all__ = [
    "create_task",
    "get_task",
    "update_task",
    "list_tasks",
    "save_review",
    "get_reviews",
    "save_agent_run",
    "get_agent_runs",
    "get_sessions_dir",
]

DB_DIR = os.path.join(os.path.expanduser("~"), ".autopilot-loop")
DB_PATH = os.path.join(DB_DIR, "state.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    prompt TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'INIT',
    pr_number INTEGER,
    branch TEXT,
    iteration INTEGER NOT NULL DEFAULT 0,
    max_iterations INTEGER NOT NULL DEFAULT 5,
    plan_mode INTEGER NOT NULL DEFAULT 0,
    dry_run INTEGER NOT NULL DEFAULT 0,
    model TEXT,
    last_review_id INTEGER,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    iteration INTEGER NOT NULL,
    body TEXT,
    comments_json TEXT,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    phase TEXT NOT NULL,
    started_at REAL NOT NULL,
    ended_at REAL,
    exit_code INTEGER,
    session_file TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);
"""


def _get_db():
    """Get a connection to the SQLite database, creating it if needed."""
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def create_task(task_id, prompt, max_iterations=5, plan_mode=False, dry_run=False, model=None):
    """Create a new task record."""
    now = time.time()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO tasks "
            "(id, prompt, state, iteration, max_iterations, plan_mode, dry_run, model, created_at, updated_at) "
            "VALUES (?, ?, 'INIT', 0, ?, ?, ?, ?, ?, ?)",
            (task_id, prompt, max_iterations, int(plan_mode), int(dry_run), model, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_task(task_id):
    """Get a task by ID. Returns a dict or None."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


_TASK_COLUMNS = frozenset({
    "prompt", "state", "pr_number", "branch", "iteration",
    "max_iterations", "plan_mode", "dry_run", "model", "last_review_id", "updated_at",
})


def update_task(task_id, **kwargs):
    """Update task fields. Pass column=value pairs."""
    if not kwargs:
        return
    kwargs["updated_at"] = time.time()
    invalid = set(kwargs) - _TASK_COLUMNS
    if invalid:
        raise ValueError("Invalid task columns: %s" % ", ".join(sorted(invalid)))
    set_clause = ", ".join("%s = ?" % k for k in kwargs)
    values = list(kwargs.values()) + [task_id]
    conn = _get_db()
    try:
        conn.execute("UPDATE tasks SET %s WHERE id = ?" % set_clause, values)
        conn.commit()
    finally:
        conn.close()


def list_tasks(limit=20):
    """List recent tasks, newest first."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_review(task_id, iteration, body, comments):
    """Save a review (body + inline comments as JSON)."""
    now = time.time()
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO reviews (task_id, iteration, body, comments_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (task_id, iteration, body, json.dumps(comments), now),
        )
        conn.commit()
    finally:
        conn.close()


def get_reviews(task_id):
    """Get all reviews for a task."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM reviews WHERE task_id = ? ORDER BY iteration", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def save_agent_run(task_id, phase, started_at, ended_at=None, exit_code=None, session_file=None, retry_count=0):
    """Record an agent run."""
    conn = _get_db()
    try:
        conn.execute(
            "INSERT INTO agent_runs (task_id, phase, started_at, ended_at, exit_code, session_file, retry_count) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, phase, started_at, ended_at, exit_code, session_file, retry_count),
        )
        conn.commit()
    finally:
        conn.close()


def get_agent_runs(task_id):
    """Get all agent runs for a task."""
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM agent_runs WHERE task_id = ? ORDER BY started_at", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_sessions_dir(task_id):
    """Return the session directory path for a task, creating it if needed."""
    sessions_dir = os.path.join(DB_DIR, "sessions", task_id)
    os.makedirs(sessions_dir, exist_ok=True)
    return sessions_dir
