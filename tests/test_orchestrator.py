"""Tests for the orchestrator state machine."""

from unittest.mock import patch

import pytest

from autopilot_loop import persistence
from autopilot_loop.agent import AgentResult
from autopilot_loop.orchestrator import Orchestrator


@pytest.fixture(autouse=True)
def use_temp_db(tmp_path, monkeypatch):
    monkeypatch.setattr(persistence, "DB_DIR", str(tmp_path))
    monkeypatch.setattr(persistence, "DB_PATH", str(tmp_path / "state.db"))


@pytest.fixture
def config():
    return {
        "model": "test-model",
        "max_iterations": 3,
        "max_retries_per_phase": 1,
        "reviewer": "Copilot",
        "review_poll_interval_seconds": 1,
        "review_timeout_seconds": 5,
        "agent_timeout_seconds": 60,
        "idle_timeout_minutes": 120,
        "branch_pattern": "autopilot/{task_id}",
        "custom_instructions": "",
    }


def _create_test_task(task_id="test1", prompt="test prompt", plan_mode=False):
    persistence.create_task(task_id, prompt, max_iterations=3, plan_mode=plan_mode, model="test-model")
    persistence.update_task(task_id, branch="autopilot/%s" % task_id)
    return task_id


def _mock_agent_result(exit_code=0, duration=10.0):
    return AgentResult(
        exit_code=exit_code,
        session_file="/tmp/session.md",
        stdout="agent output",
        stderr="",
        duration=duration,
    )


class TestOrchestratorInit:
    def test_init_transitions_to_implement(self, config):
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        next_state = orch._do_init()
        assert next_state == "IMPLEMENT"

    def test_init_transitions_to_plan_when_flag_set(self, config):
        task_id = _create_test_task(plan_mode=True)
        orch = Orchestrator(task_id, config)
        next_state = orch._do_init()
        assert next_state == "PLAN_AND_IMPLEMENT"


class TestOrchestratorImplement:
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_implement_success(self, mock_run, config):
        mock_run.return_value = _mock_agent_result()
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        assert orch._do_implement() == "VERIFY_PR"

    @patch("autopilot_loop.orchestrator.run_agent")
    def test_implement_failure_with_retry(self, mock_run, config):
        mock_run.return_value = _mock_agent_result(exit_code=1)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        # Both attempts fail → FAILED
        assert orch._do_implement() == "FAILED"
        # Should have been called twice (original + 1 retry)
        assert mock_run.call_count == 2


class TestOrchestratorVerifyPR:
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    def test_pr_found(self, mock_find, config):
        mock_find.return_value = 42
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        assert orch._do_verify_pr() == "REQUEST_REVIEW"
        task = persistence.get_task(task_id)
        assert task["pr_number"] == 42

    @patch("autopilot_loop.orchestrator.run_agent")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    def test_pr_not_found_retries(self, mock_find, mock_run, config):
        mock_find.side_effect = [None, 42]  # fail first, succeed after retry implement
        mock_run.return_value = _mock_agent_result()
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        assert orch._do_verify_pr() == "REQUEST_REVIEW"


class TestOrchestratorWaitReview:
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    def test_review_received(self, mock_check, config):
        mock_check.return_value = True
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "PARSE_REVIEW"

    @patch("autopilot_loop.orchestrator.time.sleep")
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    def test_review_timeout(self, mock_check, mock_sleep, config):
        mock_check.return_value = False
        config["review_timeout_seconds"] = 0  # Immediate timeout
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "COMPLETE"


class TestOrchestratorParseReview:
    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_no_comments_completes(self, mock_review, mock_comments, config, tmp_path):
        mock_review.return_value = {"body": "LGTM"}
        mock_comments.return_value = []
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_comments_trigger_fix(self, mock_review, mock_comments, config, tmp_path):
        mock_review.return_value = {"body": "issues found"}
        mock_comments.return_value = [{"path": "a.rb", "body": "fix this"}]
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "FIX"

    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_max_iterations_completes(self, mock_review, mock_comments, config, tmp_path):
        mock_review.return_value = {"body": "issues"}
        mock_comments.return_value = [{"path": "a.rb", "body": "fix"}]
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, iteration=3)  # At max
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "COMPLETE"


class TestOrchestratorFix:
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_fix_success(self, mock_run, mock_review, mock_comments, mock_sha, config):
        mock_run.return_value = _mock_agent_result()
        mock_review.return_value = {"body": "review"}
        mock_comments.return_value = [{"path": "a.rb", "original_line": 10, "body": "fix"}]
        mock_sha.return_value = "abc123"

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, iteration=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fix() == "VERIFY_PUSH"


class TestOrchestratorFullLoop:
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_clean_pr_no_comments(
        self, mock_run, mock_find_pr, mock_review, mock_comments,
        mock_request, mock_is_complete, mock_sha, mock_verify, mock_timeout,
        config,
    ):
        """Full loop: implement → verify PR → request review → wait → parse → COMPLETE (no comments)."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_review.return_value = {"body": "LGTM"}
        mock_comments.return_value = []
        mock_is_complete.return_value = True

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_copilot_inline_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_one_fix_iteration(
        self, mock_run, mock_find_pr, mock_review, mock_comments,
        mock_request, mock_is_complete, mock_sha, mock_verify, mock_timeout,
        config,
    ):
        """Full loop with one fix iteration."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_is_complete.return_value = True
        mock_sha.return_value = "sha1"
        mock_verify.return_value = True

        # First review: 1 comment. Second review: clean.
        mock_review.side_effect = [
            {"body": "issues"},
            {"body": "LGTM"},
            {"body": "LGTM"},
        ]
        mock_comments.side_effect = [
            [{"path": "a.rb", "original_line": 10, "body": "fix this"}],
            [{"path": "a.rb", "original_line": 10, "body": "fix this"}],
            [],
        ]

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"
        task = persistence.get_task(task_id)
        assert task["iteration"] == 2  # One fix iteration
