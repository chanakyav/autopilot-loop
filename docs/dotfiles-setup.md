# Auto-Install with Dotfiles

You can configure your [GitHub dotfiles](https://docs.github.com/en/codespaces/setting-your-user-preferences/personalizing-github-codespaces-for-your-account#dotfiles) to install autopilot-loop automatically in every new Codespace.

## Is It Safe?

**Yes.** Installing autopilot-loop does **not** change your Codespace idle timeout. The timeout is only extended when you explicitly run `autopilot start` (or `resume`, `fix-ci`). If you never run a task, nothing changes.

Specifically:
- The idle timeout extension happens inside the orchestrator's `INIT` state — only when a task begins running
- The timeout is **per-codespace**, not global — it doesn't affect your other Codespaces
- The timeout reverts when the Codespace is next stopped/restarted

## Option A: Dotfiles (Recommended)

Add to your dotfiles `install.sh`:

```bash
#!/bin/bash
# Install autopilot-loop in every Codespace
pip install git+https://github.com/chanakyav/autopilot-loop.git 2>/dev/null || true
```

This gives you `autopilot` available in every Codespace terminal immediately.

### Per-Repo Timeout Control

If you want different idle timeout behavior per repo, add an `autopilot.json` to each repo root:

```json
{
  "idle_timeout_minutes": 60
}
```

Set it lower for repos where you don't want long-running tasks. The default is 120 minutes (org-capped).

## Option B: Devcontainer (Per-Repo)

If you only want autopilot-loop in specific repos, add it to `.devcontainer/devcontainer.json`:

```json
{
  "postCreateCommand": "pip install git+https://github.com/chanakyav/autopilot-loop.git"
}
```

This installs only when this repo's Codespace is created.

## Option C: Manual Install

Install manually in each Codespace when needed:

```bash
pip install git+https://github.com/chanakyav/autopilot-loop.git
```

Simple but repetitive.

## Comparison

| Method | Scope | Auto-install | Idle Timeout Impact |
|---|---|---|---|
| **Dotfiles** | All Codespaces | Yes | None until `autopilot start` |
| **Devcontainer** | Per-repo | Yes | None until `autopilot start` |
| **Manual** | Per-codespace | No | None until `autopilot start` |
