# autopilot-loop

[![CI](https://github.com/chanakyav/autopilot-loop/actions/workflows/ci.yml/badge.svg)](https://github.com/chanakyav/autopilot-loop/actions/workflows/ci.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Headless orchestrator that automates the Copilot **implement → review → fix** cycle, running in tmux inside GitHub Codespaces. Survives disconnects.

## Quick Start

```bash
# Install
pip install git+https://github.com/chanakyav/autopilot-loop.git

# Start a task (from your repo's Codespace)
autopilot start --prompt "Refactor UserService: extract billing logic into a concern"

# Check progress (from any terminal)
autopilot status

# View logs
autopilot logs

# Attach to watch live
tmux attach -t autopilot-<task_id>
```

## What It Does

1. Runs `copilot -p` (non-interactive) to implement your task
2. Agent creates a branch, commits with proper messages, opens a draft PR using the repo's PR template
3. Requests a Copilot review via GitHub API
4. Polls until the review completes (5-20 min)
5. Fetches unresolved inline review comments, passes them to a new `copilot -p` session to fix
6. Agent decides what's worth addressing, commits, pushes
7. Replies to each review comment with what was fixed or why it was skipped, then resolves the thread
8. Re-requests review — only unresolved comments are considered in the next cycle
9. Loops until clean (or max iterations reached)

All of this runs in a tmux session. Close your laptop — it keeps going.

## Commands

```bash
# Task lifecycle
autopilot start --prompt "..."           # Start a new task
autopilot start --prompt "..." --plan    # Let agent plan + implement
autopilot start --issue 12345            # Start from a GitHub issue
autopilot resume --pr 42345              # Resume from an existing PR
autopilot fix-ci --pr 42345              # Fix CI failures (interactive check selection)
autopilot fix-ci --pr 42345 --checks "build-ubuntu,test-integration"  # Non-interactive
autopilot stop abc123                     # Stop a running task
autopilot restart abc123                  # Restart a stopped task

# Monitoring
autopilot status                          # Show all task statuses (rich table)
autopilot status --watch                  # Auto-refreshing live dashboard
autopilot status --json                   # JSON output for scripting
autopilot logs                            # Show latest task log
autopilot logs --session abc123           # Show specific task log
autopilot logs --session abc123 --phase fix-1  # Show specific phase

# Session navigation
autopilot attach abc123                   # Attach to a task's tmux session
autopilot next                            # Jump to next session needing attention
```

## Configuration

Create `autopilot.json` in your repo root or `~/.autopilot-loop/config.json`:

```json
{
  "model": "claude-opus-4.6",
  "max_iterations": 5,
  "max_retries_per_phase": 1,
  "reviewer": "copilot-pull-request-reviewer[bot]",
  "review_poll_interval_seconds": 60,
  "review_timeout_seconds": 3600,
  "agent_timeout_seconds": 1800,
  "idle_timeout_minutes": 120,
  "keepalive_enabled": false,
  "keepalive_interval_seconds": 300,
  "branch_pattern": "autopilot/{task_id}",
  "custom_instructions": "Run tests with: npm test\nRun linting with: npm run lint",
  "ci_check_names": [],
  "ci_poll_interval_seconds": 120,
  "ci_poll_timeout_seconds": 5400
}
```

All values have sensible defaults — config file is optional.

## Prerequisites

- **GitHub Codespace** with `copilot` CLI and `gh` CLI installed
- **tmux** (pre-installed in most Codespaces; `apt install tmux` elsewhere)
- **Python 3.8+** with `rich` (installed automatically)

Codespace idle timeout is checked and only extended if needed at startup.

## Session Management

Multiple autopilot sessions can run concurrently on different branches. Branch locking prevents two tasks from operating on the same branch.

If you start a task on an existing `autopilot/*` branch, autopilot detects it and works on that branch instead of creating a new one.

Stopped tasks (`autopilot stop`) are marked as `STOPPED` (not `FAILED`) and can be restarted with `autopilot restart`.

### tmux Integration

Add to your `~/.tmux.conf` for quick access:

```bash
bind g display-popup -E -w 80% -h 60% "autopilot status --watch"
bind n display-popup -E -w 80% -h 60% "autopilot start --prompt ''"
```

## Local Development

```bash
git clone https://github.com/chanakyav/autopilot-loop.git
cd autopilot-loop
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

All external calls are mocked in unit tests — no `copilot` or `gh` CLI needed locally.

## License

MIT
