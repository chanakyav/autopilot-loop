"""Codespace idle timeout management via gh api."""

import logging
import os
import subprocess

logger = logging.getLogger(__name__)


def set_idle_timeout(minutes=120):
    """Set the idle timeout for the current codespace.

    Uses: gh api -X PATCH "/user/codespaces/$CODESPACE_NAME" -F idle_timeout_minutes=<value>

    Non-fatal — logs a warning if it fails (e.g., not in a codespace).
    """
    codespace_name = os.environ.get("CODESPACE_NAME")
    if not codespace_name:
        logger.debug("Not in a codespace (CODESPACE_NAME not set), skipping idle timeout")
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
