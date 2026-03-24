"""Tests for the orchestrator state machine."""

import os
from unittest.mock import patch

import pytest

from autopilot_loop import persistence
from autopilot_loop.agent import AgentResult
from autopilot_loop.orchestrator import TERMINAL_STATES, CIOrchestrator, Orchestrator


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
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_review_received(self, mock_comments, config):
        mock_comments.return_value = [{"id": 1, "thread_id": "T1", "path": "a.rb", "body": "fix"}]
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "PARSE_REVIEW"

    @patch("autopilot_loop.orchestrator.time.sleep")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_review_timeout(self, mock_comments, mock_pending, mock_sleep, config):
        mock_comments.return_value = []
        mock_pending.return_value = True  # still pending, never finishes
        config["review_timeout_seconds"] = 0  # Immediate timeout
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_latest_copilot_review_thread_ts")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_clean_review_copilot_not_pending(
        self, mock_comments, mock_pending, mock_thread_ts, config,
    ):
        """0 unresolved + Copilot not pending + new thread -> PARSE_REVIEW."""
        mock_comments.return_value = []
        mock_pending.return_value = False
        mock_thread_ts.return_value = "2099-01-01T00:00:00Z"
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "PARSE_REVIEW"

    @patch("autopilot_loop.orchestrator.time.sleep")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_copilot_still_pending_keeps_polling(
        self, mock_comments, mock_pending, mock_sleep, config,
    ):
        """0 unresolved + still pending -> keeps polling until timeout."""
        mock_comments.return_value = []
        mock_pending.return_value = True
        config["review_timeout_seconds"] = 0
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        # With timeout=0 it will hit timeout on next iteration
        assert orch._do_wait_review() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_latest_copilot_review_thread_ts")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_copilot_not_pending_no_threads(
        self, mock_comments, mock_pending, mock_thread_ts, config,
    ):
        """0 unresolved + not pending + no threads -> still PARSE_REVIEW."""
        mock_comments.return_value = []
        mock_pending.return_value = False
        mock_thread_ts.return_value = None
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_review() == "PARSE_REVIEW"

    @patch("autopilot_loop.orchestrator.time.sleep")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_pending_check_error_continues_polling(
        self, mock_comments, mock_pending, mock_sleep, config,
    ):
        """API error in is_copilot_pending_reviewer -> falls back to polling."""
        mock_comments.return_value = []
        mock_pending.side_effect = Exception("API error")
        config["review_timeout_seconds"] = 0
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        # Error treated as "still pending" -> will hit timeout
        assert orch._do_wait_review() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_latest_copilot_review_thread_ts")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_wait_review_without_request_timestamp(
        self, mock_comments, mock_pending, mock_thread_ts, config,
    ):
        """Resume at WAIT_REVIEW without _review_requested_at set."""
        mock_comments.return_value = []
        mock_pending.return_value = False
        mock_thread_ts.return_value = "2099-01-01T00:00:00Z"
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        # _review_requested_at is NOT set — should use start_time fallback
        assert not hasattr(orch, "_review_requested_at")
        assert orch._do_wait_review() == "PARSE_REVIEW"


