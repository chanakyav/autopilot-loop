# Copilot Instructions for autopilot-loop

## Before Every Commit

1. **Lint**: `python3 -m ruff check src/ tests/` — must pass with zero errors
2. **Tests**: `python3 -m pytest tests/` — all tests must pass
3. **No unused imports/variables** — ruff catches these (F401, F841); fix before pushing
4. **Import sorting** — ruff enforces isort-style ordering (I001); alphabetize within groups

## Code Conventions

- **Python 3.8+ compatible** — no walrus operator, no `str.removeprefix`, use `% formatting` not f-strings
- **CLI output uses `_launch_in_tmux()`** — all commands that start tmux sessions go through this shared helper; never duplicate tmux launch logic inline
- **Dashboard rendering lives in `dashboard.py`** — not in `cli.py`
- **Never use `os.execvp`** — use `subprocess.run` instead; `os.execvp` replaces the process and breaks callers, scripts, and error handling
- **TUI actions must be safe** — catch all errors and show a status message instead of crashing
- **TUI uses alternate screen buffer** — `_enter_tui()` / `_exit_tui()` in dashboard.py; always restore terminal in a `finally` block
- **Persistence changes need schema migration** — bump `SCHEMA_VERSION`, add to `_MIGRATIONS` list, add column to `SCHEMA`, add to `_TASK_COLUMNS` frozenset
- **Terminal states are `COMPLETE`, `FAILED`, `STOPPED`** — use `TERMINAL_STATES` from persistence (canonical source) or `_TERMINAL_STATES` from dashboard, not hardcoded tuples
- **Branch locking** — `_check_branch_lock(branch)` prevents concurrent tasks on the same branch; use it in every command that creates a task on a branch

## When Adding New Code

1. **Write tests** for every new function, helper, or behavior — no untested code ships
2. **Run lint + tests** before committing: `python3 -m ruff check src/ tests/ && python3 -m pytest tests/`
3. **Refactor duplication** — if logic appears in 2+ places, extract a helper (e.g. `_check_branch_lock`)
4. **Test edge cases** — invalid input, empty state, terminal states, error paths
5. **Test file placement** — CLI helpers in `test_cli.py`, dashboard in `test_dashboard.py`, etc.

## When Adding a New CLI Command

1. Add `cmd_<name>(args)` function in `cli.py`
2. Add parser in `main()` under the subparsers section
3. Add dispatch in the `if/elif` chain in `main()`
4. Add `_check_branch_lock(branch)` if the command creates a task on a branch
5. Add tests in `test_cli.py` for the new command's helpers/validation
6. Update the module docstring at the top of `cli.py`
7. Update the Commands section in `README.md`

## When Modifying the Database Schema

1. Bump `SCHEMA_VERSION` in `persistence.py`
2. Add column to `SCHEMA` string (the CREATE TABLE statement)
3. Add migration entry to `_MIGRATIONS` list: `(version, table, column, column_def)`
4. Add column name to `_TASK_COLUMNS` frozenset
5. Add a test in `test_persistence.py` verifying the new column persists
6. Update `test_migration_from_pre_versioned_db` to assert the new column migrates correctly

## PR Discipline

- Link issues with `Closes #N` in PR descriptions
- Batch related changes into one PR when they touch the same scope
- Keep CI green — check `gh pr checks <N>` before merging

## GitHub Issues, PRs & Comments

When creating or updating issues, PRs, or comments via `gh` CLI, always add a footer line:

```
---
🤖 **autopilot-loop**
```

This identifies automated contributions. Use this on:
- Issue bodies and comments
- PR bodies
- Review comment replies
