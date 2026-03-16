"""CLI entry point for autopilot-loop.

Subcommands: start, resume, status, logs, stop, _run (internal).
"""

import argparse
import logging
import os
import subprocess
import sys
import time
import uuid

from autopilot_loop.config import load_config
from autopilot_loop.persistence import (
    create_task,
    get_sessions_dir,
    get_task,
    list_tasks,
)

logger = logging.getLogger("autopilot")

__all__ = ["main"]


def _setup_logging(verbose=False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )


def _generate_task_id():
    return uuid.uuid4().hex[:8]


def cmd_start(args):
    """Start a new autopilot task."""
    config = load_config({
        "model": args.model,
        "max_iterations": args.max_iters,
    })

    # Resolve prompt
    if args.issue:
        from autopilot_loop.github_api import get_issue
        issue = get_issue(args.issue)
        prompt = "Issue #%d: %s\n\n%s" % (args.issue, issue["title"], issue["body"][:4000])
    elif args.prompt:
        prompt = args.prompt
    else:
        print("Error: --prompt or --issue is required", file=sys.stderr)
        sys.exit(1)

    task_id = _generate_task_id()
    branch = config["branch_pattern"].format(task_id=task_id)

    create_task(
        task_id=task_id,
        prompt=prompt,
        max_iterations=config["max_iterations"],
        plan_mode=args.plan,
        dry_run=args.dry_run,
        model=config["model"],
    )

    from autopilot_loop.persistence import update_task
    update_task(task_id, branch=branch)

    if args.dry_run:
        print("DRY RUN — would start task %s" % task_id)
        print("  Branch: %s" % branch)
        print("  Model: %s" % config["model"])
        print("  Max iterations: %d" % config["max_iterations"])
        print("  Plan mode: %s" % args.plan)
        print("  Prompt: %s" % prompt[:200])
        return

    # Launch in tmux
    sessions_dir = get_sessions_dir(task_id)
    log_file = os.path.join(sessions_dir, "orchestrator.log")
    tmux_session = "autopilot-%s" % task_id

    # Build the command to run inside tmux
    run_cmd = "autopilot _run --task-id %s 2>&1 | tee -a %s" % (task_id, log_file)

    try:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", tmux_session, run_cmd],
            check=True,
        )
    except FileNotFoundError:
        # tmux not available — run in foreground
        logger.warning("tmux not found, running in foreground")
        cmd_run(argparse.Namespace(task_id=task_id))
        return
    except subprocess.CalledProcessError as e:
        print("Error: failed to create tmux session: %s" % e, file=sys.stderr)
        sys.exit(1)

    print("✓ Task %s created" % task_id)
    print("✓ Running in tmux session: %s" % tmux_session)
    print()
    print("  To check progress:  autopilot status")
    print("  To view logs:       autopilot logs --session %s" % task_id)
    print("  To attach to tmux:  tmux attach -t %s" % tmux_session)
    print("  To stop:            autopilot stop %s" % task_id)


def cmd_run(args):
    """Internal: run the orchestrator for a task (called from tmux)."""
    from autopilot_loop.orchestrator import Orchestrator

    task = get_task(args.task_id)
    if not task:
        print("Error: task %s not found" % args.task_id, file=sys.stderr)
        sys.exit(1)

    config = load_config({"model": task["model"]})

    # Setup file logging for this task
    sessions_dir = get_sessions_dir(args.task_id)
    log_file = os.path.join(sessions_dir, "orchestrator.log")
    file_handler = logging.FileHandler(log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(file_handler)

    orch = Orchestrator(task_id=args.task_id, config=config)
    result = orch.run()

    if result.get("state") == "COMPLETE":
        logger.info("Task %s completed successfully", args.task_id)
    else:
        logger.error("Task %s ended in state: %s", args.task_id, result.get("state"))


def cmd_resume(args):
    """Resume a task from an existing PR."""
    config = load_config()

    task_id = _generate_task_id()


    create_task(
        task_id=task_id,
        prompt="(resumed from PR #%d)" % args.pr,
        max_iterations=config["max_iterations"],
        model=config["model"],
    )

    from autopilot_loop.persistence import update_task
    update_task(task_id, pr_number=args.pr, state="PARSE_REVIEW")

    # Get branch from PR
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(args.pr), "--json", "headRefName", "--jq", ".headRefName"],
            capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()
        update_task(task_id, branch=branch)

        # Check out the branch
        subprocess.run(["git", "checkout", branch], check=True)
    except subprocess.CalledProcessError as e:
        print("Error: could not fetch PR #%d: %s" % (args.pr, e), file=sys.stderr)
        sys.exit(1)

    # Launch in tmux
    sessions_dir = get_sessions_dir(task_id)
    log_file = os.path.join(sessions_dir, "orchestrator.log")
    tmux_session = "autopilot-%s" % task_id
    run_cmd = "autopilot _run --task-id %s 2>&1 | tee -a %s" % (task_id, log_file)

    try:
        subprocess.run(
            ["tmux", "new-session", "-d", "-s", tmux_session, run_cmd],
            check=True,
        )
    except FileNotFoundError:
        logger.warning("tmux not found, running in foreground")
        cmd_run(argparse.Namespace(task_id=task_id))
        return

    print("✓ Resuming PR #%d as task %s" % (args.pr, task_id))
    print("✓ Branch: %s" % branch)
    print("✓ Running in tmux session: %s" % tmux_session)


