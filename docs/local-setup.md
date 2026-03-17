# Running Locally

autopilot-loop works on any macOS or Linux machine — not just GitHub Codespaces.

## Prerequisites

| Requirement | Install |
|---|---|
| **`copilot` CLI** | [GitHub Copilot CLI](https://docs.github.com/en/copilot/github-copilot-in-the-cli) |
| **`gh` CLI** | [cli.github.com](https://cli.github.com) |
| **`git`** | Pre-installed on most systems |
| **Python 3.8+** | [python.org](https://www.python.org/downloads/) |
| **tmux** (optional) | `brew install tmux` (macOS) or `apt install tmux` (Linux) |

tmux is recommended but not required. Without tmux, tasks run in the foreground.

## Setup

1. **Install the tool:**

   ```bash
   pip install git+https://github.com/chanakyav/autopilot-loop.git
   ```

2. **Authenticate the `gh` CLI:**

   ```bash
   gh auth login
   ```

3. **Verify your setup:**

   ```bash
   autopilot doctor
   ```

   You should see all checks passing:

   ```
   autopilot doctor

     ✓  copilot CLI  found
     ✓  gh CLI       found
     ✓  gh auth      authenticated
     ✓  git          found
     ✓  git repo     inside a git repository
     ✓  tmux         found
     ✓  environment  local workspace

   All checks passed. Ready to use autopilot-loop.
   ```

4. **Start using autopilot:**

   ```bash
   cd your-repo
   autopilot start --prompt "Refactor UserService: extract billing logic"
   ```

## What's Different from Codespaces

| Feature | Codespace | Local |
|---|---|---|
| **Idle timeout extension** | Automatically extends codespace timeout | Skipped (not applicable) |
| **Keep-alive heartbeat** | Optional fallback | Skipped (not applicable) |
| **tmux** | Pre-installed | Optional; install with `brew install tmux` or `apt install tmux` |
| **`copilot` CLI** | Pre-installed | Must install manually |
| **`gh` CLI** | Pre-installed and authenticated | Must install and run `gh auth login` |
| **Sibling repo discovery** | Scans `/workspaces/` | Scans parent of CWD; override with `add_dirs` config |

## Configuration

Codespace-specific settings are automatically skipped when running locally:

- `idle_timeout_minutes` — ignored outside Codespaces
- `idle_timeout_enabled` — ignored outside Codespaces
- `keepalive_enabled` — still functional but only useful in Codespaces
- `keepalive_interval_seconds` — same as above

### Workspace Discovery

By default, autopilot-loop auto-discovers sibling git repositories under the parent of your current directory and gives the agent read access via `--add-dir`. On a local machine, this may include unrelated repos. To control this:

```json
{
  "add_dirs": []
}
```

Or specify explicit paths:

```json
{
  "add_dirs": ["/path/to/related-repo"]
}
```

## Troubleshooting

Run `autopilot doctor` to diagnose issues. See also [Troubleshooting](troubleshooting.md).
