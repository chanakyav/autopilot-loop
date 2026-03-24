"""Tests for SQLite persistence layer."""

import json
import os
import time

import pytest

from autopilot_loop import persistence


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))


def test_create_and_get_task():
    persistence.create_task("abc123", "test prompt", max_iterations=3, model="test-model")
    task = persistence.get_task("abc123")
    assert task is not None
    assert task["id"] == "abc123"
    assert task["prompt"] == "test prompt"
    assert task["state"] == "INIT"
    assert task["max_iterations"] == 3
    assert task["model"] == "test-model"
    assert task["iteration"] == 0
    assert task["pr_number"] is None


def test_get_nonexistent_task():
    assert persistence.get_task("nonexistent") is None


def test_update_task():
    persistence.create_task("t1", "prompt")
    persistence.update_task("t1", state="IMPLEMENT", pr_number=42, branch="autopilot/t1")
    task = persistence.get_task("t1")
    assert task["state"] == "IMPLEMENT"
    assert task["pr_number"] == 42
    assert task["branch"] == "autopilot/t1"


def test_list_tasks():
    persistence.create_task("t1", "first")
    time.sleep(0.01)
    persistence.create_task("t2", "second")
    tasks = persistence.list_tasks()
    assert len(tasks) == 2
    # Newest first
    assert tasks[0]["id"] == "t2"
    assert tasks[1]["id"] == "t1"


def test_list_tasks_limit():
    for i in range(5):
        persistence.create_task("t%d" % i, "prompt %d" % i)
        time.sleep(0.01)
    tasks = persistence.list_tasks(limit=2)
    assert len(tasks) == 2


def test_save_and_get_review():
    persistence.create_task("t1", "prompt")
    comments = [{"path": "a.rb", "body": "fix this"}]
    persistence.save_review("t1", 1, "review body", comments)
    reviews = persistence.get_reviews("t1")
    assert len(reviews) == 1
    assert reviews[0]["task_id"] == "t1"
    assert reviews[0]["iteration"] == 1
    assert reviews[0]["body"] == "review body"
    assert json.loads(reviews[0]["comments_json"]) == comments


def test_save_and_get_agent_run():
    persistence.create_task("t1", "prompt")
    now = time.time()
    persistence.save_agent_run("t1", "IMPLEMENT", now, now + 60, exit_code=0, session_file="/tmp/s.md")
    runs = persistence.get_agent_runs("t1")
    assert len(runs) == 1
    assert runs[0]["phase"] == "IMPLEMENT"
    assert runs[0]["exit_code"] == 0


def test_get_sessions_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    sessions_dir = persistence.get_sessions_dir("t1")
    assert os.path.isdir(sessions_dir)
    assert sessions_dir.endswith("sessions/t1")


def test_create_task_with_flags():
    persistence.create_task("t1", "prompt", plan_mode=True, dry_run=True)
    task = persistence.get_task("t1")
    assert task["plan_mode"] == 1
    assert task["dry_run"] == 1


def test_stopped_state_persisted():
    persistence.create_task("t1", "prompt")
    persistence.update_task("t1", state="STOPPED", pre_stop_state="FIX")
    task = persistence.get_task("t1")
    assert task["state"] == "STOPPED"
    assert task["pre_stop_state"] == "FIX"


def test_existing_branch_persisted():
    persistence.create_task("t1", "prompt")
    persistence.update_task("t1", existing_branch=1)
    task = persistence.get_task("t1")
    assert task["existing_branch"] == 1


def test_existing_branch_defaults_to_zero():
    persistence.create_task("t1", "prompt")
    task = persistence.get_task("t1")
    assert task["existing_branch"] == 0


def test_get_active_tasks():
    persistence.create_task("t1", "prompt")
    persistence.create_task("t2", "prompt")
    persistence.create_task("t3", "prompt")
    persistence.update_task("t1", state="IMPLEMENT", branch="autopilot/t1")
    persistence.update_task("t2", state="COMPLETE", branch="autopilot/t2")
    persistence.update_task("t3", state="FIX", branch="autopilot/t3")
    active = persistence.get_active_tasks()
    ids = [t["id"] for t in active]
    assert "t1" in ids
    assert "t3" in ids
    assert "t2" not in ids


