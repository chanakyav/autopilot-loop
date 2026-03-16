"""Status dashboard rendering for autopilot-loop.

Provides table, JSON, live-watch, and interactive TUI views of task status.
Uses the ``rich`` library for formatted terminal output.
Interactive mode uses ``termios`` for raw keyboard input (Unix only).

Views:
- status_table(): one-shot table print
- status_json(): JSON output for scripting
- status_watch(): passive auto-refresh (non-interactive, for piped output)
- status_interactive(): full-screen TUI with keybindings, detail panel, log viewer
"""

import json
import os
import select
import subprocess
import sys
import time

from autopilot_loop.persistence import (
    get_active_tasks,
    get_sessions_dir,
    list_tasks,
    update_task,
)

__all__ = [
    "status_table",
    "status_json",
    "status_watch",
    "status_interactive",
]


# ---------------------------------------------------------------------------
# Elapsed time formatting
# ---------------------------------------------------------------------------

def _format_elapsed(created_at):
    elapsed = time.time() - created_at
    if elapsed < 60:
        return "< 1m"
    elif elapsed < 3600:
        return "%dm" % (elapsed / 60)
    else:
        return "%.1fh" % (elapsed / 3600)


# ---------------------------------------------------------------------------
# Animated spinners & color palette
# ---------------------------------------------------------------------------

_WORKING_FRAMES = list("\u280b\u2819\u2839\u2838\u283c\u2834\u2826\u2827\u2807\u280f")
_WAITING_FRAMES = list("\u25d0\u25d3\u25d1\u25d2")

_STATE_STYLES = {
    "COMPLETE": "dim green",
    "FAILED": "bright_red",
    "STOPPED": "yellow",
    "WAIT_REVIEW": "bright_cyan",
    "WAIT_CI": "bright_cyan",
    "IMPLEMENT": "bright_green",
    "PLAN_AND_IMPLEMENT": "bright_green",
    "FIX": "bright_green",
    "FIX_CI": "bright_green",
    "INIT": "bright_white",
    "REQUEST_REVIEW": "bright_cyan",
    "PARSE_REVIEW": "bright_cyan",
    "VERIFY_PR": "bright_green",
    "VERIFY_PUSH": "bright_green",
    "RESOLVE_COMMENTS": "bright_green",
    "FETCH_ANNOTATIONS": "bright_cyan",
}

_WORKING_STATES = frozenset({
    "IMPLEMENT", "PLAN_AND_IMPLEMENT", "FIX", "FIX_CI",
    "VERIFY_PR", "VERIFY_PUSH", "RESOLVE_COMMENTS", "INIT",
})

_WAITING_STATES = frozenset({
    "WAIT_REVIEW", "WAIT_CI", "REQUEST_REVIEW",
    "PARSE_REVIEW", "FETCH_ANNOTATIONS",
})

_STATIC_INDICATORS = {
    "COMPLETE": "\u2713",
    "FAILED": "\u2717",
    "STOPPED": "\u25a0",
}

_TERMINAL_STATES = frozenset({"COMPLETE", "FAILED", "STOPPED"})


def _get_indicator(state, tick):
    """Return an animated or static indicator for the given state."""
    if state in _WORKING_STATES:
        return _WORKING_FRAMES[tick % len(_WORKING_FRAMES)]
    if state in _WAITING_STATES:
        return _WAITING_FRAMES[tick % len(_WAITING_FRAMES)]
    return _STATIC_INDICATORS.get(state, "\u25cf")


# ---------------------------------------------------------------------------
# Table builder
# ---------------------------------------------------------------------------

