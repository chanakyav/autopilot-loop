"""Tests for the copilot agent runner."""

import io
import os
import signal
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from autopilot_loop.agent import run_agent


@pytest.fixture
def session_dir(tmp_path):
    return str(tmp_path)


def _make_pipe(data):
    """Create a file-like bytes pipe from a string."""
    return io.BytesIO(data)


def _mock_proc(stdout=b"", stderr=b"", returncode=0, pid=12345):
    """Create a mock Popen process with pipe-like stdout/stderr."""
    proc = MagicMock()
    proc.stdout = _make_pipe(stdout)
    proc.stderr = _make_pipe(stderr)
    proc.returncode = returncode
    proc.pid = pid
    proc.wait.return_value = returncode
    return proc


class TestRunAgent:
    def test_success(self, session_dir):
        mock_proc = _mock_proc(stdout=b"output text\n")

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc) as mock_popen:
            result = run_agent("test prompt", session_dir, model="test-model", timeout=60)

        assert result.success
        assert result.exit_code == 0
        assert "output text" in result.stdout
        assert result.duration > 0

        # Verify command construction
        call_args = mock_popen.call_args
        cmd = call_args[0][0]
        assert cmd[0] == "copilot"
        assert "-p" in cmd
        assert "test prompt" in cmd
        assert "--allow-all" in cmd
        assert "--no-ask-user" in cmd
        assert "--model" in cmd
        assert "test-model" in cmd
        assert "-s" in cmd
        assert "--share" in cmd

    def test_failure_exit_code(self, session_dir):
        mock_proc = _mock_proc(stderr=b"error occurred\n", returncode=1)

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            result = run_agent("test prompt", session_dir)

        assert not result.success
        assert result.exit_code == 1
        assert "error occurred" in result.stderr

    def test_timeout_sends_sigterm(self, session_dir):
        mock_proc = _mock_proc(returncode=-15)
        mock_proc.wait.side_effect = [
            subprocess.TimeoutExpired(cmd="copilot", timeout=5),
            -15,
        ]

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            with patch("autopilot_loop.agent.os.killpg") as mock_killpg:
                with patch("autopilot_loop.agent.os.getpgid", return_value=12345):
                    result = run_agent("test prompt", session_dir, timeout=5)

        mock_killpg.assert_called_with(12345, signal.SIGTERM)
        assert result.exit_code == -15

    def test_copilot_not_found(self, session_dir):
        with patch("autopilot_loop.agent.subprocess.Popen", side_effect=FileNotFoundError):
            result = run_agent("test prompt", session_dir)

        assert result.exit_code == 127
        assert "not found" in result.stderr

    def test_extra_flags_passed(self, session_dir):
        mock_proc = _mock_proc()

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc) as mock_popen:
            run_agent("prompt", session_dir, extra_flags=["--add-dir", "/tmp/extra"])

        cmd = mock_popen.call_args[0][0]
        assert "--add-dir" in cmd
        assert "/tmp/extra" in cmd

    def test_session_file_path(self, session_dir):
        mock_proc = _mock_proc()

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            result = run_agent("prompt", session_dir)

        assert result.session_file == os.path.join(session_dir, "session.md")

    def test_output_truncated_when_limit_exceeded(self, session_dir):
        """When max_output_bytes is set and exceeded, output is truncated."""
        # Generate output larger than the limit
        large_output = b"x" * 100 + b"\n"  # 101 bytes per line
        lines = large_output * 20  # ~2020 bytes total
        mock_proc = _mock_proc(stdout=lines)

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            result = run_agent("prompt", session_dir, max_output_bytes=500)

        assert "[OUTPUT TRUNCATED at 500 bytes]" in result.stdout
        # Captured output should be limited (truncation marker + some lines)
        assert len(result.stdout) < 2020

    def test_output_not_truncated_when_under_limit(self, session_dir):
        """When output is under max_output_bytes, nothing is truncated."""
        mock_proc = _mock_proc(stdout=b"small output\n")

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            result = run_agent("prompt", session_dir, max_output_bytes=50000)

        assert "small output" in result.stdout
        assert "TRUNCATED" not in result.stdout

    def test_output_unlimited_when_zero(self, session_dir):
        """When max_output_bytes is 0 (default), output is not truncated."""
        large_output = b"x" * 1000 + b"\n"
        mock_proc = _mock_proc(stdout=large_output * 10)

        with patch("autopilot_loop.agent.subprocess.Popen", return_value=mock_proc):
            result = run_agent("prompt", session_dir, max_output_bytes=0)

        assert "TRUNCATED" not in result.stdout
        assert len(result.stdout) > 5000