class TestOrchestratorParseReview:
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    def test_no_comments_completes(self, mock_review, mock_unresolved, config, tmp_path):
        mock_review.return_value = {"id": 100, "body": "LGTM", "state": "APPROVED"}
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
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_latest_copilot_review_thread_ts")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_clean_pr_no_comments(
        self, mock_run, mock_find_pr, mock_review, mock_unresolved,
        mock_pending, mock_thread_ts,
        mock_request, mock_sha, mock_verify,
        mock_reply, mock_resolve, mock_timeout,
        config,
    ):
        """Full loop: implement -> verify PR -> request review -> wait -> parse -> COMPLETE."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_review.return_value = {"id": 100, "body": "LGTM", "state": "APPROVED"}
        mock_unresolved.return_value = []
        mock_pending.return_value = False
        mock_thread_ts.return_value = "2099-01-01T00:00:00Z"

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_pr_description")
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.request_copilot_review")
    @patch("autopilot_loop.orchestrator.get_latest_copilot_review_thread_ts")
    @patch("autopilot_loop.orchestrator.is_copilot_pending_reviewer")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    @patch("autopilot_loop.orchestrator.get_copilot_review")
    @patch("autopilot_loop.orchestrator.find_pr_for_branch")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_one_fix_iteration(
        self, mock_run, mock_find_pr, mock_review, mock_unresolved,
        mock_pending, mock_thread_ts,
        mock_request, mock_sha, mock_verify,
        mock_reply, mock_resolve, mock_timeout,
        mock_pr_desc,
        config,
    ):
        """Full loop with one fix iteration: comments -> fix -> resolve -> re-review -> clean."""
        mock_run.return_value = _mock_agent_result()
        mock_find_pr.return_value = 42
        mock_sha.return_value = "sha1"
        mock_verify.return_value = True
        mock_pr_desc.return_value = {"title": "test", "body": "test body"}

        # First pass: 1 unresolved comment. After fix+resolve: 0 unresolved.
        # WAIT_REVIEW now also polls get_unresolved, so extra entries needed.
        mock_review.return_value = {"id": 100, "body": "review", "state": "APPROVED"}
        _comment = {"id": 1, "thread_id": "T1", "path": "a.rb", "line": 10, "body": "fix this"}
        mock_unresolved.side_effect = [
            [_comment],  # WAIT_REVIEW 1st cycle: found comments
            [_comment],  # PARSE_REVIEW
            [_comment],  # _do_fix
            [_comment],  # RESOLVE_COMMENTS
            [],          # WAIT_REVIEW 2nd cycle: clean (then pending check fires)
            [],          # PARSE_REVIEW 2nd: confirms clean
        ]
        mock_pending.return_value = False
        mock_thread_ts.return_value = "2099-01-01T00:00:00Z"

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
        mock_review.return_value = {"id": 100, "body": "LGTM", "state": "APPROVED"}
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

        assert result == "UPDATE_DESCRIPTION"
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
        assert result == "UPDATE_DESCRIPTION"
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


def _create_ci_task(task_id="ci1", check_names=None):
    import json as _json
    if check_names is None:
        check_names = ["build-and-test-7"]
    persistence.create_task(task_id, "(fix-ci)", max_iterations=3, model="test-model")
    persistence.update_task(
        task_id,
        branch="autopilot/%s" % task_id,
        pr_number=42,
        state="FETCH_ANNOTATIONS",
        task_mode="ci",
        ci_check_names=_json.dumps(check_names),
    )
    return task_id


class TestCIOrchestratorFetchAnnotations:
    @patch("autopilot_loop.orchestrator.get_check_annotations")
    @patch("autopilot_loop.orchestrator.get_failed_checks")
    def test_annotations_found_transitions_to_fix(self, mock_failed, mock_ann, config):
        mock_failed.return_value = [
            {"name": "build-and-test-7", "job_id": 100, "run_id": 1, "link": ""},
        ]
        mock_ann.return_value = [
            {"path": "test/a.rb", "start_line": 40, "end_line": 40,
             "title": "Test failure", "message": "assert failed"},
        ]
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fetch_annotations() == "FIX_CI"

    @patch("autopilot_loop.orchestrator.get_check_annotations")
    @patch("autopilot_loop.orchestrator.get_failed_checks")
    def test_no_annotations_completes(self, mock_failed, mock_ann, config):
        mock_failed.return_value = [
            {"name": "build-and-test-7", "job_id": 100, "run_id": 1, "link": ""},
        ]
        mock_ann.return_value = []
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fetch_annotations() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_check_states")
    @patch("autopilot_loop.orchestrator.get_failed_checks")
    def test_checks_now_passing_completes(self, mock_failed, mock_states, config):
        mock_failed.return_value = []  # No longer failing
        mock_states.return_value = {"build-and-test-7": "SUCCESS"}
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fetch_annotations() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_check_annotations")
    @patch("autopilot_loop.orchestrator.get_failed_checks")
    def test_max_iterations_completes(self, mock_failed, mock_ann, config):
        mock_failed.return_value = [
            {"name": "build-and-test-7", "job_id": 100, "run_id": 1, "link": ""},
        ]
        mock_ann.return_value = [
            {"path": "test/a.rb", "start_line": 40, "end_line": 40,
             "title": "Test failure", "message": "still failing"},
        ]
        task_id = _create_ci_task()
        persistence.update_task(task_id, iteration=3)  # At max
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_fetch_annotations() == "COMPLETE"


class TestCIOrchestratorFixCI:
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_fix_ci_success(self, mock_run, mock_sha, config):
        mock_run.return_value = _mock_agent_result()
        mock_sha.return_value = "abc123"
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_annotations = [
            {"path": "test/a.rb", "start_line": 40, "title": "fail", "message": "msg"},
        ]
        assert orch._do_fix_ci() == "VERIFY_PUSH"


class TestCIOrchestratorWaitCI:
    @patch("autopilot_loop.orchestrator.get_check_states")
    def test_all_checks_pass(self, mock_states, config):
        mock_states.return_value = {"build-and-test-7": "SUCCESS"}
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_ci() == "COMPLETE"

    @patch("autopilot_loop.orchestrator.get_check_states")
    def test_checks_still_failing(self, mock_states, config):
        mock_states.return_value = {"build-and-test-7": "FAILURE"}
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_ci() == "FETCH_ANNOTATIONS"

    @patch("autopilot_loop.orchestrator.time.sleep")
    @patch("autopilot_loop.orchestrator.get_check_states")
    def test_timeout_completes(self, mock_states, mock_sleep, config):
        mock_states.return_value = {"build-and-test-7": "PENDING"}
        config["ci_poll_timeout_seconds"] = 0  # Immediate timeout
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_wait_ci() == "COMPLETE"


class TestCIOrchestratorVerifyPush:
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    def test_goes_to_wait_ci(self, mock_verify, mock_sha, config):
        """CIOrchestrator VERIFY_PUSH transitions to WAIT_CI (not RESOLVE_COMMENTS)."""
        mock_verify.return_value = True
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._pre_fix_sha = "old_sha"
        assert orch._do_verify_push() == "WAIT_CI"

    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    def test_no_commits_retries_fix_ci(self, mock_verify, mock_sha, config):
        """CIOrchestrator VERIFY_PUSH retries FIX_CI (not FIX)."""
        mock_verify.return_value = False
        mock_sha.return_value = "same"
        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._pre_fix_sha = "same"
        assert orch._do_verify_push() == "FIX_CI"


class TestCIOrchestratorFullLoop:
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.get_check_states")
    @patch("autopilot_loop.orchestrator.verify_new_commits")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.run_agent")
    @patch("autopilot_loop.orchestrator.get_check_annotations")
    @patch("autopilot_loop.orchestrator.get_failed_checks")
    def test_fix_then_pass(
        self, mock_failed, mock_ann, mock_run, mock_sha, mock_verify,
        mock_states, mock_timeout, config,
    ):
        """Full CI loop: fetch annotations → fix → verify push → wait CI → COMPLETE."""
        mock_failed.return_value = [
            {"name": "build-and-test-7", "job_id": 100, "run_id": 1, "link": ""},
        ]
        mock_ann.return_value = [
            {"path": "test/a.rb", "start_line": 40, "end_line": 40,
             "title": "Test failure", "message": "assert failed"},
        ]
        mock_run.return_value = _mock_agent_result()
        mock_sha.return_value = "sha1"
        mock_verify.return_value = True
        mock_states.return_value = {"build-and-test-7": "SUCCESS"}

        task_id = _create_ci_task()
        orch = CIOrchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "COMPLETE"


class TestTerminalStates:
    def test_stopped_is_terminal(self):
        assert "STOPPED" in TERMINAL_STATES

    def test_failed_is_terminal(self):
        assert "FAILED" in TERMINAL_STATES

    def test_complete_is_terminal(self):
        assert "COMPLETE" in TERMINAL_STATES

    def test_stopped_task_does_not_run(self, config):
        """A task in STOPPED state should be treated as terminal."""
        task_id = _create_test_task()
        persistence.update_task(task_id, state="STOPPED")
        orch = Orchestrator(task_id, config)
        result = orch.run()
        assert result["state"] == "STOPPED"


class TestExistingBranchImplement:
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_existing_branch_uses_correct_prompt(self, mock_run, config):
        """When existing_branch=1, implement uses the existing-branch prompt."""
        mock_run.return_value = _mock_agent_result()
        task_id = _create_test_task()
        persistence.update_task(task_id, existing_branch=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        assert orch._do_implement() == "VERIFY_PR"

        # Verify the prompt contains the existing-branch instruction
        call_args = mock_run.call_args
        prompt = call_args[1]["prompt"] if "prompt" in call_args[1] else call_args[0][0]
        assert "Do NOT create a new branch" in prompt

    @patch("autopilot_loop.orchestrator.run_agent")
    def test_new_branch_uses_standard_prompt(self, mock_run, config):
        """When existing_branch is not set, implement uses the standard prompt."""
        mock_run.return_value = _mock_agent_result()
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        assert orch._do_implement() == "VERIFY_PR"

        call_args = mock_run.call_args
        prompt = call_args[1]["prompt"] if "prompt" in call_args[1] else call_args[0][0]
        assert "Create a new git branch" in prompt or "git checkout -b" in prompt


class TestIdleTimeoutEnabled:
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_idle_timeout_called_when_enabled(self, mock_timeout, config, monkeypatch):
        """Default config (enabled=True) should call set_idle_timeout in a Codespace."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        monkeypatch.setattr("autopilot_loop.orchestrator.get_idle_timeout", lambda: 30)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        orch._do_init()
        mock_timeout.assert_called_once()

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_idle_timeout_skipped_when_disabled(self, mock_timeout, config, monkeypatch):
        """idle_timeout_enabled=False should skip set_idle_timeout even in a Codespace."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        config["idle_timeout_enabled"] = False
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        orch._do_init()
        mock_timeout.assert_not_called()

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_idle_timeout_skipped_outside_codespace(self, mock_timeout, config, monkeypatch):
        """Outside a Codespace, idle timeout is silently skipped."""
        monkeypatch.delenv("CODESPACE_NAME", raising=False)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        orch._do_init()
        mock_timeout.assert_not_called()

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.get_idle_timeout", return_value=30)
    def test_original_timeout_saved_on_init(self, mock_get, mock_set, config, monkeypatch):
        """Original idle timeout is saved to the task record during init."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        orch._do_init()
        task = persistence.get_task(task_id)
        assert task["original_idle_timeout"] == 30

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    @patch("autopilot_loop.orchestrator.get_idle_timeout", return_value=None)
    def test_original_timeout_not_saved_when_none(self, mock_get, mock_set, config, monkeypatch):
        """When get_idle_timeout returns None, original_idle_timeout is not set."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        orch._do_init()
        task = persistence.get_task(task_id)
        assert task["original_idle_timeout"] is None


class TestFixContextCarryForward:
    def test_no_previous_summary_returns_empty(self, config):
        """First iteration has no previous context."""
        task_id = _create_test_task()
        persistence.update_task(task_id, iteration=1)
        orch = Orchestrator(task_id, config)
        assert orch._load_previous_fix_summary(1) == ""

    def test_loads_previous_summary(self, config):
        """Loads and formats the previous iteration's fix summary."""
        import json as _json

        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)

        # Write a fake fix summary for iteration 1
        summary = [
            {"comment_id": 42, "status": "fixed", "message": "Extracted billing concern"},
            {"comment_id": 43, "status": "skipped", "message": "Current pattern is idiomatic"},
        ]
        summary_path = os.path.join(orch.sessions_dir, "fix-summary-1.json")
        with open(summary_path, "w") as f:
            _json.dump(summary, f)

        result = orch._load_previous_fix_summary(2)
        assert "Comment 42: FIXED" in result
        assert "Extracted billing concern" in result
        assert "Comment 43: SKIPPED" in result
        assert "Current pattern is idiomatic" in result
        assert "STILL unresolved" in result

    def test_missing_summary_file_returns_empty(self, config):
        """Returns empty string if summary file doesn't exist."""
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        assert orch._load_previous_fix_summary(3) == ""


