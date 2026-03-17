"""Tests for CLI helpers."""

import pytest

from autopilot_loop import persistence
from autopilot_loop.cli import _check_branch_lock, _validate_task_id


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    """Use a temporary database for each test."""
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))


class TestValidateTaskId:
    def test_valid_hex_id(self):
        # Should not raise
        _validate_task_id("a1b2c3d4")

    def test_valid_all_digits(self):
        _validate_task_id("12345678")

    def test_valid_all_letters(self):
        _validate_task_id("abcdef01")

    def test_invalid_too_short(self):
        with pytest.raises(SystemExit):
            _validate_task_id("abc")

    def test_invalid_too_long(self):
        with pytest.raises(SystemExit):
            _validate_task_id("a1b2c3d4e5")

    def test_invalid_uppercase(self):
        with pytest.raises(SystemExit):
            _validate_task_id("A1B2C3D4")

    def test_invalid_non_hex(self):
        with pytest.raises(SystemExit):
            _validate_task_id("ghijklmn")

    def test_invalid_empty(self):
        with pytest.raises(SystemExit):
            _validate_task_id("")


class TestCheckBranchLock:
    def test_no_conflict_passes(self):
        # No tasks on this branch — should not raise
        _check_branch_lock("autopilot/new-branch")

    def test_conflict_exits(self):
        persistence.create_task("t1", "prompt")
        persistence.update_task("t1", state="IMPLEMENT", branch="autopilot/locked")
        with pytest.raises(SystemExit):
            _check_branch_lock("autopilot/locked")

    def test_terminal_task_no_conflict(self):
        persistence.create_task("t1", "prompt")
        persistence.update_task("t1", state="COMPLETE", branch="autopilot/done")
        # Completed task should not block — should not raise
        _check_branch_lock("autopilot/done")

    def test_stopped_task_no_conflict(self):
        persistence.create_task("t1", "prompt")
        persistence.update_task("t1", state="STOPPED", branch="autopilot/stopped")
        _check_branch_lock("autopilot/stopped")

    def test_failed_task_no_conflict(self):
        persistence.create_task("t1", "prompt")
        persistence.update_task("t1", state="FAILED", branch="autopilot/failed")
        _check_branch_lock("autopilot/failed")
