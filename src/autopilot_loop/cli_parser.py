"""Argument parser construction for the autopilot CLI.

Separated from cli.py to keep command handlers and parser definition
in distinct modules. Imported by cli.main().
"""

import argparse

__all__ = ["build_parser"]

_BANNER = r"""
   ___       __              _ __   __     __
  / _ |__ __/ /____  ___  (_) /__  / /_   / /  ___  ___  ___
 / __ / // / __/ _ \/ _ \/ / / _ \/ __/  / /__/ _ \/ _ \/ _ \
/_/ |_\_,_/\__/\___/ .__/_/_/\___/\__/  /____/\___/\___/ .__/
                  /_/                                  /_/
"""


def build_parser():
    """Build and return the top-level argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="autopilot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=_BANNER + "Headless Copilot coding-review-fix orchestrator",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    subparsers = parser.add_subparsers(dest="command")

    # start
    p_start = subparsers.add_parser("start", help="Start a new autopilot task")
    p_start.add_argument("--prompt", "-p", type=str, help="Task description")
    p_start.add_argument("--issue", "-i", type=str, help="GitHub issue number or full URL")
    p_start.add_argument("--file", "-f", type=str, help="Read prompt from a file")
    p_start.add_argument("--plan", action="store_true",
                         help="Agent creates a plan first, then implements (default: implement only)")
    p_start.add_argument("--model", type=str, help="Model override")
    p_start.add_argument("--max-iters", type=int, help="Max review-fix iterations")
    p_start.add_argument("--dry-run", action="store_true",
                         help="Show what would run without starting agents or tmux")
    p_start.add_argument("--no-follow", action="store_true",
                         help="Don't auto-open log viewer after start")

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume from an existing PR")
    p_resume.add_argument("--pr", type=int, required=True, help="PR number to resume")
    p_resume.add_argument("--context", "-c", type=str, default="",
                          help="Additional instructions for the agent")

    # status
    p_status = subparsers.add_parser("status", help="Show task status")
    p_status.add_argument("--watch", "-w", action="store_true", help="Auto-refresh status display")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")
    p_status.add_argument("--interval", type=int, default=5,
                           help="Refresh interval in seconds (only with --watch)")

    # logs
    p_logs = subparsers.add_parser("logs", help="Show task logs")
    p_logs.add_argument("--session", type=str, help="Task ID")
    p_logs.add_argument("--phase", type=str,
                        help="Phase name (e.g. implement, fix-1, fix-2, plan)")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop a running task")
    p_stop.add_argument("task_id", type=str, help="Task ID to stop")

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart a stopped task")
    p_restart.add_argument("task_id", type=str, help="Task ID to restart")

    # fix-ci
    p_fixci = subparsers.add_parser("fix-ci", help="Fix CI failures on an existing PR")
    p_fixci.add_argument("--pr", type=int, required=True, help="PR number")
    p_fixci.add_argument("--checks", type=str,
                         help="Comma-separated check names; uses substring match "
                              "(e.g. 'build' matches 'build-ubuntu')")
    p_fixci.add_argument("--max-iters", type=int, help="Max fix iterations")
    p_fixci.add_argument("--model", type=str, help="Model override")

    # attach
    p_attach = subparsers.add_parser("attach", help="Attach to a task's tmux session")
    p_attach.add_argument("task_id", type=str, help="Task ID to attach to")

    # next
    subparsers.add_parser("next", help="Jump to next session needing attention")

    # doctor
    subparsers.add_parser("doctor", help="Check prerequisites for running autopilot-loop")

    # _run (internal, called from tmux)
    p_run = subparsers.add_parser("_run", help=argparse.SUPPRESS)
    p_run.add_argument("--task-id", required=True, help=argparse.SUPPRESS)

    return parser
