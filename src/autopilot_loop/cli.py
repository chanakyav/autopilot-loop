"""CLI entry point for autopilot-loop.

Subcommands: start, resume, status, logs, stop, restart, fix-ci, attach, next.
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import uuid

from autopilot_loop.config import load_config
from autopilot_loop.persistence import (
    create_task,
    get_active_tasks,
    get_sessions_dir,
    get_task,
    get_tasks_on_branch,
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


def _detect_autopilot_branch():
    """If the current git branch matches autopilot/*, return the branch name.

    Returns None if not on an autopilot branch or git is unavailable.
    """
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()
        if branch.startswith("autopilot/"):
            return branch
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return None


def _launch_in_tmux(task_id, mode="review", branch=None, pr_number=None):
    """Launch a task in tmux with standardized output.

    Args:
        task_id: The task ID to launch.
        mode: Display mode ("review", "ci", "resume").
        branch: Branch name for display.
        pr_number: PR number for display (optional).
    """
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
    except subprocess.CalledProcessError as e:
        print("Error: failed to create tmux session: %s" % e, file=sys.stderr)
        sys.exit(1)

    # Standardized output
    print("\u2713 Autopilot session started")
    print("  Task:     %s" % task_id)
    print("  Mode:     %s" % mode)
    if branch:
        print("  Branch:   %s" % branch)
    if pr_number:
        print("  PR:       #%d" % pr_number)
    print("  tmux:     %s" % tmux_session)
    print()
    print("  autopilot status              — check progress")
    print("  autopilot logs --session %s  — view logs" % task_id)
    print("  autopilot attach %s          — attach to session" % task_id)
    print("  autopilot stop %s            — stop task" % task_id)


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

    # Detect if we're already on an autopilot branch
    existing_branch = _detect_autopilot_branch()
    if existing_branch:
        branch = existing_branch
        logger.info("Detected existing autopilot branch: %s", branch)
    else:
        branch = config["branch_pattern"].format(task_id=task_id)

    # Branch locking: prevent concurrent tasks on the same branch
    conflicting = get_tasks_on_branch(branch)
    if conflicting:
        print("Error: branch %s already has an active task: %s (state: %s)" % (
            branch, conflicting[0]["id"], conflicting[0]["state"]), file=sys.stderr)
        print("Use 'autopilot stop %s' first, or work on a different branch." % conflicting[0]["id"],
              file=sys.stderr)
        sys.exit(1)

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

    if existing_branch:
        update_task(task_id, existing_branch=1)

    if args.dry_run:
        print("DRY RUN — would start task %s" % task_id)
        print("  Branch: %s" % branch)
        print("  Model: %s" % config["model"])
        print("  Max iterations: %d" % config["max_iterations"])
        print("  Plan mode: %s" % args.plan)
        print("  Prompt: %s" % prompt[:200])
        return

    _launch_in_tmux(task_id, mode="review", branch=branch)


def cmd_run(args):
    """Internal: run the orchestrator for a task (called from tmux)."""
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

    # Dispatch to the right orchestrator based on task mode
    task_mode = task.get("task_mode", "review")
    if task_mode == "ci":
        from autopilot_loop.orchestrator import CIOrchestrator
        orch = CIOrchestrator(task_id=args.task_id, config=config)
    else:
        from autopilot_loop.orchestrator import Orchestrator
        orch = Orchestrator(task_id=args.task_id, config=config)

    result = orch.run()

    if result.get("state") == "COMPLETE":
        logger.info("Task %s completed successfully", args.task_id)
    else:
        logger.error("Task %s ended in state: %s", args.task_id, result.get("state"))


def cmd_resume(args):
    """Resume a task from an existing PR."""
    config = load_config()

    # Validate PR exists and get branch BEFORE creating any task state
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(args.pr), "--json", "headRefName", "--jq", ".headRefName"],
            capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()

        # Check out the branch
        subprocess.run(["git", "checkout", branch], check=True)
    except subprocess.CalledProcessError as e:
        print("Error: could not fetch PR #%d: %s" % (args.pr, e), file=sys.stderr)
        sys.exit(1)

    task_id = _generate_task_id()

    create_task(
        task_id=task_id,
        prompt="(resumed from PR #%d)" % args.pr,
        max_iterations=config["max_iterations"],
        model=config["model"],
    )

    from autopilot_loop.persistence import update_task
    update_task(task_id, pr_number=args.pr, state="PARSE_REVIEW", branch=branch)

    _launch_in_tmux(task_id, mode="resume", branch=branch, pr_number=args.pr)


def cmd_status(args):
    """Show status of all autopilot tasks."""
    from autopilot_loop.dashboard import status_json, status_table, status_watch

    if getattr(args, "json", False):
        status_json()
        return

    if getattr(args, "watch", False):
        status_watch(interval=getattr(args, "interval", 5))
        return

    status_table()


def cmd_attach(args):
    """Attach to a task's tmux session."""
    task_id = args.task_id
    task = get_task(task_id)
    if not task:
        print("Error: task %s not found" % task_id, file=sys.stderr)
        sys.exit(1)

    tmux_session = "autopilot-%s" % task_id
    try:
        subprocess.run(["tmux", "switch-client", "-t", tmux_session], check=True)
    except subprocess.CalledProcessError:
        # Not inside tmux — try attach instead
        try:
            os.execvp("tmux", ["tmux", "attach", "-t", tmux_session])
        except FileNotFoundError:
            print("Error: tmux not found", file=sys.stderr)
            sys.exit(1)


def cmd_next(args):
    """Jump to the next session needing attention (STOPPED, FAILED, or input-waiting)."""
    tasks = list_tasks()
    # Priority: STOPPED > FAILED > active states needing attention
    attention_states = ["STOPPED", "FAILED"]
    for state in attention_states:
        for t in tasks:
            if t["state"] == state:
                tmux_session = "autopilot-%s" % t["id"]
                print("Switching to task %s (state: %s)" % (t["id"], state))
                try:
                    subprocess.run(["tmux", "switch-client", "-t", tmux_session], check=True)
                    return
                except subprocess.CalledProcessError:
                    try:
                        os.execvp("tmux", ["tmux", "attach", "-t", tmux_session])
                    except FileNotFoundError:
                        print("Error: tmux not found", file=sys.stderr)
                        sys.exit(1)

    # No sessions needing attention
    active = get_active_tasks()
    if active:
        print("No sessions need attention. %d active session(s) running." % len(active))
    else:
        print("No active sessions.")


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


def cmd_fix_ci(args):
    """Fix CI failures on an existing PR."""
    from autopilot_loop.github_api import get_failed_checks

    config = load_config({
        "model": args.model,
        "max_iterations": args.max_iters,
    })

    # Validate PR exists and get branch
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(args.pr), "--json", "headRefName", "--jq", ".headRefName"],
            capture_output=True, text=True, check=True,
        )
        branch = result.stdout.strip()
        subprocess.run(["git", "checkout", branch], check=True)
    except subprocess.CalledProcessError as e:
        print("Error: could not fetch PR #%d: %s" % (args.pr, e), file=sys.stderr)
        sys.exit(1)

    # Get failed checks
    failed_checks = get_failed_checks(args.pr)
    if not failed_checks:
        print("No failed CI checks found on PR #%d" % args.pr)
        return

    # Determine which checks to fix
    if args.checks:
        # Non-interactive: substring match
        patterns = [p.strip() for p in args.checks.split(",")]
        selected = [c for c in failed_checks if any(p in c["name"] for p in patterns)]
        if not selected:
            print("No failed checks matched: %s" % args.checks, file=sys.stderr)
            print("Available failed checks:")
            for c in failed_checks:
                print("  %s" % c["name"])
            sys.exit(1)
    elif config.get("ci_check_names"):
        # Pre-configured in config
        patterns = config["ci_check_names"]
        selected = [c for c in failed_checks if any(p in c["name"] for p in patterns)]
        if not selected:
            print("No failed checks matched ci_check_names config: %s" % patterns, file=sys.stderr)
            sys.exit(1)
    else:
        # Interactive: list and prompt
        print("Failed CI checks on PR #%d:" % args.pr)
        print()
        for i, c in enumerate(failed_checks, 1):
            print("  %d. %s" % (i, c["name"]))
        print()

        try:
            selection = input("Which checks to fix? (comma-separated numbers, or 'all'): ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            sys.exit(1)

        if selection.lower() == "all":
            selected = list(failed_checks)
        else:
            try:
                indices = [int(s.strip()) for s in selection.split(",")]
                selected = [failed_checks[i - 1] for i in indices if 1 <= i <= len(failed_checks)]
            except (ValueError, IndexError):
                print("Error: invalid selection", file=sys.stderr)
                sys.exit(1)

        if not selected:
            print("No checks selected.")
            return

    check_names = [c["name"] for c in selected]
    print("\u2713 Selected %d checks:" % len(selected))
    for name in check_names:
        print("  \u2022 %s" % name)

    task_id = _generate_task_id()
    from autopilot_loop.persistence import update_task

    create_task(
        task_id=task_id,
        prompt="(fix-ci for PR #%d)" % args.pr,
        max_iterations=config["max_iterations"],
        model=config["model"],
    )
    update_task(
        task_id,
        pr_number=args.pr,
        branch=branch,
        state="FETCH_ANNOTATIONS",
        task_mode="ci",
        ci_check_names=json.dumps(check_names),
    )

    _launch_in_tmux(task_id, mode="ci", branch=branch, pr_number=args.pr)


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

    # Update task state — save the current state before marking STOPPED
    task = get_task(task_id)
    if task and task["state"] not in ("COMPLETE", "FAILED", "STOPPED"):
        from autopilot_loop.persistence import update_task
        update_task(task_id, pre_stop_state=task["state"], state="STOPPED")
        print("✓ Task %s marked as STOPPED" % task_id)


def cmd_restart(args):
    """Restart a stopped task from its current phase."""
    task_id = args.task_id
    task = get_task(task_id)

    if not task:
        print("Error: task %s not found" % task_id, file=sys.stderr)
        sys.exit(1)

    if task["state"] != "STOPPED":
        print("Error: task %s is in state %s, only STOPPED tasks can be restarted" % (task_id, task["state"]),
              file=sys.stderr)
        sys.exit(1)

    # Determine the phase to restart from. For states that are mid-action
    # (e.g. FIX, IMPLEMENT), restart from the beginning of that phase.
    # For waiting states, restart from the state that triggered the wait.
    restart_state = task["state"]
    stopped_state = task.get("pre_stop_state") or "INIT"

    # Map waiting/verification states back to their action states
    _RESTART_STATE_MAP = {
        "VERIFY_PUSH": "FIX" if task.get("task_mode") != "ci" else "FIX_CI",
        "WAIT_REVIEW": "REQUEST_REVIEW",
        "WAIT_CI": "FIX_CI",
        "VERIFY_PR": "IMPLEMENT",
    }
    restart_state = _RESTART_STATE_MAP.get(stopped_state, stopped_state)

    from autopilot_loop.persistence import update_task
    update_task(task_id, state=restart_state)

    mode = "ci" if task.get("task_mode") == "ci" else "review"
    _launch_in_tmux(task_id, mode="restart (%s)" % mode, branch=task.get("branch"),
                    pr_number=task.get("pr_number"))


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
    p_status = subparsers.add_parser("status", help="Show task status")
    p_status.add_argument("--watch", "-w", action="store_true", help="Auto-refresh status display")
    p_status.add_argument("--json", action="store_true", help="Output as JSON")
    p_status.add_argument("--interval", type=int, default=5, help="Refresh interval in seconds (with --watch)")

    # logs
    p_logs = subparsers.add_parser("logs", help="Show task logs")
    p_logs.add_argument("--session", type=str, help="Task ID")
    p_logs.add_argument("--phase", type=str, help="Phase name (e.g., fix-1, implement)")

    # stop
    p_stop = subparsers.add_parser("stop", help="Stop a running task")
    p_stop.add_argument("task_id", type=str, help="Task ID to stop")

    # restart
    p_restart = subparsers.add_parser("restart", help="Restart a stopped task")
    p_restart.add_argument("task_id", type=str, help="Task ID to restart")

    # fix-ci
    p_fixci = subparsers.add_parser("fix-ci", help="Fix CI failures on an existing PR")
    p_fixci.add_argument("--pr", type=int, required=True, help="PR number")
    p_fixci.add_argument("--checks", type=str, help="Comma-separated check names (substring match)")
    p_fixci.add_argument("--max-iters", type=int, help="Max fix iterations")
    p_fixci.add_argument("--model", type=str, help="Model override")

    # attach
    p_attach = subparsers.add_parser("attach", help="Attach to a task's tmux session")
    p_attach.add_argument("task_id", type=str, help="Task ID to attach to")

    # next
    subparsers.add_parser("next", help="Jump to next session needing attention")

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
    elif args.command == "restart":
        cmd_restart(args)
    elif args.command == "fix-ci":
        cmd_fix_ci(args)
    elif args.command == "attach":
        cmd_attach(args)
    elif args.command == "next":
        cmd_next(args)
    elif args.command == "_run":
        cmd_run(args)
    else:
        parser.print_help()
        sys.exit(1)
