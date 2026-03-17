"""Tests for config loading and validation."""

import json

import pytest

from autopilot_loop.config import DEFAULTS, load_config


def test_defaults_returned_with_no_config_file():
    config = load_config()
    assert config["model"] == "claude-opus-4.6"
    assert config["max_iterations"] == 5
    assert config["max_retries_per_phase"] == 1
    assert config["reviewer"] == "copilot-pull-request-reviewer[bot]"
    assert config["review_poll_interval_seconds"] == 60
    assert config["review_timeout_seconds"] == 3600
    assert config["agent_timeout_seconds"] == 1800
    assert config["idle_timeout_minutes"] == 120
    assert config["idle_timeout_enabled"] is True
    assert config["keepalive_enabled"] is False
    assert "{task_id}" in config["branch_pattern"]


def test_cli_overrides_applied():
    config = load_config({"model": "gpt-5", "max_iterations": 10})
    assert config["model"] == "gpt-5"
    assert config["max_iterations"] == 10


def test_none_cli_overrides_ignored():
    config = load_config({"model": None, "max_iterations": None})
    assert config["model"] == DEFAULTS["model"]
    assert config["max_iterations"] == DEFAULTS["max_iterations"]


def test_config_file_loaded(tmp_path, monkeypatch):
    config_file = tmp_path / "autopilot.json"
    config_file.write_text(json.dumps({"model": "test-model", "max_iterations": 3}))
    monkeypatch.chdir(tmp_path)

    # Patch CONFIG_FILENAMES to use our temp file
    import autopilot_loop.config as config_module
    original = config_module.CONFIG_FILENAMES
    config_module.CONFIG_FILENAMES = [str(config_file)]
    try:
        config = load_config()
        assert config["model"] == "test-model"
        assert config["max_iterations"] == 3
        # Defaults still present for unset keys
        assert config["reviewer"] == "copilot-pull-request-reviewer[bot]"
    finally:
        config_module.CONFIG_FILENAMES = original


def test_cli_overrides_beat_file(tmp_path, monkeypatch):
    config_file = tmp_path / "autopilot.json"
    config_file.write_text(json.dumps({"model": "file-model"}))

    import autopilot_loop.config as config_module
    original = config_module.CONFIG_FILENAMES
    config_module.CONFIG_FILENAMES = [str(config_file)]
    try:
        config = load_config({"model": "cli-model"})
        assert config["model"] == "cli-model"
    finally:
        config_module.CONFIG_FILENAMES = original


def test_validation_max_iterations():
    with pytest.raises(ValueError, match="max_iterations"):
        load_config({"max_iterations": 0})


def test_validation_max_retries():
    with pytest.raises(ValueError, match="max_retries_per_phase"):
        load_config({"max_retries_per_phase": -1})


def test_validation_poll_interval():
    with pytest.raises(ValueError, match="review_poll_interval_seconds"):
        load_config({"review_poll_interval_seconds": 5})


def test_validation_review_timeout():
    with pytest.raises(ValueError, match="review_timeout_seconds"):
        load_config({"review_timeout_seconds": 30})


def test_validation_agent_timeout():
    with pytest.raises(ValueError, match="agent_timeout_seconds"):
        load_config({"agent_timeout_seconds": 10})


def test_validation_branch_pattern():
    with pytest.raises(ValueError, match="branch_pattern"):
        load_config({"branch_pattern": "no-task-id-placeholder"})