def _build_table(title, tasks, selected_idx=-1, tick=0, compact=False):
    """Build a rich Table from a list of task dicts.

    Args:
        title: Table title string.
        tasks: List of task dicts from persistence.
        selected_idx: Index of the highlighted row (-1 for none).
        tick: Animation tick counter for spinner frames.
        compact: If True, use minimal padding (for detail panel mode).
    """
    from rich.box import HEAVY
    from rich.table import Table

    pad = (0, 1) if compact else (1, 1)
    table = Table(
        title="[bold bright_white]%s[/]" % title,
        box=HEAVY,
        border_style="dim cyan",
        expand=True,
        padding=pad,
    )
    table.add_column("#", style="dim", width=4, no_wrap=True)
    table.add_column("Task ID", style="bold", no_wrap=True)
    table.add_column("Mode", no_wrap=True)
    table.add_column("Branch", no_wrap=True)
    table.add_column("State", no_wrap=True)
    table.add_column("PR", no_wrap=True)
    table.add_column("Iter", no_wrap=True)
    table.add_column("Elapsed", justify="right", no_wrap=True)

    for i, t in enumerate(tasks):
        state = t["state"]
        style = _STATE_STYLES.get(state, "")
        indicator = _get_indicator(state, tick)
        state_display = "%s %s" % (indicator, state)
        pr = "#%d" % t["pr_number"] if t["pr_number"] else "-"
        iteration = "%d/%d" % (t["iteration"], t["max_iterations"])
        mode = t.get("task_mode", "review")
        branch = t.get("branch") or "-"
        if len(branch) > 30:
            branch = branch[:27] + "..."
        elapsed = _format_elapsed(t["created_at"])

        is_selected = (i == selected_idx)
        prefix = " \u25ba " if is_selected else "   "
        row_style = "reverse" if is_selected else ""

        state_cell = "[%s]%s[/]" % (style, state_display) if style else state_display

        table.add_row(
            prefix + str(i + 1), t["id"], mode, branch,
            state_cell, pr, iteration, elapsed,
            style=row_style,
        )

    return table


# ---------------------------------------------------------------------------
# Detail panel builder
# ---------------------------------------------------------------------------

def _build_detail_panel(task):
    """Build a detail panel for the selected task."""
    from rich.box import HEAVY
    from rich.panel import Panel
    from rich.text import Text

    lines = Text()
    lines.append("Task ", style="dim")
    lines.append(task["id"], style="bold bright_white")
    lines.append(" \u2014 %s\n" % task.get("task_mode", "review"), style="dim")

    lines.append("Branch:  ", style="dim")
    lines.append("%s\n" % (task.get("branch") or "-"), style="bright_white")

    lines.append("State:   ", style="dim")
    state = task["state"]
    style = _STATE_STYLES.get(state, "")
    lines.append("%s" % state, style=style)
    lines.append("  (%s)\n" % _format_elapsed(task["created_at"]), style="dim")

    pr_num = task.get("pr_number")
    lines.append("PR:      ", style="dim")
    lines.append("%s\n" % ("#%d" % pr_num if pr_num else "not yet created"), style="bright_white")

    lines.append("Iter:    ", style="dim")
    lines.append("%d/%d\n" % (task["iteration"], task["max_iterations"]), style="bright_white")

    # Tail the orchestrator log
    lines.append("\n")
    lines.append("Recent log:\n", style="bold dim")
    log_lines = _read_log_tail(task["id"], max_lines=8)
    if log_lines:
        for line in log_lines:
            lines.append_text(_style_log_line(line))
            lines.append("\n")
    else:
        lines.append("  (no logs yet)\n", style="dim")

    return Panel(
        lines,
        box=HEAVY,
        border_style="dim cyan",
        expand=True,
        padding=(0, 1),
    )


def _read_log_tail(task_id, max_lines=8):
    """Read the last N lines from a task's orchestrator log."""
    try:
        sessions_dir = get_sessions_dir(task_id)
        log_file = os.path.join(sessions_dir, "orchestrator.log")
        if not os.path.isfile(log_file):
            return []
        with open(log_file, "r") as f:
            all_lines = f.readlines()
        return [line.rstrip() for line in all_lines[-max_lines:]]
    except (OSError, IOError):
        return []


