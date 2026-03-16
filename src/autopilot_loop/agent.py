"""Wrapper around `copilot -p` subprocess calls.

Spawns copilot CLI in non-interactive mode, streams stdout in real-time
(visible when attached to tmux), handles timeout with SIGTERM/SIGKILL.
"""

import io
import logging
import os
import signal
import subprocess
import sys
import threading
import time

logger = logging.getLogger(__name__)

__all__ = ["AgentResult", "run_agent"]


class AgentResult:
    """Result of a copilot agent invocation."""

    __slots__ = ("exit_code", "session_file", "stdout", "stderr", "duration")

    def __init__(self, exit_code, session_file, stdout, stderr, duration):
        self.exit_code = exit_code
        self.session_file = session_file
        self.stdout = stdout
        self.stderr = stderr
        self.duration = duration

    @property
    def success(self):
        return self.exit_code == 0


def _stream_and_capture(pipe, echo_to, captured):
    """Read from pipe line-by-line, echo to a stream, and capture all output."""
    try:
        for line in iter(pipe.readline, b""):
            decoded = line.decode("utf-8", errors="replace")
            captured.write(decoded)
            if echo_to:
                echo_to.write(decoded)
                echo_to.flush()
    finally:
        pipe.close()


def run_agent(prompt, session_dir, model="claude-opus-4.6", timeout=1800, extra_flags=None):
    """Run copilot CLI in non-interactive mode.

    Streams stdout to the terminal in real-time so you can see progress
    when attached to the tmux session. Also captures full output for logging.

    Args:
        prompt: The prompt text for copilot -p.
        session_dir: Directory to store the session markdown.
        model: Model name for --model flag.
        timeout: Timeout in seconds (SIGTERM, then SIGKILL after 30s grace).
        extra_flags: Additional CLI flags as a list of strings.

    Returns:
        AgentResult with exit code, session file path, stdout, stderr, duration.
    """
    session_file = os.path.join(session_dir, "session.md")

    cmd = [
        "copilot",
        "-p", prompt,
        "--allow-all",
        "--no-ask-user",
        "--model", model,
        "--share", session_file,
        "-s",
    ]

    if extra_flags:
        cmd.extend(extra_flags)

    logger.info("Running agent: copilot -p '%.100s...' --model %s (timeout: %ds)", prompt, model, timeout)
    logger.debug("Full command: %s", " ".join(cmd))

    start_time = time.time()

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # Start in a new process group so we can kill the whole tree
            preexec_fn=os.setsid,
        )
    except FileNotFoundError:
        logger.error("copilot CLI not found. Is it installed?")
        return AgentResult(
            exit_code=127,
            session_file=session_file,
            stdout="",
            stderr="copilot: command not found",
            duration=0.0,
        )

    # Stream stdout to terminal in real-time while capturing it
    stdout_captured = io.StringIO()
    stderr_captured = io.StringIO()

    stdout_thread = threading.Thread(
        target=_stream_and_capture,
        args=(proc.stdout, sys.stdout, stdout_captured),
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_and_capture,
        args=(proc.stderr, None, stderr_captured),
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()

    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        logger.warning("Agent timed out after %ds, sending SIGTERM", timeout)
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            logger.warning("Agent still running after SIGTERM grace period, sending SIGKILL")
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            proc.wait()

    # Wait for streaming threads to finish reading
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    duration = time.time() - start_time
    stdout = stdout_captured.getvalue()
    stderr = stderr_captured.getvalue()

    logger.info(
        "Agent finished: exit_code=%d, duration=%.1fs, session=%s",
        proc.returncode, duration, session_file,
    )

    if proc.returncode != 0:
        logger.warning("Agent stderr: %s", stderr[:500])

    return AgentResult(
        exit_code=proc.returncode,
        session_file=session_file,
        stdout=stdout,
        stderr=stderr,
        duration=duration,
    )