class TestFixPromptPreviousContext:
    def test_previous_context_included_in_prompt(self):
        """fix_prompt includes previous context when provided."""
        from autopilot_loop.prompts import fix_prompt
        prompt = fix_prompt(
            review_comments_text="some comments",
            previous_context="- Comment 42: FIXED — did a thing",
        )
        assert "Previous Iteration Context" in prompt
        assert "Comment 42: FIXED" in prompt

    def test_no_previous_context(self):
        """fix_prompt omits section when no previous context."""
        from autopilot_loop.prompts import fix_prompt
        prompt = fix_prompt(review_comments_text="some comments")
        assert "Previous Iteration Context" not in prompt


class TestPromptFileProtection:
    """Tests for prompt file protection instruction in prompt builders."""

    def test_implement_prompt_with_file(self):
        from autopilot_loop.prompts import implement_prompt
        prompt = implement_prompt("Do X", "autopilot/abc", prompt_file="task.txt")
        assert "Do NOT" in prompt
        assert "task.txt" in prompt

    def test_implement_prompt_without_file(self):
        from autopilot_loop.prompts import implement_prompt
        prompt = implement_prompt("Do X", "autopilot/abc")
        assert "Do NOT" not in prompt or "Do NOT use generic" in prompt

    def test_plan_and_implement_prompt_with_file(self):
        from autopilot_loop.prompts import plan_and_implement_prompt
        prompt = plan_and_implement_prompt("Do X", "autopilot/abc", prompt_file="plan.md")
        assert "plan.md" in prompt
        assert "must remain unchanged" in prompt

    def test_fix_prompt_with_file(self):
        from autopilot_loop.prompts import fix_prompt
        prompt = fix_prompt("comments", prompt_file="instructions.txt")
        assert "instructions.txt" in prompt
        assert "must remain unchanged" in prompt

    def test_fix_prompt_without_file(self):
        from autopilot_loop.prompts import fix_prompt
        prompt = fix_prompt("comments")
        assert "must remain unchanged" not in prompt

    def test_fix_ci_prompt_with_file(self):
        from autopilot_loop.prompts import fix_ci_prompt
        prompt = fix_ci_prompt("annotations", prompt_file="task.txt")
        assert "task.txt" in prompt

    def test_existing_branch_prompt_with_file(self):
        from autopilot_loop.prompts import implement_on_existing_branch_prompt
        prompt = implement_on_existing_branch_prompt("Do X", "autopilot/abc", prompt_file="task.txt")
        assert "task.txt" in prompt
        assert "must remain unchanged" in prompt