def _read_log_full(task_id):
    """Read the full orchestrator log for a task.

    Deduplicates lines that appear twice (once from tee with short timestamp,
    once from file handler with full timestamp). Collapses runs of 3+ blank
    lines down to a single blank line.
    """
    try:
        sessions_dir = get_sessions_dir(task_id)
        log_file = os.path.join(sessions_dir, "orchestrator.log")
        if not os.path.isfile(log_file):
            return []
        with open(log_file, "r") as f:
            raw_lines = [line.rstrip() for line in f.readlines()]
    except (OSError, IOError):
        return []

    # Deduplicate: keep the full-timestamp version (starts with 20xx-)
    # and drop the short-timestamp duplicate
    seen = set()
    deduped = []
    for line in raw_lines:
        # Extract the content after the timestamp for comparison
        # Full format: "2026-03-16 12:45:50,889 [INFO] [id] message"
        # Short format: "12:45:50 [INFO] [id] message"
        # Normalize by stripping the date prefix if present
        normalized = line
        if len(line) > 20 and line[:4].isdigit() and line[4] == "-":
            # Full timestamp — extract from the time portion
            # "2026-03-16 12:45:50,889 [INFO]..." → key on "12:45:50 [INFO]..."
            parts = line.split(" ", 2)
            if len(parts) >= 3:
                # Remove milliseconds: "12:45:50,889" → "12:45:50"
                time_part = parts[1].split(",")[0]
                normalized = time_part + " " + parts[2]
        elif len(line) > 8 and line[2] == ":" and line[5] == ":":
            # Short timestamp — "12:45:50 [INFO]..."
            time_part = line[:8]
            normalized = time_part + " " + line[9:] if len(line) > 9 else line

        if normalized in seen and line:
            continue
        if line:
            seen.add(normalized)
        deduped.append(line)

    # Collapse runs of 3+ blank lines into 1
    result = []
    blank_count = 0
    for line in deduped:
        if not line.strip():
            blank_count += 1
            if blank_count <= 1:
                result.append(line)
        else:
            blank_count = 0
            result.append(line)

    return result


# ---------------------------------------------------------------------------
# Footer builders
# ---------------------------------------------------------------------------

def _build_footer_main():
    """Footer for the main session list view."""
    from rich.text import Text
    footer = Text()
    keys = [
        ("j/k", "navigate"),
        ("Enter", "attach"),
        ("x", "stop"),
        ("l", "logs"),
        ("d", "detail"),
        ("r", "refresh"),
        ("q", "quit"),
    ]
    for i, (key, action) in enumerate(keys):
        if i > 0:
            footer.append("  ", style="dim")
        footer.append(key, style="bold bright_white")
        footer.append(" %s" % action, style="dim")
    return footer


def _build_footer_detail():
    """Footer for the detail panel view."""
    from rich.text import Text
    footer = Text()
    keys = [
        ("j/k", "navigate"),
        ("d", "close detail"),
        ("l", "full logs"),
        ("q", "quit"),
    ]
    for i, (key, action) in enumerate(keys):
        if i > 0:
            footer.append("  ", style="dim")
        footer.append(key, style="bold bright_white")
        footer.append(" %s" % action, style="dim")
    return footer


def _build_footer_logs():
    """Footer for the log viewer."""
    from rich.text import Text
    footer = Text()
    keys = [
        ("j/k", "scroll"),
        ("Ctrl-D/U", "page"),
        ("G", "end"),
        ("g", "top"),
        ("q", "back"),
    ]
    for i, (key, action) in enumerate(keys):
        if i > 0:
            footer.append("  ", style="dim")
        footer.append(key, style="bold bright_white")
        footer.append(" %s" % action, style="dim")
    return footer


def _build_status_message(msg):
    """Build a status message line."""
    from rich.text import Text
    if not msg:
        return Text("")
    return Text(msg, style="dim yellow")


# ---------------------------------------------------------------------------
# Non-interactive views
# ---------------------------------------------------------------------------

def status_table():
    """Print a rich table of all tasks (non-interactive)."""
    from rich.console import Console

    tasks = list_tasks()
    if not tasks:
        print("No tasks found.")
        return

    console = Console()
    table = _build_table("autopilot-loop \u2014 Sessions", tasks)
    console.print(table)

    active = get_active_tasks()
    if active:
        console.print("\n[dim]%d active session(s)[/dim]" % len(active))


def status_json():
    """Print task status as JSON."""
    tasks = list_tasks()
    output = []
    for t in tasks:
        output.append({
            "id": t["id"],
            "state": t["state"],
            "mode": t.get("task_mode", "review"),
            "branch": t.get("branch"),
            "pr_number": t.get("pr_number"),
            "iteration": t["iteration"],
            "max_iterations": t["max_iterations"],
            "elapsed_seconds": round(time.time() - t["created_at"]),
        })
    print(json.dumps(output, indent=2))


