"""Configuration loading and validation.

Loads autopilot.json from the current directory or ~/.autopilot-loop/config.json,
merges with CLI argument overrides, and validates.
"""

import json
import logging
import os

logger = logging.getLogger(__name__)

__all__ = ["load_config", "DEFAULTS"]

DEFAULTS = {
    "model": "claude-opus-4.6",
    "max_iterations": 5,
    "max_retries_per_phase": 1,
    "reviewer": "copilot-pull-request-reviewer[bot]",
    "review_poll_interval_seconds": 60,
    "review_timeout_seconds": 3600,
    "agent_timeout_seconds": 1800,
    "idle_timeout_minutes": 120,
    "keepalive_enabled": False,
    "keepalive_interval_seconds": 300,
    "branch_pattern": "autopilot/{task_id}",
    "custom_instructions": "",
}

CONFIG_FILENAMES = [
    "autopilot.json",
    os.path.join(os.path.expanduser("~"), ".autopilot-loop", "config.json"),
]


def load_config(cli_overrides=None):
    """Load config from file, apply defaults and CLI overrides.

    Search order:
    1. ./autopilot.json (current directory)
    2. ~/.autopilot-loop/config.json

    Returns a dict with all config keys populated.
    """
    config = dict(DEFAULTS)

    # Load from file
    for path in CONFIG_FILENAMES:
        if os.path.isfile(path):
            logger.info("Loading config from %s", path)
            with open(path, "r") as f:
                file_config = json.load(f)
            config.update(file_config)
            break
    else:
        logger.info("No config file found, using defaults")

    # Apply CLI overrides (only non-None values)
    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None:
                config[key] = value

    _validate(config)
    return config


def _validate(config):
    """Validate config values."""
    if config["max_iterations"] < 1:
        raise ValueError("max_iterations must be >= 1, got %d" % config["max_iterations"])
    if config["max_retries_per_phase"] < 0:
        raise ValueError("max_retries_per_phase must be >= 0, got %d" % config["max_retries_per_phase"])
    if config["review_poll_interval_seconds"] < 10:
        raise ValueError("review_poll_interval_seconds must be >= 10, got %d" % config["review_poll_interval_seconds"])
    if config["review_timeout_seconds"] < 60:
        raise ValueError("review_timeout_seconds must be >= 60, got %d" % config["review_timeout_seconds"])
    if config["agent_timeout_seconds"] < 60:
        raise ValueError("agent_timeout_seconds must be >= 60, got %d" % config["agent_timeout_seconds"])
    if "{task_id}" not in config["branch_pattern"]:
        raise ValueError("branch_pattern must contain {task_id}, got '%s'" % config["branch_pattern"])