class TestWorkspaceDirs:
    def test_auto_discovers_sibling_repos(self, tmp_path, monkeypatch, config):
        """Discovers sibling git repos under the parent directory."""
        # Create workspace layout: /workspace/repo-a (cwd), /workspace/repo-b (sibling)
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        (repo_a / ".git").mkdir()
        (repo_b / ".git").mkdir()

        monkeypatch.chdir(repo_a)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        dirs = orch._get_workspace_dirs()
        assert str(repo_b) in dirs
        assert str(repo_a) not in dirs

    def test_ignores_non_git_dirs(self, tmp_path, monkeypatch, config):
        """Non-git directories are not included."""
        repo = tmp_path / "repo"
        plain = tmp_path / "plain-dir"
        repo.mkdir()
        plain.mkdir()
        (repo / ".git").mkdir()
        # plain has no .git

        monkeypatch.chdir(repo)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        dirs = orch._get_workspace_dirs()
        assert str(plain) not in dirs

    def test_config_override_replaces_auto(self, tmp_path, monkeypatch, config):
        """add_dirs config overrides auto-discovery."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        monkeypatch.chdir(repo)
        config["add_dirs"] = ["/custom/path"]
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        dirs = orch._get_workspace_dirs()
        assert dirs == ["/custom/path"]

    def test_config_empty_list_disables(self, tmp_path, monkeypatch, config):
        """add_dirs: [] disables auto-discovery."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        (repo_a / ".git").mkdir()
        (repo_b / ".git").mkdir()

        monkeypatch.chdir(repo_a)
        config["add_dirs"] = []
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        dirs = orch._get_workspace_dirs()
        assert dirs == []

    def test_extra_flags_built_from_dirs(self, tmp_path, monkeypatch, config):
        """_get_extra_flags() produces --add-dir pairs."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_a.mkdir()
        repo_b.mkdir()
        (repo_a / ".git").mkdir()
        (repo_b / ".git").mkdir()

        monkeypatch.chdir(repo_a)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        flags = orch._get_extra_flags()
        assert "--add-dir" in flags
        assert str(repo_b) in flags

    def test_no_siblings_returns_none(self, tmp_path, monkeypatch, config):
        """No sibling repos returns None (no extra flags)."""
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / ".git").mkdir()

        monkeypatch.chdir(repo)
        task_id = _create_test_task()
        orch = Orchestrator(task_id, config)
        flags = orch._get_extra_flags()
        assert flags is None


class TestIdleTimeoutRestore:
    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_restore_called_when_original_saved(self, mock_set, config, monkeypatch):
        """Idle timeout is restored at terminal state when original was saved."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        task_id = _create_test_task()
        persistence.update_task(task_id, original_idle_timeout=30)

        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._restore_idle_timeout()

        mock_set.assert_called_once_with(30)

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_restore_not_called_when_no_original(self, mock_set, config, monkeypatch):
        """Idle timeout is not restored when no original was saved."""
        monkeypatch.setenv("CODESPACE_NAME", "test-codespace")
        task_id = _create_test_task()

        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._restore_idle_timeout()

        mock_set.assert_not_called()

    @patch("autopilot_loop.orchestrator.set_idle_timeout")
    def test_restore_not_called_outside_codespace(self, mock_set, config, monkeypatch):
        """Idle timeout is not restored outside a codespace."""
        monkeypatch.delenv("CODESPACE_NAME", raising=False)
        task_id = _create_test_task()
        persistence.update_task(task_id, original_idle_timeout=30)

        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._restore_idle_timeout()

        mock_set.assert_not_called()


