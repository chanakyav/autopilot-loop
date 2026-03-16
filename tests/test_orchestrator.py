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
        "reviewer": "copilot-pull-request-reviewer[bot]",
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
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_no_comments_completes(self, mock_review, mock_unresolved, config, tmp_path):
        mock_review.return_value = {"id": 100, "body": "LGTM"}
        mock_unresolved.return_value = []
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_comments_trigger_fix(self, mock_review, mock_unresolved, config, tmp_path):
        mock_review.return_value = {"id": 100, "body": "issues found"}
        mock_unresolved.return_value = [{"id": 1, "thread_id": "T1", "path": "a.rb", "body": "fix this"}]
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "FIX"

    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_max_iterations_completes(self, mock_review, mock_unresolved, config, tmp_path):
        mock_review.return_value = {"id": 100, "body": "issues"}
        mock_unresolved.return_value = [{"id": 1, "thread_id": "T1", "path": "a.rb", "body": "fix"}]
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, iteration=3)  # At max
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_parse_review() == "COMPLETE"


class TestOrchestratorFix:
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_fix_success(self, mock_run, mock_review, mock_unresolved, mock_sha, config):
        mock_run.return_value = _mock_agent_result()
        mock_review.return_value = {"id": 100, "body": "review"}
        mock_unresolved.return_value = [{"id": 1, "thread_id": "T1", "path": "a.rb", "line": 10, "body": "fix"}]
        mock_sha.return_value = "abc123"

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, iteration=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fix() == "VERIFY_PUSH"


class TestOrchestratorFullLoop:
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_clean_pr_no_comments(
        self, mock_run, mock_find_pr, mock_review, mock_unresolved,
        mock_request, mock_is_complete, mock_sha, mock_verify,
        mock_reply, mock_resolve, mock_timeout,
        config,
    ):
        """Full loop: implement → verify PR → request review → wait → parse → COMPLETE."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_review.return_value = {"id": 100, "body": "LGTM"}
        mock_unresolved.return_value = []
        mock_is_complete.return_value = True

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.is_copilot_review_complete")
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_one_fix_iteration(
        self, mock_run, mock_find_pr, mock_review, mock_unresolved,
        mock_request, mock_is_complete, mock_sha, mock_verify,
        mock_reply, mock_resolve, mock_timeout,
        config,
    ):
        """Full loop with one fix iteration: comments → fix → resolve → re-review → clean."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_is_complete.return_value = True
        mock_sha.return_value = "sha1"
        mock_verify.return_value = True

        # First pass: 1 unresolved comment. After fix+resolve: 0 unresolved.
        mock_review.return_value = {"id": 100, "body": "review"}
        mock_unresolved.side_effect = [
            [{"id": 1, "thread_id": "T1", "path": "a.rb", "line": 10, "body": "fix this"}],
            [{"id": 1, "thread_id": "T1", "path": "a.rb", "line": 10, "body": "fix this"}],
            [],
        ]

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"
        task = persistence.get_task(task_id)
        # iteration 1: parse found comments, 2: parse after fix found comments (resolved), 3: clean
        assert task["iteration"] >= 2
        # Verify comments were resolved
        assert mock_reply.called
        assert mock_resolve.called

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_resume_starts_at_parse_review(
        self, mock_review, mock_unresolved, mock_timeout, config,
    ):
        """Resume skips REQUEST_REVIEW and goes straight to PARSE_REVIEW."""
        mock_review.return_value = {"id": 100, "body": "LGTM"}
        mock_unresolved.return_value = []

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, state="PARSE_REVIEW")
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"
        # Should NOT have been called — we skipped REQUEST_REVIEW
        # (no request_copilot_review mock needed = it was never called)


class TestOrchestratorResolveComments:
    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_resolves_with_fix_summary(
        self, mock_unresolved, mock_sha, mock_reply, mock_resolve, config, tmp_path,
    ):
        """RESOLVE_COMMENTS reads fix summary and posts correct replies."""
        import json as _json
        import os

        mock_sha.return_value = "abc1234"
        mock_unresolved.return_value = [
            {"id": 10, "thread_id": "T10", "path": "a.rb", "line": 5, "body": "fix this"},
            {"id": 20, "thread_id": "T20", "path": "b.rb", "line": 8, "body": "style nit"},
        ]

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_comments = mock_unresolved.return_value

        # Write a fix summary file
        summary = [
            {"comment_id": 10, "status": "fixed", "message": "Added null check"},
            {"comment_id": 20, "status": "skipped", "message": "Style is intentional"},
        ]
        summary_path = os.path.join(os.getcwd(), ".autopilot-fix-summary.json")
        with open(summary_path, "w") as f:
            _json.dump(summary, f)

        try:
            result = orch._do_resolve_comments()
        finally:
            if os.path.exists(summary_path):
                os.remove(summary_path)

        assert result == "REQUEST_REVIEW"
        assert mock_reply.call_count == 2
        assert mock_resolve.call_count == 2

        # Check reply content
        first_reply = mock_reply.call_args_list[0]
        assert "Addressed" in first_reply[0][2]
        assert "abc1234" in first_reply[0][2]

        second_reply = mock_reply.call_args_list[1]
        assert "Skipped" in second_reply[0][2]
        assert "Style is intentional" in second_reply[0][2]

    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_resolves_without_summary_file(
        self, mock_unresolved, mock_sha, mock_reply, mock_resolve, config,
    ):
        """RESOLVE_COMMENTS works even without a fix summary file (defaults to 'fixed')."""
        mock_sha.return_value = "def5678"
        comments = [{"id": 10, "thread_id": "T10", "path": "a.rb", "line": 5, "body": "fix"}]
        mock_unresolved.return_value = comments

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_comments = comments

        result = orch._do_resolve_comments()
        assert result == "REQUEST_REVIEW"
        assert mock_reply.call_count == 1
        assert "Addressed" in mock_reply.call_args[0][2]
        assert mock_resolve.call_count == 1


class TestOrchestratorVerifyPush:
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    def test_new_commits_found(self, mock_verify, mock_sha, config):
        mock_verify.return_value = True
        task_id = _create_test_task()
        persistence.update_task(task_id, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._pre_fix_sha = "old_sha"
        assert orch._do_verify_push() == "RESOLVE_COMMENTS"

    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    def test_no_commits_retries_fix(self, mock_verify, mock_sha, config):
        mock_verify.return_value = False
        mock_sha.return_value = "same_sha"
        task_id = _create_test_task()
        persistence.update_task(task_id, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._pre_fix_sha = "same_sha"
        assert orch._do_verify_push() == "FIX"

    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    def test_no_commits_after_retry_fails(self, mock_verify, mock_sha, config):
        mock_verify.return_value = False
        mock_sha.return_value = "same_sha"
        task_id = _create_test_task()
        persistence.update_task(task_id, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._pre_fix_sha = "same_sha"
        orch._retry_counts["VERIFY_PUSH_FIX_RETRY"] = 1  # Already retried
        assert orch._do_verify_push() == "FAILED"
