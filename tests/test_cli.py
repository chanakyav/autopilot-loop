"""Tests for CLI helpers."""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from autopilot_loop import persistence
from autopilot_loop.cli import (
    _add_to_git_exclude,
    _check_branch_lock,
    _parse_issue_arg,
    _validate_task_id,
    cmd_doctor,
    cmd_fix_ci,
    cmd_resume,
    cmd_start,
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
            "file": None,
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
        monkeypatch.setattr("autopilot_loop.cli._add_to_git_exclude", lambda f: None)

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

        args = SimpleNamespace(pr=42)
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


class TestParseIssueArg:
    """Tests for _parse_issue_arg: plain numbers and full URLs."""

    def test_plain_number(self):
        num, repo = _parse_issue_arg("123")
        assert num == 123
        assert repo is None

    def test_full_url(self):
        num, repo = _parse_issue_arg("https://github.com/org/design-docs/issues/45")
        assert num == 45
        assert repo == "org/design-docs"

    def test_https_url(self):
        num, repo = _parse_issue_arg("https://github.com/owner/repo/issues/1")
        assert num == 1
        assert repo == "owner/repo"

    def test_invalid_string_exits(self):
        with pytest.raises(SystemExit):
            _parse_issue_arg("not-a-number-or-url")

    def test_invalid_url_wrong_path_exits(self):
        with pytest.raises(SystemExit):
            _parse_issue_arg("https://github.com/owner/repo/pull/123")

    def test_empty_string_exits(self):
        with pytest.raises(SystemExit):
            _parse_issue_arg("")


class TestCmdStartFile:
    """Tests for --file flag in cmd_start."""

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
        monkeypatch.setattr("autopilot_loop.cli._add_to_git_exclude", lambda f: None)

    def _make_args(self, **overrides):
        defaults = {
            "prompt": None,
            "issue": None,
            "file": None,
            "plan": False,
            "model": None,
            "max_iters": None,
            "dry_run": False,
            "no_follow": True,
        }
        defaults.update(overrides)
        return SimpleNamespace(**defaults)

    def test_file_reads_prompt(self, tmp_path, monkeypatch):
        """--file reads the file contents as the prompt."""
        self._stub_start_deps(monkeypatch)
        prompt_file = tmp_path / "task.txt"
        prompt_file.write_text("Implement feature X with tests")

        cmd_start(self._make_args(file=str(prompt_file)))

        # Verify task was created with file contents as prompt
        tasks = persistence.list_tasks(limit=1)
        assert len(tasks) == 1
        assert tasks[0]["prompt"] == "Implement feature X with tests"
        assert tasks[0]["prompt_file"] == str(prompt_file)

    def test_file_not_found_exits(self, monkeypatch):
        """--file with nonexistent file exits with error."""
        self._stub_start_deps(monkeypatch)
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(file="/nonexistent/path.txt"))

    def test_empty_file_exits(self, tmp_path, monkeypatch):
        """--file with empty file exits with error."""
        self._stub_start_deps(monkeypatch)
        empty_file = tmp_path / "empty.txt"
        empty_file.write_text("")
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(file=str(empty_file)))

    def test_whitespace_only_file_exits(self, tmp_path, monkeypatch):
        """--file with whitespace-only file exits with error."""
        self._stub_start_deps(monkeypatch)
        ws_file = tmp_path / "whitespace.txt"
        ws_file.write_text("   \n\n  \t  ")
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(file=str(ws_file)))

    def test_mutual_exclusion_file_and_prompt(self, monkeypatch):
        """--file and --prompt together should exit with error."""
        self._stub_start_deps(monkeypatch)
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(file="some.txt", prompt="inline prompt"))

    def test_mutual_exclusion_file_and_issue(self, monkeypatch):
        """--file and --issue together should exit with error."""
        self._stub_start_deps(monkeypatch)
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(file="some.txt", issue="123"))

    def test_mutual_exclusion_prompt_and_issue(self, monkeypatch):
        """--prompt and --issue together should exit with error."""
        self._stub_start_deps(monkeypatch)
        with pytest.raises(SystemExit):
            cmd_start(self._make_args(prompt="inline", issue="123"))

    def test_no_source_exits(self, monkeypatch):
        """No --prompt, --issue, or --file exits with error."""
        self._stub_start_deps(monkeypatch)
        with pytest.raises(SystemExit):
            cmd_start(self._make_args())

    def test_issue_url_resolves(self, monkeypatch):
        """--issue with a full URL fetches from the correct repo."""
        self._stub_start_deps(monkeypatch)

        calls = []

        def fake_get_issue(num, repo=None):
            calls.append((num, repo))
            return {"title": "Test Issue", "body": "Issue body"}

        monkeypatch.setattr("autopilot_loop.github_api.get_issue", fake_get_issue)

        cmd_start(self._make_args(issue="https://github.com/org/docs/issues/99"))

        assert len(calls) == 1
        assert calls[0] == (99, "org/docs")

        tasks = persistence.list_tasks(limit=1)
        assert "org/docs#99" in tasks[0]["prompt"]

    def test_issue_plain_number(self, monkeypatch):
        """--issue with a plain number fetches from local repo."""
        self._stub_start_deps(monkeypatch)

        calls = []

        def fake_get_issue(num, repo=None):
            calls.append((num, repo))
            return {"title": "Local Issue", "body": "Body text"}

        monkeypatch.setattr("autopilot_loop.github_api.get_issue", fake_get_issue)

        cmd_start(self._make_args(issue="42"))

        assert len(calls) == 1
        assert calls[0] == (42, None)


class TestAddToGitExclude:
    """Tests for _add_to_git_exclude."""

    def test_adds_path(self, tmp_path, monkeypatch):
        """Adds file path to .git/info/exclude."""
        git_dir = tmp_path / ".git" / "info"
        git_dir.mkdir(parents=True)
        exclude_file = git_dir / "exclude"

        # Stub git rev-parse to return tmp_path as repo root
        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=str(tmp_path) + "\n", stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        prompt_file = tmp_path / "my-task.txt"
        prompt_file.write_text("task")

        _add_to_git_exclude(str(prompt_file))

        assert "my-task.txt" in exclude_file.read_text()

    def test_idempotent(self, tmp_path, monkeypatch):
        """Adding the same path twice only creates one entry."""
        git_dir = tmp_path / ".git" / "info"
        git_dir.mkdir(parents=True)
        exclude_file = git_dir / "exclude"
        exclude_file.write_text("my-task.txt\n")

        def fake_run(cmd, **kw):
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=str(tmp_path) + "\n", stderr="",
            )

        monkeypatch.setattr(subprocess, "run", fake_run)

        _add_to_git_exclude(str(tmp_path / "my-task.txt"))

        lines = [ln for ln in exclude_file.read_text().splitlines() if ln == "my-task.txt"]
        assert len(lines) == 1
