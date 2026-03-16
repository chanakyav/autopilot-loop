# Troubleshooting

## Common Issues

### `copilot: command not found`

The `copilot` CLI is not installed or not in your PATH.

**In Codespaces:** It should be pre-installed if your org has Copilot enabled. Try:

```bash
which copilot
```

If missing, check with your org admin that Copilot is enabled for your repository.

### `gh: command not found`

The `gh` CLI is not installed. In most Codespaces it's pre-installed. Otherwise:

```bash
# Ubuntu/Debian
sudo apt install gh

# macOS
brew install gh
```

Then authenticate: `gh auth login`

### `tmux not found`

```bash
# Ubuntu/Debian
sudo apt install tmux

# macOS
brew install tmux
```

### `Error: branch autopilot/... already has an active task`

Another task is already running on this branch. Options:

```bash
# Stop the existing task first
autopilot stop <task_id>

# Or switch to the default branch and start fresh
git checkout main
autopilot start --prompt "..."
```

### Task stuck in `WAIT_REVIEW`

The Copilot review is taking longer than expected. This can happen when:

- The PR is large (many files changed)
- GitHub is experiencing delays

Check the timeout: default is 3600 seconds (1 hour). You can increase it:

```json
{
  "review_timeout_seconds": 7200
}
```

If the task times out, it transitions to `FAILED`. You can restart it:

```bash
autopilot restart <task_id>
```

### Task stuck in `WAIT_CI`

CI takes a while. Check your CI pipeline directly:

```bash
gh pr checks <pr_number>
```

Default CI timeout is 5400 seconds (90 minutes). Adjust with:

```json
{
  "ci_poll_timeout_seconds": 7200
}
```

### Agent times out (`SIGTERM` in logs)

The `copilot -p` subprocess exceeded `agent_timeout_seconds` (default: 1800s / 30 min).

This happens with very large tasks. Options:

1. Increase the timeout:

   ```json
   {
     "agent_timeout_seconds": 3600
   }
   ```

2. Break the task into smaller prompts

3. Use plan mode for complex tasks: `autopilot start --prompt "..." --plan`

### No PR was created

If the task fails at `VERIFY_PR`, the agent didn't create a PR. Check the session logs:

```bash
autopilot logs --session <task_id> --phase implement
```

Common causes:
- The prompt was too vague — be specific about what to change
- The agent couldn't find the relevant code — add `custom_instructions` to your config
- Git authentication issues in the Codespace

### Dashboard shows garbled output

If the interactive dashboard (`--watch`) looks broken:

1. Make sure your terminal supports ANSI escape codes
2. Try a wider terminal (minimum ~80 columns recommended)
3. Set `NO_COLOR=1` to disable colors: `NO_COLOR=1 autopilot status --watch`
4. Fall back to non-interactive: `autopilot status` (table) or `autopilot status --json`

## tmux Tips

### Quick Access

Add to your `~/.tmux.conf`:

```bash
# Bind Ctrl-g to open autopilot dashboard in a popup
bind g display-popup -E -w 80% -h 60% "autopilot status --watch"
```

### Attach to a Session

```bash
# From inside tmux — switches to the session
autopilot attach <task_id>

# From outside tmux — attaches to the session
tmux attach -t autopilot-<task_id>
```

### List All autopilot Sessions

```bash
tmux list-sessions | grep autopilot
```

### Mouse Scrolling

Mouse scrolling is enabled automatically in autopilot tmux sessions. If it's not working, add to `~/.tmux.conf`:

```bash
set -g mouse on
```

## Logs

Logs are stored in `~/.autopilot-loop/sessions/<task_id>/`:

```
orchestrator.log    — Full orchestrator log (state transitions, API calls)
implement/          — Session files from the implement phase
fix-1/              — Session files from the first fix iteration
fix-2/              — Session files from the second fix iteration
```

View logs:

```bash
# Latest task's orchestrator log
autopilot logs

# Specific task
autopilot logs --session <task_id>

# Specific phase
autopilot logs --session <task_id> --phase fix-1
```

## Getting Help

- Check `autopilot --help` and `autopilot <command> --help` for all options
- Review the [Configuration Reference](configuration.md) for all settings
- File an issue on the repo for bugs or feature requests
