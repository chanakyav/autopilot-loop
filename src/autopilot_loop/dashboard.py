"""Status dashboard rendering for autopilot-loop.

Provides table, JSON, live-watch, and interactive TUI views of task status.
Uses the ``rich`` library for formatted terminal output.
Interactive mode uses ``termios`` for raw keyboard input (Unix only).
"""

import json
import os
import select
import subprocess
import sys
import time

from autopilot_loop.persistence import get_active_tasks, list_tasks, update_task

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

def _build_table(title, tasks, selected_idx=-1, tick=0):
    """Build a rich Table from a list of task dicts.

    Args:
        title: Table title string.
        tasks: List of task dicts from persistence.
        selected_idx: Index of the highlighted row (-1 for none).
        tick: Animation tick counter for spinner frames.
    """
    from rich.box import ROUNDED
    from rich.table import Table

    table = Table(
        title="[bold bright_white]%s[/]" % title,
        box=ROUNDED,
        border_style="dim cyan",
        expand=True,
        padding=(0, 1),
    )
    table.add_column("#", style="dim", width=3, no_wrap=True)
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
        prefix = "\u25ba " if is_selected else "  "
        row_style = "reverse" if is_selected else ""

        state_cell = "[%s]%s[/]" % (style, state_display) if style else state_display

        table.add_row(
            prefix + str(i + 1), t["id"], mode, branch,
            state_cell, pr, iteration, elapsed,
            style=row_style,
        )

    return table


def _build_footer():
    """Build the keybinding hint footer."""
    from rich.text import Text

    footer = Text()
    keys = [
        ("j/k", "navigate"),
        ("Enter", "attach"),
        ("x", "stop"),
        ("r", "refresh"),
        ("q", "quit"),
    ]
    for i, (key, action) in enumerate(keys):
        if i > 0:
            footer.append("  ", style="dim")
        footer.append(key, style="bold bright_white")
        footer.append(" %s" % action, style="dim")
    return footer


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

    # Enter
    if b in (10, 13):
        return "enter"
    # Escape — could be bare Esc or start of arrow sequence
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

    return None


# ---------------------------------------------------------------------------
# Interactive TUI
# ---------------------------------------------------------------------------

def status_interactive(interval=2):
    """Full-screen interactive dashboard with keybindings.

    Requires a TTY. Falls back to status_watch() if not a terminal.
    """
    if not sys.stdin.isatty():
        status_watch(interval=interval)
        return

    try:
        import termios
        import tty
    except ImportError:
        # Windows or environment without termios — fall back to passive watch
        status_watch(interval=interval)
        return

    from rich.console import Console, Group
    from rich.live import Live

    console = Console()
    selected = 0
    tick = 0

    fd = sys.stdin.fileno()
    try:
        old_settings = termios.tcgetattr(fd)
    except termios.error:
        # Not a real terminal (e.g. VS Code process terminal) — fall back
        status_watch(interval=interval)
        return

    def render():
        tasks = list_tasks()
        if not tasks:
            from rich.table import Table
            table = Table(title="[bold bright_white]autopilot-loop \u2014 No sessions[/]")
            return Group(table, _build_footer()), tasks

        sel = min(selected, len(tasks) - 1)
        table = _build_table("autopilot-loop \u2014 Sessions", tasks,
                             selected_idx=sel, tick=tick)
        return Group(table, _build_footer()), tasks

    try:
        tty.setraw(fd)
        content, tasks = render()

        with Live(content, console=console, screen=False, refresh_per_second=4) as live:
            while True:
                key = _read_key(fd, timeout=interval)

                if key in ("quit", "esc"):
                    break

                if key == "down":
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
                        task = tasks[selected]
                        tmux_session = "autopilot-%s" % task["id"]
                        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
                        try:
                            subprocess.run(
                                ["tmux", "switch-client", "-t", tmux_session],
                                check=True,
                            )
                        except subprocess.CalledProcessError:
                            try:
                                os.execvp("tmux", ["tmux", "attach", "-t", tmux_session])
                            except FileNotFoundError:
                                pass
                        tty.setraw(fd)

                elif key == "stop":
                    tasks = list_tasks()
                    if tasks and 0 <= selected < len(tasks):
                        task = tasks[selected]
                        if task["state"] not in ("COMPLETE", "FAILED", "STOPPED"):
                            tmux_session = "autopilot-%s" % task["id"]
                            try:
                                subprocess.run(
                                    ["tmux", "kill-session", "-t", tmux_session],
                                    check=True, capture_output=True,
                                )
                            except (subprocess.CalledProcessError, FileNotFoundError):
                                pass
                            update_task(task["id"],
                                        pre_stop_state=task["state"],
                                        state="STOPPED")

                tick += 1
                content, tasks = render()
                live.update(content)

    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