def status_watch(interval=5):
    """Auto-refreshing status display (non-interactive, for piped output)."""
    from rich.console import Console
    from rich.live import Live

    console = Console()

    def build():
        tasks = list_tasks()
        if not tasks:
            from rich.table import Table
            return Table(title="autopilot-loop \u2014 No sessions")
        return _build_table(
            "autopilot-loop \u2014 Sessions (refreshing every %ds)" % interval,
            tasks,
        )

    try:
        with Live(build(), console=console, refresh_per_second=1) as live:
            while True:
                time.sleep(interval)
                live.update(build())
    except KeyboardInterrupt:
        pass


# ---------------------------------------------------------------------------
# Keyboard input (raw terminal mode)
# ---------------------------------------------------------------------------

def _read_key(fd, timeout=2.0):
    """Read a single keypress from the terminal.

    Returns a string identifying the key, or None on timeout.
    """
    ready, _, _ = select.select([fd], [], [], timeout)
    if not ready:
        return None

    ch = os.read(fd, 1)
    if not ch:
        return None

    b = ch[0] if isinstance(ch[0], int) else ord(ch[0])

    if b in (10, 13):
        return "enter"
    if b == 27:
        ready2, _, _ = select.select([fd], [], [], 0.05)
        if ready2:
            seq = os.read(fd, 2)
            if seq == b"[A":
                return "up"
            if seq == b"[B":
                return "down"
        return "esc"
    if b == ord("j"):
        return "down"
    if b == ord("k"):
        return "up"
    if b == ord("q"):
        return "quit"
    if b == ord("x"):
        return "stop"
    if b == ord("r"):
        return "refresh"
    if b == ord("l"):
        return "logs"
    if b == ord("d") or b == ord(" "):
        return "detail"
    if b == ord("G"):
        return "end"
    if b == ord("g"):
        return "top"
    # Ctrl-D = page down, Ctrl-U = page up
    if b == 4:
        return "pagedown"
    if b == 21:
        return "pageup"

    return None


# ---------------------------------------------------------------------------
# Terminal helpers
# ---------------------------------------------------------------------------

_ENTER_ALT_SCREEN = "\x1b[?1049h"
_EXIT_ALT_SCREEN = "\x1b[?1049l"
_CLEAR_SCREEN = "\x1b[2J\x1b[H"
_HOME_CURSOR = "\x1b[H"
_CLEAR_TO_END = "\x1b[J"
_HIDE_CURSOR = "\x1b[?25l"
_SHOW_CURSOR = "\x1b[?25h"


def _enter_tui():
    """Enter the alternate screen buffer and hide cursor."""
    sys.stdout.write(_ENTER_ALT_SCREEN + _HIDE_CURSOR + _CLEAR_SCREEN)
    sys.stdout.flush()


def _exit_tui():
    """Exit the alternate screen buffer and show cursor."""
    sys.stdout.write(_SHOW_CURSOR + _EXIT_ALT_SCREEN)
    sys.stdout.flush()


def _render_frame(console, renderable):
    """Render a frame without flicker.

    Homes the cursor, prints a top margin, renders content,
    then clears any leftover lines from the previous frame.
    Zero-flicker: no full-screen erase between frames.
    """
    with console.capture() as capture:
        console.print(renderable)
    output = capture.get()
    # Home cursor + top margin + content + clear remainder
    sys.stdout.write(_HOME_CURSOR + "\n" + output + _CLEAR_TO_END)
    sys.stdout.flush()


def _style_log_line(line):
    """Apply syntax highlighting to a single log line."""
    from rich.text import Text

    # Empty line
    if not line.strip():
        return Text("")

    # State transition lines (e.g. "IMPLEMENT", "VERIFY_PR", "COMPLETE")
    # These are lines that contain just a state name after the task ID
    for state in ("INIT", "IMPLEMENT", "PLAN_AND_IMPLEMENT", "VERIFY_PR",
                  "REQUEST_REVIEW", "WAIT_REVIEW", "PARSE_REVIEW", "FIX",
                  "VERIFY_PUSH", "RESOLVE_COMMENTS", "COMPLETE", "FAILED",
                  "STOPPED", "FETCH_ANNOTATIONS", "FIX_CI", "WAIT_CI"):
        if line.rstrip().endswith("] %s" % state):
            return Text(line, style="bold bright_white")

    # Success lines (checkmark)
    if "\u2713" in line or "completed successfully" in line.lower():
        return Text(line, style="bright_green")

    # Error / failure lines
    if "[ERROR]" in line or "FAILED" in line:
        return Text(line, style="bright_red")

    # Warning lines
    if "[WARNING]" in line:
        return Text(line, style="yellow")

    # Orchestrator info lines with timestamps
    if "[INFO]" in line:
        t = Text()
        # Highlight the timestamp portion
        bracket_idx = line.find("[INFO]")
        if bracket_idx > 0:
            t.append(line[:bracket_idx], style="dim")
            t.append("[INFO]", style="dim cyan")
            rest = line[bracket_idx + 6:]
            # Highlight task ID in brackets
            if rest.startswith(" [") and "]" in rest[2:]:
                close = rest.index("]", 2)
                t.append(rest[:close + 1], style="bold dim")
                t.append(rest[close + 1:], style="")
            else:
                t.append(rest, style="")
            return t
        return Text(line, style="")

    # Agent output (no timestamp prefix) — normal brightness
    return Text(line, style="")


