<pre>
   ___       __              _ __   __     __
  / _ |__ __/ /____  ___  (_) /__  / /_   / /  ___  ___  ___
 / __ / // / __/ _ \/ _ \/ / / _ \/ __/  / /__/ _ \/ _ \/ _ \
/_/ |_\_,_/\__/\___/ .__/_/_/\___/\__/  /____/\___/\___/ .__/
                  /_/                                  /_/
</pre>

# autopilot-loop

[![CI](https://github.com/chanakyav/autopilot-loop/actions/workflows/ci.yml/badge.svg)](https://github.com/chanakyav/autopilot-loop/actions/workflows/ci.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Headless orchestrator that automates the Copilot **implement → review → fix** cycle, running in tmux. Works in GitHub Codespaces and local workspaces. Survives disconnects.

---

**[Getting Started](docs/getting-started.md)** · **[Running Locally](docs/local-setup.md)** · **[Configuration](docs/configuration.md)** · **[Fix CI](docs/fix-ci-workflow.md)** · **[Dotfiles Setup](docs/dotfiles-setup.md)** · **[Troubleshooting](docs/troubleshooting.md)**

---

## Quick Start

```bash
pip install git+https://github.com/chanakyav/autopilot-loop.git

autopilot start --prompt "Refactor UserService: extract billing logic into a concern"
autopilot status            # check progress
autopilot logs              # view logs
autopilot status --watch    # interactive dashboard
```

## What It Does

1. Runs `copilot -p` (non-interactive) to implement your task
2. Agent creates a branch, commits, opens a draft PR using the repo's PR template
3. Requests a Copilot review via GitHub API
4. Polls until the review completes
5. Fetches unresolved inline comments, passes them to a new `copilot -p` session to fix
6. Replies to each review comment with what was fixed or skipped, resolves threads
7. Re-requests review — loops until clean (or max iterations reached)

All runs in tmux. Close your laptop — it keeps going.

## Commands

| Command | Description |
|---|---|
| `autopilot start --prompt "..."` | Start a new task |
| `autopilot start --prompt "..." --plan` | Plan first, then implement |
| `autopilot start --issue 123` | Start from a GitHub issue |
| `autopilot resume --pr 42345` | Resume from an existing PR |
| `autopilot fix-ci --pr 42345` | [Fix CI failures](docs/fix-ci-workflow.md) |
| `autopilot stop <id>` | Stop a running task |
| `autopilot restart <id>` | Restart a stopped task |
| `autopilot status` | Show all task statuses |
| `autopilot status --watch` | Interactive dashboard (TUI) |
| `autopilot status --json` | JSON output for scripting |
| `autopilot logs` | Show latest task log |
| `autopilot attach <id>` | Attach to a task's tmux session |
| `autopilot next` | Jump to next session needing attention |
| `autopilot doctor` | Check prerequisites (tools, auth, environment) |

## Interactive Dashboard

```
┏━ autopilot-loop — Sessions (3) ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃   #   Task ID    Mode     Branch                  State          PR    Iter   ┃
┃   ► 1  a1b2c3d4  review   autopilot/a1b2c3d4      ⠹ IMPLEMENT    -     0/5    ┃
┃     2  e5f6g7h8  ci       autopilot/e5f6g7h8      ◐ WAIT_CI      #43   1/5    ┃
┃     3  i9j0k1l2  review   autopilot/i9j0k1l2      ■ STOPPED      #44   3/5    ┃
┗━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┛
 j/k navigate  Enter attach  x stop  l logs  d detail  r refresh  q quit
```

`j/k` navigate · `Enter` attach · `l` logs · `d` detail · `x` stop · `q` quit — [full keybindings](docs/getting-started.md)

## Configuration

Create `autopilot.json` in your repo root (optional — all values have sensible defaults):

```json
{
  "model": "claude-opus-4.6",
  "max_iterations": 5,
  "custom_instructions": "Run tests with: npm test"
}
```

See [Configuration Reference](docs/configuration.md) for all options.

## Prerequisites

- **`copilot` CLI** and **`gh` CLI** (pre-installed in Codespaces, or [install locally](docs/local-setup.md))
- **tmux** (recommended; tasks fall back to foreground without it)
- **Python 3.8+**

Run `autopilot doctor` to verify your setup.

## Local Development

```bash
git clone https://github.com/chanakyav/autopilot-loop.git
cd autopilot-loop
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## License

MIT
