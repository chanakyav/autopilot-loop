"""Tests for CLI helpers."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autopilot_loop import persistence
from autopilot_loop.cli import (
    _check_branch_lock,
    _validate_task_id,
    cmd_attach,
    cmd_doctor,
    cmd_fix_ci,
    cmd_restart,
    cmd_resume,
    cmd_stop,
)


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


class TestCmdFixCiErrorHandling:
    def test_none_checks_prints_error_and_exits(self, capsys, monkeypatch):
        """When get_failed_checks returns None, cmd_fix_ci should exit 1 with an error."""
        args = SimpleNamespace(pr=99, model=None, max_iters=None, checks=None)

        # Stub load_config
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda overrides: {"model": "gpt-4", "max_iterations": 5},
        )

        # Stub PR branch lookup
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="some-branch\n", stderr="",
            ),
        )

        # Stub _check_branch_lock to be a no-op
        monkeypatch.setattr("autopilot_loop.cli._check_branch_lock", lambda b: None)

        # get_failed_checks returns None (API error)
        with patch("autopilot_loop.github_api.get_failed_checks", return_value=None):
            with pytest.raises(SystemExit) as exc_info:
                cmd_fix_ci(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "could not fetch CI checks" in captured.err
        assert "gh auth status" in captured.err


class TestCmdStartFollow:
    """Test auto-follow behavior after autopilot start."""

    def _make_args(self, **overrides):
        defaults = {
            "prompt": "test prompt",
            "issue": None,
            "plan": False,
            "model": None,
            "max_iters": None,
            "dry_run": False,
            "no_follow": False,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def _stub_start_deps(self, monkeypatch):
        """Stub all cmd_start dependencies so it doesn't hit real systems."""
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda overrides: {
                "model": "test-model",
                "max_iterations": 3,
                "branch_pattern": "autopilot/{task_id}",
            },
        )
        monkeypatch.setattr("autopilot_loop.cli._detect_autopilot_branch", lambda: None)
        monkeypatch.setattr("autopilot_loop.cli._check_branch_lock", lambda b: None)
        monkeypatch.setattr("autopilot_loop.cli._launch_in_tmux", lambda *a, **kw: None)

    def test_follow_calls_logs_tui(self, monkeypatch):
        """When --no-follow is absent and stdout is a TTY, logs_tui is called."""
        self._stub_start_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        from autopilot_loop.cli import cmd_start
        cmd_start(self._make_args())
        assert len(calls) == 1

    def test_no_follow_skips_logs_tui(self, monkeypatch):
        """When --no-follow is set, logs_tui is NOT called."""
        self._stub_start_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        from autopilot_loop.cli import cmd_start
        cmd_start(self._make_args(no_follow=True))
        assert len(calls) == 0

    def test_non_tty_skips_logs_tui(self, monkeypatch):
        """When stdout is not a TTY, logs_tui is NOT called."""
        self._stub_start_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        from autopilot_loop.cli import cmd_start
        cmd_start(self._make_args())
        assert len(calls) == 0

    def test_dry_run_skips_logs_tui(self, monkeypatch, capsys):
        """When --dry-run is set, logs_tui is NOT called."""
        self._stub_start_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        from autopilot_loop.cli import cmd_start
        cmd_start(self._make_args(dry_run=True))
        assert len(calls) == 0