# ---------------------------------------------------------------------------
# Safe action handlers (never crash, always return a status message)
# ---------------------------------------------------------------------------

def _do_attach(task):
    """Try to attach to a task's tmux session. Returns a status message or None."""
    tmux_session = "autopilot-%s" % task["id"]
    try:
        result = subprocess.run(
            ["tmux", "switch-client", "-t", tmux_session],
            capture_output=True, check=True,
        )
        return None
    except subprocess.CalledProcessError:
        pass
    except FileNotFoundError:
        return "tmux not available"

    # Try attach as subprocess (NOT execvp — we want to return)
    try:
        result = subprocess.run(
            ["tmux", "has-session", "-t", tmux_session],
            capture_output=True,
        )
        if result.returncode != 0:
            return "Session not running \u2014 task is %s" % task["state"]
        # Session exists but switch-client failed (not inside tmux)
        return "Run: tmux attach -t %s" % tmux_session
    except FileNotFoundError:
        return "tmux not available"


def _do_stop(task):
    """Try to stop a task. Returns a status message."""
    if task["state"] in _TERMINAL_STATES:
        return "Already %s" % task["state"].lower()

    tmux_session = "autopilot-%s" % task["id"]
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_session],
            check=True, capture_output=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass  # Session may already be gone

    update_task(task["id"], pre_stop_state=task["state"], state="STOPPED")
    return "Stopped task %s" % task["id"]


# ---------------------------------------------------------------------------
# Log viewer TUI
# ---------------------------------------------------------------------------