class TestResolveCommentsNewStatuses:
    """Tests for dismissed and uncertain statuses in _do_resolve_comments."""

    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_uncertain_does_not_resolve_thread(
        self, mock_unresolved, mock_sha, mock_reply, mock_resolve, config, tmp_path,
    ):
        """Uncertain status replies but does NOT resolve the thread."""
        import json as _json

        mock_sha.return_value = "abc1234"
        comments = [
            {"id": 30, "thread_id": "T30", "path": "c.rb", "line": 12, "body": "maybe wrong"},
        ]
        mock_unresolved.return_value = comments

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_comments = comments

        summary = [
            {"comment_id": 30, "status": "uncertain", "message": "Not sure about this",
             "evidence": "Checked tests but inconclusive"},
        ]
        summary_path = os.path.join(os.getcwd(), ".autopilot-fix-summary.json")
        with open(summary_path, "w") as f:
            _json.dump(summary, f)

        try:
            result = orch._do_resolve_comments()
        finally:
            if os.path.exists(summary_path):
                os.remove(summary_path)

        assert result == "UPDATE_DESCRIPTION"
        # Reply was posted
        assert mock_reply.call_count == 1
        reply_body = mock_reply.call_args[0][2]
        assert "Needs human review" in reply_body
        assert "Checked tests but inconclusive" in reply_body
        # Thread was NOT resolved
        assert mock_resolve.call_count == 0

    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_dismissed_resolves_with_evidence(
        self, mock_unresolved, mock_sha, mock_reply, mock_resolve, config, tmp_path,
    ):
        """Dismissed status replies with evidence and resolves the thread."""
        import json as _json

        mock_sha.return_value = "abc1234"
        comments = [
            {"id": 40, "thread_id": "T40", "path": "d.rb", "line": 20, "body": "make optional"},
        ]
        mock_unresolved.return_value = comments

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_comments = comments

        summary = [
            {"comment_id": 40, "status": "dismissed", "message": "Field is required",
             "evidence": "API contract at src/schema.py:42 requires this field"},
        ]
        summary_path = os.path.join(os.getcwd(), ".autopilot-fix-summary.json")
        with open(summary_path, "w") as f:
            _json.dump(summary, f)

        try:
            result = orch._do_resolve_comments()
        finally:
            if os.path.exists(summary_path):
                os.remove(summary_path)

        assert result == "UPDATE_DESCRIPTION"
        assert mock_reply.call_count == 1
        reply_body = mock_reply.call_args[0][2]
        assert "Dismissed" in reply_body
        assert "API contract at src/schema.py:42" in reply_body
        # Thread WAS resolved
        assert mock_resolve.call_count == 1

    @patch("autopilot_loop.orchestrator.resolve_review_thread")
    @patch("autopilot_loop.orchestrator.reply_to_comment")
    @patch("autopilot_loop.orchestrator.get_head_sha")
    @patch("autopilot_loop.orchestrator.get_unresolved_review_comments")
    def test_mixed_statuses(
        self, mock_unresolved, mock_sha, mock_reply, mock_resolve, config, tmp_path,
    ):
        """All four statuses handled correctly in one pass."""
        import json as _json

        mock_sha.return_value = "abc1234"
        comments = [
            {"id": 10, "thread_id": "T10", "path": "a.rb", "line": 5, "body": "fix this"},
            {"id": 20, "thread_id": "T20", "path": "b.rb", "line": 8, "body": "style nit"},
            {"id": 30, "thread_id": "T30", "path": "c.rb", "line": 12, "body": "maybe wrong"},
            {"id": 40, "thread_id": "T40", "path": "d.rb", "line": 20, "body": "bad suggestion"},
        ]

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1")
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        orch._current_comments = comments

        summary = [
            {"comment_id": 10, "status": "fixed", "message": "Added null check"},
            {"comment_id": 20, "status": "skipped", "message": "Style is intentional"},
            {"comment_id": 30, "status": "uncertain", "message": "Not sure",
             "evidence": "Checked tests"},
            {"comment_id": 40, "status": "dismissed", "message": "Wrong suggestion",
             "evidence": "API requires this"},
        ]
        summary_path = os.path.join(os.getcwd(), ".autopilot-fix-summary.json")
        with open(summary_path, "w") as f:
            _json.dump(summary, f)

        try:
            result = orch._do_resolve_comments()
        finally:
            if os.path.exists(summary_path):
                os.remove(summary_path)

        assert result == "UPDATE_DESCRIPTION"
        # 4 replies (one per comment)
        assert mock_reply.call_count == 4
        # 3 resolved (fixed, skipped, dismissed) — uncertain is NOT resolved
        assert mock_resolve.call_count == 3


