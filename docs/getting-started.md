# Getting Started

## Prerequisites

| Requirement | Notes |
|---|---|
| **`copilot` CLI** | Pre-installed in Codespaces; [install locally](local-setup.md) otherwise |
| **`gh` CLI** | Pre-installed in Codespaces; [install locally](https://cli.github.com) otherwise |
| **tmux** | Recommended; `brew install tmux` (macOS) or `apt install tmux` (Linux) |
| **Python 3.8+** | Pre-installed in most environments |

Run `autopilot doctor` to verify all prerequisites.

## Install

```bash
pip install git+https://github.com/chanakyav/autopilot-loop.git
```

Verify the install:

```bash
autopilot --help
```

## Run Your First Task

1. **Open a terminal** in your Codespace or local workspace.

2. **Verify your setup** (first time only):

   ```bash
   autopilot doctor
   ```

3. **Start a task** with a prompt describing what you want done:

   ```bash
   autopilot start --prompt "Refactor UserService: extract billing logic into a concern"
   ```

   You'll see output like:

   ```
   ✓ Autopilot session started
     Task:     a1b2c3d4
     Mode:     review
     Branch:   autopilot/a1b2c3d4
     tmux:     autopilot-a1b2c3d4

     autopilot status              — check progress
     autopilot logs --session a1b2c3d4  — view logs
     autopilot attach a1b2c3d4          — attach to session
     autopilot stop a1b2c3d4            — stop task
   ```

3. **Check progress** at any time:

   ```bash
   autopilot status
   ```

   Or use the interactive dashboard:

   ```bash
   autopilot status --watch
   ```

4. **View logs** to see what the agent is doing:

   ```bash
   autopilot logs
   ```

5. **Wait for completion.** The orchestrator will:
   - Create a branch and open a draft PR
   - Request a Copilot review
   - Fix review comments automatically
   - Loop until the review is clean (or max iterations reached)

   Close your laptop — it keeps going in tmux.

> **Running locally?** See [Running Locally](local-setup.md) for setup instructions.

## Start from a GitHub Issue

```bash
autopilot start --issue 123
```

The issue title and body are fetched and used as the prompt.

## Plan Mode

For complex tasks, let the agent plan before implementing:

```bash
autopilot start --prompt "Add pagination to all API endpoints" --plan
```

The agent creates a plan first, then implements it in a single pass.

## Stopping and Restarting

```bash
# Stop a running task
autopilot stop a1b2c3d4

# Restart it later (resumes from where it left off)
autopilot restart a1b2c3d4
```

## Resume from an Existing PR

If you already have a PR with Copilot review comments:

```bash
autopilot resume --pr 42345
```

This checks out the PR branch and starts fixing review comments.

## Fix CI Failures

```bash
# Interactive — pick which checks to fix
autopilot fix-ci --pr 42345

# Non-interactive — specify checks by name (substring match)
autopilot fix-ci --pr 42345 --checks "build-ubuntu,test-integration"
```

## Next Steps

- [Configuration Reference](configuration.md) — customize model, timeouts, and more
- [CI Fix Workflow](fix-ci-workflow.md) — detailed CI fix guide
- [Dotfiles Setup](dotfiles-setup.md) — auto-install in every Codespace
- [Troubleshooting](troubleshooting.md) — common issues and fixes
