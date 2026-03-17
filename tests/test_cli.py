"""Tests for CLI helpers."""

import subprocess

import pytest

from autopilot_loop import persistence
from autopilot_loop.cli import _check_branch_lock, _validate_task_id, cmd_doctor


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


class TestCmdDoctor:
    def _which(self, available):
        """Return a shutil.which replacement that knows about *available* tools."""
        def fake_which(name):
            return "/usr/bin/%s" % name if name in available else None
        return fake_which

    def _run_ok(self, *a, **kw):
        return subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")

    def _run_fail(self, *a, **kw):
        raise subprocess.CalledProcessError(1, "cmd")

    def test_all_checks_pass(self, monkeypatch, capsys):
        """All tools present, authed, inside a git repo."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "git", "tmux"}))
        monkeypatch.setattr(subprocess, "run", self._run_ok)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        cmd_doctor(None)
        out = capsys.readouterr().out
        assert "All checks passed" in out
        assert "local workspace" in out

    def test_codespace_detected(self, monkeypatch, capsys):
        """CODESPACE_NAME env var is reported."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "git", "tmux"}))
        monkeypatch.setattr(subprocess, "run", self._run_ok)
        monkeypatch.setenv("CODESPACE_NAME", "my-codespace")

        cmd_doctor(None)
        out = capsys.readouterr().out
        assert "my-codespace" in out

    def test_copilot_missing_fails(self, monkeypatch, capsys):
        """Missing copilot CLI causes exit 1."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"gh", "git", "tmux"}))
        monkeypatch.setattr(subprocess, "run", self._run_ok)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        with pytest.raises(SystemExit):
            cmd_doctor(None)
        out = capsys.readouterr().out
        assert "copilot CLI" in out

    def test_gh_not_authed_fails(self, monkeypatch, capsys):
        """gh present but not authenticated causes exit 1."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "git", "tmux"}))

        def selective_run(cmd, **kw):
            if "auth" in cmd:
                raise subprocess.CalledProcessError(1, "gh auth status")
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(subprocess, "run", selective_run)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        with pytest.raises(SystemExit):
            cmd_doctor(None)
        out = capsys.readouterr().out
        assert "not authenticated" in out

    def test_git_missing_fails(self, monkeypatch, capsys):
        """Missing git causes exit 1."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "tmux"}))
        monkeypatch.setattr(subprocess, "run", self._run_ok)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        with pytest.raises(SystemExit):
            cmd_doctor(None)
        out = capsys.readouterr().out
        assert "git" in out

    def test_tmux_missing_is_warning(self, monkeypatch, capsys):
        """Missing tmux does NOT cause exit 1 (it's optional)."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "git"}))
        monkeypatch.setattr(subprocess, "run", self._run_ok)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        cmd_doctor(None)
        out = capsys.readouterr().out
        assert "All checks passed" in out
        assert "optional" in out

    def test_not_in_git_repo_fails(self, monkeypatch, capsys):
        """Inside no git repo causes exit 1."""
        import shutil as _shutil
        monkeypatch.setattr(_shutil, "which", self._which({"copilot", "gh", "git", "tmux"}))

        def selective_run(cmd, **kw):
            if "rev-parse" in cmd:
                raise subprocess.CalledProcessError(128, "git rev-parse")
            return subprocess.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(subprocess, "run", selective_run)
        monkeypatch.delenv("CODESPACE_NAME", raising=False)

        with pytest.raises(SystemExit):
            cmd_doctor(None)
        out = capsys.readouterr().out
        assert "not inside a git repository" in out