class TestCmdResumeRepoValidation:
    """Test that cmd_resume rejects PRs from a different repo."""

    def _gh_pr_view_output(self, branch, state, nwo):
        """Build the tsv output that gh pr view would return."""
        return "%s\t%s\t%s\n" % (branch, state, nwo)

    def test_wrong_repo_exits(self, monkeypatch, capsys):
        """cmd_resume should exit 1 when the PR belongs to a different repo."""
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda **kw: {"model": "m", "max_iterations": 3},
        )
        monkeypatch.setattr(
            "autopilot_loop.github_api.get_repo_nwo",
            lambda: "owner/my-repo",
        )

        def fake_run(cmd, **kw):
            if "pr" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout=self._gh_pr_view_output(
                        "some-branch", "OPEN", "other-owner/other-repo",
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = SimpleNamespace(pr=999)
        with pytest.raises(SystemExit) as exc_info:
            cmd_resume(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "other-owner/other-repo" in captured.err
        assert "owner/my-repo" in captured.err

    def test_same_repo_passes_validation(self, monkeypatch):
        """cmd_resume should NOT exit when the PR belongs to the current repo."""
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda **kw: {"model": "m", "max_iterations": 3},
        )
        monkeypatch.setattr(
            "autopilot_loop.github_api.get_repo_nwo",
            lambda: "owner/my-repo",
        )
        monkeypatch.setattr("autopilot_loop.cli._check_branch_lock", lambda b: None)
        monkeypatch.setattr("autopilot_loop.cli._launch_in_tmux", lambda *a, **kw: None)

        def fake_run(cmd, **kw):
            if "pr" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout=self._gh_pr_view_output(
                        "fix/something", "OPEN", "owner/my-repo",
                    ),
                    stderr="",
                )
            # git checkout
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = SimpleNamespace(pr=42, no_follow=True)
        # Should not raise
        cmd_resume(args)

    def test_merged_pr_wrong_repo_exits_with_repo_error(self, monkeypatch, capsys):
        """A merged PR from a different repo should fail on repo mismatch, not state."""
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda **kw: {"model": "m", "max_iterations": 3},
        )
        monkeypatch.setattr(
            "autopilot_loop.github_api.get_repo_nwo",
            lambda: "owner/my-repo",
        )

        def fake_run(cmd, **kw):
            if "pr" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout=self._gh_pr_view_output(
                        "some-branch", "MERGED", "other-owner/other-repo",
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        args = SimpleNamespace(pr=16173)
        with pytest.raises(SystemExit) as exc_info:
            cmd_resume(args)

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        # Should fail on repo mismatch BEFORE checking state
        assert "other-owner/other-repo" in captured.err


class TestCmdAttachTerminalStates:
    """Test that cmd_attach shows helpful messages for terminal-state tasks."""

    def test_stopped_task_no_session_shows_guidance(self, monkeypatch, capsys):
        """STOPPED task with no tmux session shows restart/logs guidance."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="STOPPED", pre_stop_state="WAIT_REVIEW")
        monkeypatch.setattr("autopilot_loop.cli._tmux_session_exists", lambda s: False)

        cmd_attach(SimpleNamespace(task_id=task_id))

        captured = capsys.readouterr()
        assert "STOPPED" in captured.out
        assert "WAIT_REVIEW" in captured.out
        assert "restart" in captured.out
        assert "logs" in captured.out

    def test_complete_task_no_session_shows_pr(self, monkeypatch, capsys):
        """COMPLETE task shows PR number and logs guidance."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="COMPLETE", pr_number=42)
        monkeypatch.setattr("autopilot_loop.cli._tmux_session_exists", lambda s: False)

        cmd_attach(SimpleNamespace(task_id=task_id))

        captured = capsys.readouterr()
        assert "COMPLETE" in captured.out
        assert "PR #42" in captured.out
        assert "logs" in captured.out

    def test_failed_task_no_session_shows_guidance(self, monkeypatch, capsys):
        """FAILED task shows restart and logs guidance."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="FAILED")
        monkeypatch.setattr("autopilot_loop.cli._tmux_session_exists", lambda s: False)

        cmd_attach(SimpleNamespace(task_id=task_id))

        captured = capsys.readouterr()
        assert "FAILED" in captured.out
        assert "restart" in captured.out
        assert "logs" in captured.out

    def test_terminal_task_with_session_still_attaches(self, monkeypatch):
        """COMPLETE task with live tmux session still attempts attach."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="COMPLETE")
        monkeypatch.setattr("autopilot_loop.cli._tmux_session_exists", lambda s: True)

        attach_calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: attach_calls.append(cmd) or subprocess.CompletedProcess(
                args=cmd, returncode=0),
        )

        cmd_attach(SimpleNamespace(task_id=task_id))
        assert any("switch-client" in str(c) for c in attach_calls)

    def test_running_task_attaches_normally(self, monkeypatch):
        """Non-terminal task proceeds to tmux attach."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="IMPLEMENT")

        attach_calls = []
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: attach_calls.append(cmd) or subprocess.CompletedProcess(
                args=cmd, returncode=0),
        )

        cmd_attach(SimpleNamespace(task_id=task_id))
        assert any("switch-client" in str(c) for c in attach_calls)


class TestCmdStopGuidance:
    """Test that cmd_stop shows restart/logs guidance after stopping."""

    def test_stop_prints_guidance(self, monkeypatch, capsys):
        """After stopping, cmd_stop prints restart and logs commands."""
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(task_id, state="IMPLEMENT")
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **kw: subprocess.CompletedProcess(args=cmd, returncode=0),
        )

        cmd_stop(SimpleNamespace(task_id=task_id))

        captured = capsys.readouterr()
        assert "restart" in captured.out
        assert "logs" in captured.out


class TestCmdResumeFollow:
    """Test auto-follow behavior after autopilot resume."""

    def _stub_resume_deps(self, monkeypatch):
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda **kw: {"model": "m", "max_iterations": 3},
        )
        monkeypatch.setattr(
            "autopilot_loop.github_api.get_repo_nwo",
            lambda: "owner/my-repo",
        )
        monkeypatch.setattr("autopilot_loop.cli._check_branch_lock", lambda b: None)
        monkeypatch.setattr("autopilot_loop.cli._launch_in_tmux", lambda *a, **kw: None)

        def fake_run(cmd, **kw):
            if "pr" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0,
                    stdout="fix/something\tOPEN\towner/my-repo\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_follow_calls_logs_tui(self, monkeypatch):
        """Default resume auto-opens log viewer."""
        self._stub_resume_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        cmd_resume(SimpleNamespace(pr=42, no_follow=False))
        assert len(calls) == 1

    def test_no_follow_skips_logs_tui(self, monkeypatch):
        """--no-follow skips log viewer."""
        self._stub_resume_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        cmd_resume(SimpleNamespace(pr=42, no_follow=True))
        assert len(calls) == 0

    def test_non_tty_skips_logs_tui(self, monkeypatch):
        """Non-TTY stdout skips log viewer."""
        self._stub_resume_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: False)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda task_id: calls.append(task_id),
        )

        cmd_resume(SimpleNamespace(pr=42, no_follow=False))
        assert len(calls) == 0


class TestCmdRestartFollow:
    """Test auto-follow behavior after autopilot restart."""

    def _create_stopped_task(self):
        task_id = "abcd1234"
        persistence.create_task(task_id, "test")
        persistence.update_task(
            task_id, state="STOPPED", pre_stop_state="IMPLEMENT", branch="autopilot/abcd1234",
        )
        return task_id

    def _stub_restart_deps(self, monkeypatch):
        monkeypatch.setattr("autopilot_loop.cli._launch_in_tmux", lambda *a, **kw: None)

    def test_follow_calls_logs_tui(self, monkeypatch):
        """Default restart auto-opens log viewer."""
        task_id = self._create_stopped_task()
        self._stub_restart_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda tid: calls.append(tid),
        )

        cmd_restart(SimpleNamespace(task_id=task_id, no_follow=False))
        assert len(calls) == 1

    def test_no_follow_skips_logs_tui(self, monkeypatch):
        """--no-follow skips log viewer."""
        task_id = self._create_stopped_task()
        self._stub_restart_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda tid: calls.append(tid),
        )

        cmd_restart(SimpleNamespace(task_id=task_id, no_follow=True))
        assert len(calls) == 0


class TestCmdFixCiFollow:
    """Test auto-follow behavior after autopilot fix-ci."""

    def _stub_fixci_deps(self, monkeypatch):
        monkeypatch.setattr(
            "autopilot_loop.cli.load_config",
            lambda overrides: {"model": "m", "max_iterations": 3},
        )
        monkeypatch.setattr("autopilot_loop.cli._check_branch_lock", lambda b: None)
        monkeypatch.setattr("autopilot_loop.cli._launch_in_tmux", lambda *a, **kw: None)
        monkeypatch.setattr(
            "autopilot_loop.github_api.get_failed_checks",
            lambda pr: [{"name": "build"}],
        )

        def fake_run(cmd, **kw):
            if "pr" in cmd and "view" in cmd:
                return subprocess.CompletedProcess(
                    args=cmd, returncode=0, stdout="fix-branch\n", stderr="",
                )
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

    def test_follow_calls_logs_tui(self, monkeypatch):
        """Default fix-ci auto-opens log viewer."""
        self._stub_fixci_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda tid: calls.append(tid),
        )

        cmd_fix_ci(SimpleNamespace(pr=10, checks="build", max_iters=None, model=None, no_follow=False))
        assert len(calls) == 1

    def test_no_follow_skips_logs_tui(self, monkeypatch):
        """--no-follow skips log viewer."""
        self._stub_fixci_deps(monkeypatch)
        monkeypatch.setattr("sys.stdout.isatty", lambda: True)

        calls = []
        monkeypatch.setattr(
            "autopilot_loop.dashboard.logs_tui",
            lambda tid: calls.append(tid),
        )

        cmd_fix_ci(SimpleNamespace(pr=10, checks="build", max_iters=None, model=None, no_follow=True))
        assert len(calls) == 0
