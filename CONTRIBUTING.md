# Contributing to autopilot-loop

Thanks for your interest in contributing! This guide will help you get set up and submit a clean PR.

## Development Setup

```bash
git clone https://github.com/chanakyav/autopilot-loop.git
cd autopilot-loop
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Pre-commit Hook (Recommended)

Install [pre-commit](https://pre-commit.com/) so lint runs automatically before each commit:

```bash
pip install pre-commit
pre-commit install
```

This catches lint errors locally before you push, saving time on CI.

## Before You Push

Run these two commands and make sure both pass with zero errors:

```bash
ruff check src/ tests/          # lint
python -m pytest tests/ -v      # tests
```

`ruff check --fix src/ tests/` can auto-fix many common issues (unused imports, import ordering).

## Code Conventions

- **Python 3.8+ compatible** — no walrus operator (`:=`), no `str.removeprefix`, use `%` formatting instead of f-strings
- **No `os.execvp`** — use `subprocess.run` instead
- **Import ordering** — ruff enforces isort-style ordering; alphabetize within groups
- **No unused imports or variables** — ruff catches these (`F401`, `F841`)
- **CLI output uses `_launch_in_tmux()`** — all commands that start tmux sessions go through this shared helper
- **Dashboard rendering lives in `dashboard.py`** — not in `cli.py`
- **Tests required** — every new function, helper, or behavior needs tests

## Submitting a Pull Request

1. Fork the repo and create a feature branch
2. Make your changes
3. Run lint and tests (see above)
4. Push and open a PR against `main`
5. Link any related issue with `Closes #N` in the PR description
6. Fill out the PR checklist — the template will guide you

## Project Structure

```
src/autopilot_loop/
    cli.py          # CLI commands and argument parsing
    dashboard.py    # Interactive TUI (alternate screen buffer)
    persistence.py  # SQLite task storage and schema migrations
    orchestrator.py # Main implement-review-fix loop
    agent.py        # Copilot agent interaction
    github_api.py   # GitHub API calls (reviews, comments, PRs)
    config.py       # Configuration loading
    prompts.py      # Prompt templates
tests/
    test_cli.py         # CLI helper tests
    test_dashboard.py   # Dashboard/TUI tests
    test_persistence.py # Database and migration tests
    ...
```

## Questions?

Open an issue — happy to help.