class TestUpdateDescription:
    @patch("autopilot_loop.orchestrator.get_pr_description")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_update_description_success(self, mock_run, mock_pr_desc, config):
        """UPDATE_DESCRIPTION runs agent and transitions to REQUEST_REVIEW."""
        mock_run.return_value = _mock_agent_result()
        mock_pr_desc.return_value = {"title": "test", "body": "old body"}

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1", iteration=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        result = orch._do_update_description()
        assert result == "REQUEST_REVIEW"
        assert mock_run.called

    @patch("autopilot_loop.orchestrator.get_pr_description")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_update_description_failure_non_fatal(self, mock_run, mock_pr_desc, config):
        """UPDATE_DESCRIPTION agent failure is non-fatal, still transitions to REQUEST_REVIEW."""
        mock_run.return_value = _mock_agent_result(exit_code=1)
        mock_pr_desc.return_value = {"title": "test", "body": "old body"}

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1", iteration=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        result = orch._do_update_description()
        assert result == "REQUEST_REVIEW"

    @patch("autopilot_loop.orchestrator.get_pr_description")
    @patch("autopilot_loop.orchestrator.run_agent")
    def test_update_description_pr_fetch_error(self, mock_run, mock_pr_desc, config):
        """UPDATE_DESCRIPTION handles PR description fetch error gracefully."""
        mock_run.return_value = _mock_agent_result()
        mock_pr_desc.side_effect = Exception("API error")

        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1", iteration=1)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        result = orch._do_update_description()
        assert result == "REQUEST_REVIEW"


