"""Codespace idle timeout management via gh api."""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def get_idle_timeout():
    """Get the current idle timeout for the codespace, or None if not in a codespace."""
    codespace_name = os.environ.get("CODESPACE_NAME")
    if not codespace_name:
        return None

    try:
        result = subprocess.run(
            [
                "gh", "api",
                "/user/codespaces/%s" % codespace_name,
                "--jq", ".idle_timeout_minutes",
            ],
            capture_output=True, text=True, check=True,
        )
        return int(result.stdout.strip())
    except (FileNotFoundError, subprocess.CalledProcessError, ValueError):
        return None


def set_idle_timeout(minutes=120):
    """Set the idle timeout for the current codespace.

    Uses: gh api -X PATCH "/user/codespaces/$CODESPACE_NAME" -F idle_timeout_minutes=<value>

    Skips the update if the current timeout is already >= the desired value.
    Non-fatal — logs a warning if it fails (e.g., not in a codespace).
    """
    codespace_name = os.environ.get("CODESPACE_NAME")
    if not codespace_name:
        logger.debug("Not in a codespace (CODESPACE_NAME not set), skipping idle timeout")
        return

    # Check current timeout before updating
    current = get_idle_timeout()
    if current is not None and current >= minutes:
        logger.info("Codespace idle timeout already set to %dm (>= %dm), skipping", current, minutes)
        return

    try:
        subprocess.run(
            [
                "gh", "api", "-X", "PATCH",
                "/user/codespaces/%s" % codespace_name,
                "-F", "idle_timeout_minutes=%d" % minutes,
            ],
            capture_output=True, text=True, check=True,
        )
        logger.info("Codespace idle timeout set to %d minutes", minutes)
    except FileNotFoundError:
        logger.warning("gh CLI not found, cannot set codespace idle timeout")
    except subprocess.CalledProcessError as e:
        logger.warning("Failed to set codespace idle timeout: %s", e.stderr.strip())
