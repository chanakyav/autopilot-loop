"""Status dashboard rendering for autopilot-loop.

Provides table, JSON, and live-watch views of task status.
Uses the `rich` library for formatted terminal output.
"""

import json
import time

from autopilot_loop.persistence import get_active_tasks, list_tasks

__all__ = [
    "status_table",
    "status_json",
    "status_watch",
]


def _format_elapsed(created_at):
    elapsed = time.time() - created_at
    if elapsed < 60:
        return "< 1m"
    elif elapsed < 3600:
        return "%dm ago" % (elapsed / 60)
    else:
        return "%.1fh ago" % (elapsed / 3600)


_STATE_STYLES = {
    "COMPLETE": "green",
    "FAILED": "red",
    "STOPPED": "yellow",
    "WAIT_REVIEW": "cyan",
    "WAIT_CI": "cyan",
    "IMPLEMENT": "blue",
    "PLAN_AND_IMPLEMENT": "blue",
    "FIX": "magenta",
    "FIX_CI": "magenta",
}

_STATE_INDICATORS = {
    "COMPLETE": "\u2713",
    "FAILED": "\u2717",
    "STOPPED": "\u25a0",
}


def _build_table(title, tasks):
    """Build a rich Table from a list of task dicts."""
    from rich.table import Table

    table = Table(title=title, border_style="dim")
    table.add_column("#", style="dim", width=3)
    table.add_column("Task ID", style="bold")
    table.add_column("Mode")
    table.add_column("Branch")
    table.add_column("State")
    table.add_column("PR")
    table.add_column("Iter")
    table.add_column("Elapsed", justify="right")

    for i, t in enumerate(tasks, 1):
        state = t["state"]
        style = _STATE_STYLES.get(state, "")
        indicator = _STATE_INDICATORS.get(state, "\u25cf")
        state_display = "%s %s" % (indicator, state)
        pr = "#%d" % t["pr_number"] if t["pr_number"] else "-"
        iteration = "%d/%d" % (t["iteration"], t["max_iterations"])
        mode = t.get("task_mode", "review")
        branch = t.get("branch") or "-"
        if len(branch) > 30:
            branch = branch[:27] + "..."
        elapsed = _format_elapsed(t["created_at"])

        table.add_row(
            str(i), t["id"], mode, branch,
            "[%s]%s[/]" % (style, state_display) if style else state_display,
            pr, iteration, elapsed,
        )

    return table


def status_table():
    """Print a rich table of all tasks."""
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
        console.print("\\n[dim]%d active session(s)[/dim]" % len(active))


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
    """Auto-refreshing status display."""
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
