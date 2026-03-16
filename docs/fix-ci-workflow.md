# Fix CI Workflow

The `fix-ci` command automates fixing CI failures on an existing PR. The agent reads failure annotations, writes fixes, pushes, and waits for CI to re-run.

## Quick Start

```bash
# Interactive — shows failed checks, you pick which to fix
autopilot fix-ci --pr 42345

# Non-interactive — specify checks by name (substring match)
autopilot fix-ci --pr 42345 --checks "build-ubuntu,test-integration"
```

## How It Works

```
                 ┌──────────────────┐
                 │ FETCH_ANNOTATIONS│
                 └────────┬─────────┘
                          │
                ┌─────────▼──────────┐
                │      FIX_CI        │  Agent reads annotations,
                │  (copilot -p)      │  writes fixes, commits
                └─────────┬──────────┘
                          │
                ┌─────────▼──────────┐
                │   VERIFY_PUSH      │  Check for new commits
                └─────────┬──────────┘
                          │
                ┌─────────▼──────────┐
                │     WAIT_CI        │  Poll until CI re-runs
                └─────────┬──────────┘
                          │
                     ┌────▼────┐
                     │ Pass?   │
                     └────┬────┘
                    yes   │   no
                ┌─────────┤─────────┐
                ▼                   ▼
           COMPLETE          FETCH_ANNOTATIONS
                              (next iteration)
```

1. **FETCH_ANNOTATIONS** — Fetches failure annotations (error messages, file paths, line numbers) from the selected CI checks
2. **FIX_CI** — Passes annotations to `copilot -p` with a prompt instructing it to fix the failures
3. **VERIFY_PUSH** — Verifies the agent pushed new commits
4. **WAIT_CI** — Polls CI status until all selected checks complete
5. If checks still fail → loop back to FETCH_ANNOTATIONS (up to `max_iterations`)
6. If all checks pass → **COMPLETE**

## Step-by-Step Guide

### 1. Identify the Failing PR

```bash
# List your recent PRs
gh pr list --author @me

# Check which checks failed
gh pr checks 42345
```

### 2. Start the CI Fix

```bash
autopilot fix-ci --pr 42345
```

You'll see a numbered list of failed checks:

```
Failed CI checks on PR #42345:

  1. build-ubuntu
  2. test-integration
  3. lint

Which checks to fix? (comma-separated numbers, or 'all'): 1,2
```

### 3. Monitor Progress

```bash
# Quick status
autopilot status

# Interactive dashboard
autopilot status --watch

# Detailed logs
autopilot logs
```

### 4. Pre-Configure Checks (Optional)

If you always want to fix the same checks, add to `autopilot.json`:

```json
{
  "ci_check_names": ["build", "test"]
}
```

This uses substring matching — `"build"` matches `"build-ubuntu"`, `"build-macos"`, etc. When configured, `fix-ci` skips interactive selection.

## Tips

- **Increase iterations for flaky CI:** `autopilot fix-ci --pr 123 --max-iters 8`
- **Fix only specific checks:** `--checks "lint"` to target just linting failures
- **Check annotations first:** Use `gh run view <run-id>` to see what failed before starting
- CI fix tasks show as `ci` mode in `autopilot status`