def _logs_view(fd, old_settings, task_id, interval=2):
    """Full-screen log viewer for a task. Returns when user presses q/Esc."""
    import termios
    import tty

    from rich.box import HEAVY
    from rich.console import Console, Group
    from rich.panel import Panel
    from rich.text import Text

    lines = _read_log_full(task_id)
    if not lines:
        return "No logs available for task %s" % task_id

    # Pre-style all lines once (only re-style on log refresh)
    styled_lines = [_style_log_line(line) for line in lines]

    console_height = os.get_terminal_size().lines - 7  # borders + footer + margin
    half_page = max(1, console_height // 2)
    scroll_offset = max(0, len(lines) - console_height)

    while True:
        # Render in cooked mode
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

        console = Console()
        max_offset = max(0, len(lines) - console_height)
        visible = styled_lines[scroll_offset:scroll_offset + console_height]

        log_text = Text()
        for styled in visible:
            log_text.append_text(styled)
            log_text.append("\n")

        panel = Panel(
            log_text,
            title="[bold bright_white]Logs \u2014 Task %s[/]" % task_id,
            box=HEAVY,
            border_style="dim cyan",
            expand=True,
            padding=(0, 1),
        )

        position = Text()
        position.append(
            "  Line %d-%d of %d" % (
                scroll_offset + 1,
                min(scroll_offset + console_height, len(lines)),
                len(lines),
            ),
            style="dim",
        )

        _render_frame(console, Group(panel, _build_footer_logs(), position))

        # Raw mode only for key reading
        tty.setraw(fd)
        key = _read_key(fd, timeout=interval)

        if key in ("quit", "esc"):
            return None

        if key == "down":
            scroll_offset = min(scroll_offset + 1, max_offset)
        elif key == "up":
            scroll_offset = max(scroll_offset - 1, 0)
        elif key == "pagedown":
            scroll_offset = min(scroll_offset + half_page, max_offset)
        elif key == "pageup":
            scroll_offset = max(scroll_offset - half_page, 0)
        elif key == "end":
            scroll_offset = max_offset
        elif key == "top":
            scroll_offset = 0
        elif key is None:
            # Timeout — re-read log in case it grew
            lines = _read_log_full(task_id)
            styled_lines = [_style_log_line(line) for line in lines]
            max_offset = max(0, len(lines) - console_height)
            if not lines:
                return None


# ---------------------------------------------------------------------------
# Interactive TUI (main entry point)
# ---------------------------------------------------------------------------

def status_interactive(interval=2):
    """Full-screen interactive dashboard with keybindings.

    Features:
    - j/k navigate, Enter attach, x stop, l logs, d detail, r refresh, q quit
    - Animated spinners for active states
    - Detail panel toggle showing task metadata + log tail
    - Full log viewer with j/k scroll
    - Alternate screen buffer (no scrollback bleed)
    - Safe actions with status messages (never crashes out of TUI)

    Falls back to status_watch() if not a TTY or termios unavailable.
    """
    if not sys.stdin.isatty():
        status_watch(interval=interval)
        return

    try:
        import termios
        import tty
    except ImportError:
        status_watch(interval=interval)
        return

    from rich.console import Console, Group

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        status_watch(interval=interval)
        return

    selected = 0
    tick = 0
    detail_open = False
    status_msg = ""
    status_msg_until = 0

    def set_status(msg, duration=3):
        nonlocal status_msg, status_msg_until
        status_msg = msg
        status_msg_until = time.time() + duration

    def get_status():
        if time.time() < status_msg_until:
            return status_msg
        return ""

    def render_main(console):
        tasks = list_tasks()
        if not tasks:
            from rich.table import Table
            table = Table(title="[bold bright_white]autopilot-loop \u2014 No sessions[/]")
            footer = _build_footer_main()
            msg = _build_status_message(get_status())
            return Group(table, footer, msg), tasks

        sel = min(selected, len(tasks) - 1)
        title = "autopilot-loop \u2014 Sessions (%d)" % len(tasks)
        table = _build_table(title, tasks, selected_idx=sel, tick=tick,
                             compact=detail_open)

        parts = [table]
        if detail_open and tasks:
            parts.append(_build_detail_panel(tasks[sel]))

        footer = _build_footer_detail() if detail_open else _build_footer_main()
        parts.append(footer)
        msg_text = get_status()
        if msg_text:
            parts.append(_build_status_message(msg_text))

        return Group(*parts), tasks

    try:
        _enter_tui()

        while True:
            # Render in cooked mode for correct terminal width
            termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

            console = Console()
            content, tasks = render_main(console)
            _render_frame(console, content)

            # Raw mode only for key reading
            tty.setraw(fd)
            key = _read_key(fd, timeout=interval)

            if key in ("quit", "esc"):
                if detail_open:
                    detail_open = False
                    sys.stdout.write(_CLEAR_SCREEN)
                    sys.stdout.flush()
                else:
                    break

            elif key == "down":
                tasks = list_tasks()
                if tasks:
                    selected = min(selected + 1, len(tasks) - 1)

            elif key == "up":
                tasks = list_tasks()
                if tasks:
                    selected = max(selected - 1, 0)

            elif key == "enter":
                tasks = list_tasks()
                if tasks and 0 <= selected < len(tasks):
                    msg = _do_attach(tasks[selected])
                    if msg:
                        set_status(msg)

            elif key == "stop":
                tasks = list_tasks()
                if tasks and 0 <= selected < len(tasks):
                    msg = _do_stop(tasks[selected])
                    set_status(msg)

            elif key == "detail":
                detail_open = not detail_open
                sys.stdout.write(_CLEAR_SCREEN)
                sys.stdout.flush()

            elif key == "logs":
                tasks = list_tasks()
                if tasks and 0 <= selected < len(tasks):
                    msg = _logs_view(fd, old_settings, tasks[selected]["id"], interval)
                    if msg:
                        set_status(msg)
                    # Full clear after returning from log viewer
                    sys.stdout.write(_CLEAR_SCREEN)
                    sys.stdout.flush()

            elif key == "refresh":
                set_status("Refreshed")

            tick += 1

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        _exit_tui()
