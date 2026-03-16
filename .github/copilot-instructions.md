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
- **Persistence changes need schema migration** — bump `SCHEMA_VERSION`, add to `_MIGRATIONS` list, add column to `SCHEMA`, add to `_TASK_COLUMNS` frozenset
- **Terminal states are `COMPLETE`, `FAILED`, `STOPPED`** — use `TERMINAL_STATES` from orchestrator, not hardcoded tuples
- **Branch locking** — `get_tasks_on_branch()` prevents concurrent tasks on the same branch

## When Adding a New CLI Command

1. Add `cmd_<name>(args)` function in `cli.py`
2. Add parser in `main()` under the subparsers section
3. Add dispatch in the `if/elif` chain in `main()`
4. Update the module docstring at the top of `cli.py`
5. Update the Commands section in `README.md`

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