class TestBouncingCommentDetection:
    def _setup_orchestrator(self, config, iteration=3):
        task_id = _create_test_task()
        persistence.update_task(task_id, pr_number=42, branch="autopilot/test1", iteration=iteration)
        orch = Orchestrator(task_id, config)
        orch.task = persistence.get_task(task_id)
        return orch

    def test_no_bounce_on_first_iterations(self, config):
        """No bouncing detected on iterations 1-2 (not enough history)."""
        orch = self._setup_orchestrator(config, iteration=2)
        comments = [{"id": 1, "path": "a.rb", "line": 5, "body": "fix this"}]
        result = orch._detect_bouncing_comments(comments, 2)
        assert result == ""

    def test_bounce_detected_after_two_fixes(self, config, tmp_path):
        """Comment bouncing back after being fixed twice is detected."""
        import json as _json

        orch = self._setup_orchestrator(config, iteration=3)

        # Create fix summaries for iterations 1 and 2 — both marked "fixed"
        for prev_iter in [1, 2]:
            summary_path = os.path.join(orch.sessions_dir, "fix-summary-%d.json" % prev_iter)
            _json.dump(
                [{"comment_id": 1, "status": "fixed", "message": "fixed it"}],
                open(summary_path, "w"),
            )
            review_path = os.path.join(orch.sessions_dir, "review-%d.json" % prev_iter)
            _json.dump(
                {"body": "", "comments": [
                    {"id": 1, "path": "a.rb", "line": 5, "body": "make this field optional"},
                ]},
                open(review_path, "w"),
            )

        # Current iteration 3: same comment reappears
        comments = [{"id": 99, "path": "a.rb", "line": 5, "body": "make this field optional please"}]
        result = orch._detect_bouncing_comments(comments, 3)
        assert "CIRCULAR REVIEW LOOP" in result
        assert "a.rb" in result
        assert "DO NOT fix" in result

    def test_no_bounce_for_different_files(self, config, tmp_path):
        """Comments on different files do not trigger bounce detection."""
        import json as _json

        orch = self._setup_orchestrator(config, iteration=3)

        for prev_iter in [1, 2]:
            summary_path = os.path.join(orch.sessions_dir, "fix-summary-%d.json" % prev_iter)
            _json.dump(
                [{"comment_id": 1, "status": "fixed", "message": "fixed it"}],
                open(summary_path, "w"),
            )
            review_path = os.path.join(orch.sessions_dir, "review-%d.json" % prev_iter)
            _json.dump(
                {"body": "", "comments": [
                    {"id": 1, "path": "a.rb", "line": 5, "body": "fix this thing"},
                ]},
                open(review_path, "w"),
            )

        # Current comment is on a DIFFERENT file
        comments = [{"id": 99, "path": "b.rb", "line": 5, "body": "fix this thing"}]
        result = orch._detect_bouncing_comments(comments, 3)
        assert result == ""

    def test_no_bounce_for_skipped_comments(self, config, tmp_path):
        """Skipped comments (not fixed) do not count toward bounce detection."""
        import json as _json

        orch = self._setup_orchestrator(config, iteration=3)

        for prev_iter in [1, 2]:
            summary_path = os.path.join(orch.sessions_dir, "fix-summary-%d.json" % prev_iter)
            _json.dump(
                [{"comment_id": 1, "status": "skipped", "message": "not worth it"}],
                open(summary_path, "w"),
            )
            review_path = os.path.join(orch.sessions_dir, "review-%d.json" % prev_iter)
            _json.dump(
                {"body": "", "comments": [
                    {"id": 1, "path": "a.rb", "line": 5, "body": "make optional"},
                ]},
                open(review_path, "w"),
            )

        comments = [{"id": 99, "path": "a.rb", "line": 5, "body": "make optional"}]
        result = orch._detect_bouncing_comments(comments, 3)
        assert result == ""


