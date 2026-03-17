# Configuration Reference

autopilot-loop is configured via a JSON file. All values have sensible defaults — a config file is optional.

## Config File Location

Search order (first found wins):

1. `./autopilot.json` — repo root (committed, shared with team)
2. `~/.autopilot-loop/config.json` — user-level (personal overrides)

## Full Example

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
  "custom_instructions": "",
  "ci_check_names": [],
  "ci_poll_interval_seconds": 120,
  "ci_poll_timeout_seconds": 5400
}
```

## Field Reference

### Core

| Field | Type | Default | Description |
|---|---|---|---|
| `model` | string | `"claude-opus-4.6"` | Model passed to `copilot -p --model`. Override per-task with `--model` flag. |
| `max_iterations` | int | `5` | Maximum review-fix cycles before the task completes. Must be >= 1. |
| `max_retries_per_phase` | int | `1` | Retries if agent fails (non-zero exit) within a single phase. Must be >= 0. |
| `branch_pattern` | string | `"autopilot/{task_id}"` | Branch naming pattern. Must contain `{task_id}`. |
| `custom_instructions` | string | `""` | Extra instructions appended to every agent prompt. Use for repo-specific context (test commands, lint rules, etc.). |

### Workspace

| Field | Type | Default | Description |
|---|---|---|---|
| `add_dirs` | list or null | `null` (auto) | Directories to give the agent read access to via `--add-dir`. By default, all sibling git repos in the workspace are auto-discovered. Set to `[]` to disable, or `["/path/to/repo"]` to override. |

### Review Loop

| Field | Type | Default | Description |
|---|---|---|---|
| `reviewer` | string | `"copilot-pull-request-reviewer[bot]"` | GitHub login of the reviewer to request. Normally leave as default. |
| `review_poll_interval_seconds` | int | `60` | How often to poll for review completion. Must be >= 10. |
| `review_timeout_seconds` | int | `3600` | Max wait for a review before failing the task. Must be >= 60. 1 hour default. |
| `agent_timeout_seconds` | int | `1800` | Max time for a single `copilot -p` invocation. Must be >= 60. 30 min default. |

### CI Fix Mode

| Field | Type | Default | Description |
|---|---|---|---|
| `ci_check_names` | list | `[]` | Pre-configured CI check names for `fix-ci`. When set, skips interactive selection. Uses substring matching. |
| `ci_poll_interval_seconds` | int | `120` | How often to poll for CI completion after a fix push. |
| `ci_poll_timeout_seconds` | int | `5400` | Max wait for CI to complete. 90 min default. |

### Codespace

| Field | Type | Default | Description |
|---|---|---|---|
| `idle_timeout_minutes` | int | `120` | Codespace idle timeout to set when a task starts. Only applied if the current timeout is lower. Capped by org policy. |
| `idle_timeout_enabled` | bool | `true` | Set to `false` to skip idle timeout extension entirely. Useful for repos where you don't want tasks to keep Codespaces alive longer. |
| `keepalive_enabled` | bool | `false` | Enable a background heartbeat thread as a fallback if the idle timeout API doesn't work. |
| `keepalive_interval_seconds` | int | `300` | Heartbeat interval when keepalive is enabled. |

## CLI Overrides

Some config values can be overridden per-task via CLI flags:

```bash
autopilot start --model gpt-4.1 --max-iters 3 --prompt "..."
autopilot fix-ci --pr 123 --model gpt-4.1 --max-iters 2
```

CLI flags take precedence over config file values.

## Validation Rules

| Field | Rule |
|---|---|
| `max_iterations` | Must be >= 1 |
| `max_retries_per_phase` | Must be >= 0 |
| `review_poll_interval_seconds` | Must be >= 10 |
| `review_timeout_seconds` | Must be >= 60 |
| `agent_timeout_seconds` | Must be >= 60 |
| `branch_pattern` | Must contain `{task_id}` |

Invalid values raise an error at startup before any work begins.