def test_get_active_tasks_excludes_stopped_and_failed():
    persistence.create_task("t1", "prompt")
    persistence.create_task("t2", "prompt")
    persistence.update_task("t1", state="STOPPED")
    persistence.update_task("t2", state="FAILED")
    active = persistence.get_active_tasks()
    assert len(active) == 0


def test_get_tasks_on_branch():
    persistence.create_task("t1", "prompt")
    persistence.create_task("t2", "prompt")
    persistence.update_task("t1", state="IMPLEMENT", branch="autopilot/shared")
    persistence.update_task("t2", state="FIX", branch="autopilot/other")
    tasks = persistence.get_tasks_on_branch("autopilot/shared")
    assert len(tasks) == 1
    assert tasks[0]["id"] == "t1"


def test_get_tasks_on_branch_excludes_terminal():
    persistence.create_task("t1", "prompt")
    persistence.update_task("t1", state="COMPLETE", branch="autopilot/done")
    tasks = persistence.get_tasks_on_branch("autopilot/done")
    assert len(tasks) == 0


def test_terminal_states_constant():
    """TERMINAL_STATES is defined in persistence and contains the expected values."""
    assert "COMPLETE" in persistence.TERMINAL_STATES
    assert "FAILED" in persistence.TERMINAL_STATES
    assert "STOPPED" in persistence.TERMINAL_STATES
    assert len(persistence.TERMINAL_STATES) == 3


def test_last_review_id_persisted():
    persistence.create_task("t1", "prompt")
    task = persistence.get_task("t1")
    assert task["last_review_id"] is None
    persistence.update_task("t1", last_review_id=12345)
    task = persistence.get_task("t1")
    assert task["last_review_id"] == 12345


def test_migration_from_pre_versioned_db(tmp_path, monkeypatch):
    """Simulate upgrading a DB created before the versioning system existed."""
    import sqlite3

    db_path = str(tmp_path / "state.db")
    monkeypatch.setattr(persistence, "DB_PATH", db_path)

    # Create a minimal v1-era DB (no schema_meta, no last_review_id, no task_mode)
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE tasks (
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
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    now = time.time()
    conn.execute(
        "INSERT INTO tasks (id, prompt, state, created_at, updated_at) VALUES (?, ?, 'INIT', ?, ?)",
        ("old1", "old task", now, now),
    )
    conn.commit()
    conn.close()

    # Now open via _get_db — should migrate and add new columns
    task = persistence.get_task("old1")
    assert task is not None
    assert task["id"] == "old1"
    assert task["last_review_id"] is None
    assert task["task_mode"] == "review"
    assert task["ci_check_names"] is None
    assert task["pre_stop_state"] is None
    assert task["existing_branch"] == 0
    assert task["original_idle_timeout"] is None
    assert task["prompt_file"] is None

    # New columns should be usable
    persistence.update_task("old1", task_mode="ci", ci_check_names='["check-a"]')
    task = persistence.get_task("old1")
    assert task["task_mode"] == "ci"
    assert task["ci_check_names"] == '["check-a"]'


def test_original_idle_timeout_persists(tmp_path, monkeypatch):
    """original_idle_timeout column can be written and read back."""
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))

    persistence.create_task("t1", "prompt")
    task = persistence.get_task("t1")
    assert task["original_idle_timeout"] is None

    persistence.update_task("t1", original_idle_timeout=30)
    task = persistence.get_task("t1")
    assert task["original_idle_timeout"] == 30


def test_prompt_file_persists(tmp_path, monkeypatch):
    """prompt_file column can be written and read back."""
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))

    persistence.create_task("t1", "prompt")
    task = persistence.get_task("t1")
    assert task["prompt_file"] is None

    persistence.update_task("t1", prompt_file="/tmp/my-task.txt")
    task = persistence.get_task("t1")
    assert task["prompt_file"] == "/tmp/my-task.txt"