class TestFixPromptWithBouncing:
    def test_bouncing_section_included(self):
        """fix_prompt includes bouncing comments warning when provided."""
        from autopilot_loop.prompts import fix_prompt
        result = fix_prompt(
            review_comments_text="some review",
            bouncing_comments="Comment on a.rb bounced 3 times",
        )
        assert "Circular Review Loop Detected" in result
        assert "Comment on a.rb bounced 3 times" in result

    def test_no_bouncing_section_when_empty(self):
        """fix_prompt omits bouncing section when empty."""
        from autopilot_loop.prompts import fix_prompt
        result = fix_prompt(review_comments_text="some review")
        assert "Circular Review Loop" not in result

    def test_3_tier_model_present(self):
        """fix_prompt includes the 3-tier decision model."""
        from autopilot_loop.prompts import fix_prompt
        result = fix_prompt(review_comments_text="some review")
        assert "3-tier decision model" in result
        assert "AGREE & FIX" in result
        assert "DISAGREE with evidence" in result
        assert "UNCERTAIN" in result
        assert "dismissed" in result
        assert "uncertain" in result


class TestUpdateDescriptionPrompt:
    def test_includes_all_sections(self):
        """update_description_prompt includes all required sections."""
        from autopilot_loop.prompts import update_description_prompt
        result = update_description_prompt(
            task_description="Implement feature X",
            current_pr_body="Old PR body",
            diff_stat="file1.py | 10 +++",
        )
        assert "Implement feature X" in result
        assert "Old PR body" in result
        assert "file1.py | 10 +++" in result
        assert "gh pr edit" in result
        assert "Do NOT make any code changes" in result

    def test_handles_empty_body(self):
        """update_description_prompt handles empty PR body."""
        from autopilot_loop.prompts import update_description_prompt
        result = update_description_prompt(
            task_description="task",
            current_pr_body="",
            diff_stat="",
        )
        assert "(empty)" in result