def cmd_status(args):
    """Show status of all autopilot tasks."""
    tasks = list_tasks()

    if not tasks:
        print("No tasks found.")
        return

    # Header
    print("%-10s %-18s %-8s %-11s %s" % ("TASK_ID", "STATE", "PR", "ITERATION", "STARTED"))
    print("-" * 65)

    for t in tasks:
        pr = "#%d" % t["pr_number"] if t["pr_number"] else "-"
        iteration = "%d/%d" % (t["iteration"], t["max_iterations"])
        elapsed = time.time() - t["created_at"]
        if elapsed < 3600:
            started = "%dm ago" % (elapsed / 60)
        else:
            started = "%.1fh ago" % (elapsed / 3600)
        print("%-10s %-18s %-8s %-11s %s" % (t["id"], t["state"], pr, iteration, started))


def cmd_logs(args):
    """Show logs for a task."""
    # Find task
    if args.session:
        task_id = args.session
    else:
        tasks = list_tasks(limit=1)
        if not tasks:
            print("No tasks found.")
            return
        task_id = tasks[0]["id"]

    sessions_dir = get_sessions_dir(task_id)

    if args.phase:
        # Show specific phase file
        phase_file = os.path.join(sessions_dir, "%s.md" % args.phase)
        if not os.path.isfile(phase_file):
            # Try .json
            phase_file = os.path.join(sessions_dir, "%s.json" % args.phase)
        if os.path.isfile(phase_file):
            with open(phase_file, "r") as f:
                print(f.read())
        else:
            print("No log file found for phase '%s' in task %s" % (args.phase, task_id))
            print("Available files:")
            for name in sorted(os.listdir(sessions_dir)):
                print("  %s" % name)
    else:
        # Show orchestrator.log
        log_file = os.path.join(sessions_dir, "orchestrator.log")
        if os.path.isfile(log_file):
            with open(log_file, "r") as f:
                print(f.read())
        else:
            print("No orchestrator log found for task %s" % task_id)
            if os.path.isdir(sessions_dir):
                print("Available files:")
                for name in sorted(os.listdir(sessions_dir)):
                    print("  %s" % name)


def cmd_stop(args):
    """Stop a running task."""
    task_id = args.task_id
    tmux_session = "autopilot-%s" % task_id

    # Try to kill the tmux session
    try:
        subprocess.run(
            ["tmux", "kill-session", "-t", tmux_session],
            check=True, capture_output=True,
        )
        print("✓ Stopped tmux session: %s" % tmux_session)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("No tmux session found for task %s" % task_id)

    # Update task state
    task = get_task(task_id)
    if task and task["state"] not in ("COMPLETE", "FAILED"):
        from autopilot_loop.persistence import update_task
        update_task(task_id, state="FAILED")
        print("✓ Task %s marked as FAILED" % task_id)


def main():
    parser = argparse.ArgumentParser(
        prog="autopilot",
        description="Headless Copilot coding-review-fix orchestrator",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Verbose output")
    subparsers = parser.add_subparsers(dest="command")

    # start
    p_start = subparsers.add_parser("start", help="Start a new autopilot task")
    p_start.add_argument("--prompt", "-p", type=str, help="Task description")
    p_start.add_argument("--issue", "-i", type=int, help="GitHub issue number")
    p_start.add_argument("--plan", action="store_true", help="Let agent plan + implement (default: implement only)")
    p_start.add_argument("--model", type=str, help="Model override")
    p_start.add_argument("--max-iters", type=int, help="Max review-fix iterations")
    p_start.add_argument("--dry-run", action="store_true", help="Log transitions without running agents")

    # resume
    p_resume = subparsers.add_parser("resume", help="Resume from an existing PR")
    p_resume.add_argument("--pr", type=int, required=True, help="PR number to resume")

    # status
    subparsers.add_parser("status", help="Show task status")

    # logs
    p_logs = subparsers.add_parser("logs", help="Show task logs")
    p_logs.add_argument("--session", type=str, help="Task ID")
    p_logs.add_argument("--phase", type=str, help="Phase name (e.g., fix-1, implement)")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop a running task")
    p_stop.add_argument("task_id", type=str, help="Task ID to stop")

    # _run (internal, called from tmux)
    p_run = subparsers.add_parser("_run", help=argparse.SUPPRESS)
    p_run.add_argument("--task-id", required=True, help=argparse.SUPPRESS)

    args = parser.parse_args()
    _setup_logging(verbose=getattr(args, "verbose", False))

    if args.command == "start":
        cmd_start(args)
    elif args.command == "resume":
        cmd_resume(args)
    elif args.command == "status":
        cmd_status(args)
    elif args.command == "logs":
        cmd_logs(args)
    elif args.command == "stop":
        cmd_stop(args)
    elif args.command == "_run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)
