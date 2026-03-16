"""Tests for the dashboard rendering functions."""

import os
import time

import pytest

from autopilot_loop import persistence
from autopilot_loop.dashboard import (
    _STATIC_INDICATORS,
    _WAITING_FRAMES,
    _WAITING_STATES,
    _WORKING_FRAMES,
    _WORKING_STATES,
    _build_detail_panel,
    _build_footer_detail,
    _build_footer_logs,
    _build_footer_main,
    _build_status_message,
    _build_table,
    _format_elapsed,
    _get_indicator,
    _read_key,
    _read_log_tail,
)


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))


# ---------------------------------------------------------------------------
# _format_elapsed
# ---------------------------------------------------------------------------

class TestFormatElapsed:
    def test_under_one_minute(self):
        assert _format_elapsed(time.time() - 30) == "< 1m"

    def test_minutes(self):
        result = _format_elapsed(time.time() - 600)
        assert result == "10m"

    def test_hours(self):
        result = _format_elapsed(time.time() - 7200)
        assert "2.0h" in result


# ---------------------------------------------------------------------------
# _get_indicator
# ---------------------------------------------------------------------------

class TestGetIndicator:
    def test_working_state_cycles(self):
        indicators = [_get_indicator("IMPLEMENT", t) for t in range(20)]
        # Should cycle through working frames
        assert indicators[0] == _WORKING_FRAMES[0]
        assert indicators[1] == _WORKING_FRAMES[1]

    def test_waiting_state_cycles(self):
        indicators = [_get_indicator("WAIT_REVIEW", t) for t in range(8)]
        assert indicators[0] == _WAITING_FRAMES[0]
        assert indicators[4] == _WAITING_FRAMES[0]  # Wraps at len 4

    def test_complete_static(self):
        assert _get_indicator("COMPLETE", 0) == _STATIC_INDICATORS["COMPLETE"]
        assert _get_indicator("COMPLETE", 99) == _STATIC_INDICATORS["COMPLETE"]

    def test_failed_static(self):
        assert _get_indicator("FAILED", 0) == _STATIC_INDICATORS["FAILED"]

    def test_stopped_static(self):
        assert _get_indicator("STOPPED", 0) == _STATIC_INDICATORS["STOPPED"]

    def test_all_working_states_animate(self):
        for state in _WORKING_STATES:
            i0 = _get_indicator(state, 0)
            i1 = _get_indicator(state, 1)
            assert i0 in _WORKING_FRAMES
            assert i1 in _WORKING_FRAMES

    def test_all_waiting_states_animate(self):
        for state in _WAITING_STATES:
            i0 = _get_indicator(state, 0)
            assert i0 in _WAITING_FRAMES


# ---------------------------------------------------------------------------
# _build_table
# ---------------------------------------------------------------------------

class TestBuildTable:
    def _make_task(self, task_id="t1", state="IMPLEMENT", pr_number=None,
                   branch="autopilot/t1"):
        now = time.time()
        return {
            "id": task_id, "state": state, "pr_number": pr_number,
            "branch": branch, "iteration": 1, "max_iterations": 5,
            "task_mode": "review", "created_at": now,
        }

    def test_empty_tasks(self):
        table = _build_table("Test", [])
        assert table.row_count == 0

    def test_single_task(self):
        tasks = [self._make_task()]
        table = _build_table("Test", tasks)
        assert table.row_count == 1

    def test_selected_row(self):
        tasks = [self._make_task("t1"), self._make_task("t2")]
        table = _build_table("Test", tasks, selected_idx=1)
        assert table.row_count == 2

    def test_compact_mode(self):
        tasks = [self._make_task()]
        table = _build_table("Test", tasks, compact=True)
        assert table.row_count == 1

    def test_long_branch_truncated(self):
        tasks = [self._make_task(branch="autopilot/very-long-branch-name-that-exceeds-thirty-characters")]
        table = _build_table("Test", tasks)
        assert table.row_count == 1

    def test_tick_advances_spinner(self):
        tasks = [self._make_task(state="IMPLEMENT")]
        t0 = _build_table("Test", tasks, tick=0)
        t1 = _build_table("Test", tasks, tick=1)
        # Tables should differ (different spinner frame)
        assert t0 is not t1


# ---------------------------------------------------------------------------
# _build_detail_panel
# ---------------------------------------------------------------------------

class TestBuildDetailPanel:
    def test_renders_without_error(self):
        task = {
            "id": "abc123", "state": "IMPLEMENT", "task_mode": "review",
            "branch": "autopilot/abc123", "pr_number": None,
            "iteration": 0, "max_iterations": 5,
            "created_at": time.time(),
        }
        panel = _build_detail_panel(task)
        assert panel is not None

    def test_with_pr_number(self):
        task = {
            "id": "abc123", "state": "WAIT_REVIEW", "task_mode": "review",
            "branch": "autopilot/abc123", "pr_number": 42,
            "iteration": 1, "max_iterations": 5,
            "created_at": time.time() - 300,
        }
        panel = _build_detail_panel(task)
        assert panel is not None


# ---------------------------------------------------------------------------
# _read_log_tail
# ---------------------------------------------------------------------------

