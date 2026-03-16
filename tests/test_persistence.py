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


def test_last_review_id_persisted():
    persistence.create_task("t1", "prompt")
    task = persistence.get_task("t1")
    assert task["last_review_id"] is None
    persistence.update_task("t1", last_review_id=12345)
    task = persistence.get_task("t1")
    assert task["last_review_id"] == 12345