class TestReadLogTail:
    def test_no_log_file(self):
        assert _read_log_tail("nonexistent") == []

    def test_reads_tail(self, tmp_path, monkeypatch):
        monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
        sessions_dir = tmp_path / "sessions" / "t1"
        sessions_dir.mkdir(parents=True)
        log_file = sessions_dir / "orchestrator.log"
        lines = ["line %d" % i for i in range(20)]
        log_file.write_text("\n".join(lines) + "\n")
        result = _read_log_tail("t1", max_lines=5)
        assert len(result) == 5
        assert result[-1] == "line 19"


# ---------------------------------------------------------------------------
# Footer builders
# ---------------------------------------------------------------------------

class TestFooters:
    def test_main_footer_has_all_keys(self):
        footer = _build_footer_main()
        text = str(footer)
        for key in ["j/k", "Enter", "x", "l", "d", "r", "q"]:
            assert key in text

    def test_detail_footer_has_close(self):
        footer = _build_footer_detail()
        text = str(footer)
        assert "close" in text

    def test_logs_footer_has_scroll(self):
        footer = _build_footer_logs()
        text = str(footer)
        assert "scroll" in text
        assert "G" in text

    def test_status_message_empty(self):
        msg = _build_status_message("")
        assert str(msg) == ""

    def test_status_message_content(self):
        msg = _build_status_message("Session not running")
        assert "Session not running" in str(msg)


# ---------------------------------------------------------------------------
# _read_key (unit test with mock fd)
# ---------------------------------------------------------------------------

class TestReadKey:
    def test_timeout_returns_none(self):
        # Use a real pipe to test timeout
        r, w = os.pipe()
        try:
            result = _read_key(r, timeout=0.05)
            assert result is None
        finally:
            os.close(r)
            os.close(w)

    def test_j_returns_down(self):
        r, w = os.pipe()
        try:
            os.write(w, b"j")
            result = _read_key(r, timeout=0.1)
            assert result == "down"
        finally:
            os.close(r)
            os.close(w)

    def test_k_returns_up(self):
        r, w = os.pipe()
        try:
            os.write(w, b"k")
            result = _read_key(r, timeout=0.1)
            assert result == "up"
        finally:
            os.close(r)
            os.close(w)

    def test_q_returns_quit(self):
        r, w = os.pipe()
        try:
            os.write(w, b"q")
            result = _read_key(r, timeout=0.1)
            assert result == "quit"
        finally:
            os.close(r)
            os.close(w)

    def test_x_returns_stop(self):
        r, w = os.pipe()
        try:
            os.write(w, b"x")
            result = _read_key(r, timeout=0.1)
            assert result == "stop"
        finally:
            os.close(r)
            os.close(w)

    def test_r_returns_refresh(self):
        r, w = os.pipe()
        try:
            os.write(w, b"r")
            result = _read_key(r, timeout=0.1)
            assert result == "refresh"
        finally:
            os.close(r)
            os.close(w)

    def test_l_returns_logs(self):
        r, w = os.pipe()
        try:
            os.write(w, b"l")
            result = _read_key(r, timeout=0.1)
            assert result == "logs"
        finally:
            os.close(r)
            os.close(w)

    def test_d_returns_detail(self):
        r, w = os.pipe()
        try:
            os.write(w, b"d")
            result = _read_key(r, timeout=0.1)
            assert result == "detail"
        finally:
            os.close(r)
            os.close(w)

    def test_enter_returns_enter(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\n")
            result = _read_key(r, timeout=0.1)
            assert result == "enter"
        finally:
            os.close(r)
            os.close(w)

    def test_arrow_up(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\x1b[A")
            result = _read_key(r, timeout=0.1)
            assert result == "up"
        finally:
            os.close(r)
            os.close(w)

    def test_arrow_down(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\x1b[B")
            result = _read_key(r, timeout=0.1)
            assert result == "down"
        finally:
            os.close(r)
            os.close(w)

    def test_G_returns_end(self):
        r, w = os.pipe()
        try:
            os.write(w, b"G")
            result = _read_key(r, timeout=0.1)
            assert result == "end"
        finally:
            os.close(r)
            os.close(w)

    def test_g_returns_top(self):
        r, w = os.pipe()
        try:
            os.write(w, b"g")
            result = _read_key(r, timeout=0.1)
            assert result == "top"
        finally:
            os.close(r)
            os.close(w)

    def test_unknown_key_returns_none(self):
        r, w = os.pipe()
        try:
            os.write(w, b"z")
            result = _read_key(r, timeout=0.1)
            assert result is None
        finally:
            os.close(r)
            os.close(w)

    def test_ctrl_d_returns_pagedown(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\x04")
            result = _read_key(r, timeout=0.1)
            assert result == "pagedown"
        finally:
            os.close(r)
            os.close(w)

    def test_ctrl_u_returns_pageup(self):
        r, w = os.pipe()
        try:
            os.write(w, b"\x15")
            result = _read_key(r, timeout=0.1)
            assert result == "pageup"
        finally:
            os.close(r)
            os.close(w)
