"""Core state machine orchestrator.

Manages the full lifecycle: INIT → IMPLEMENT → VERIFY_PR → REQUEST_REVIEW →
WAIT_REVIEW → PARSE_REVIEW → FIX → VERIFY_PUSH → ... → COMPLETE.
"""

import json
import logging
import os
import time

from autopilot_loop.agent import run_agent
from autopilot_loop.codespace import set_idle_timeout
from autopilot_loop.github_api import (
    find_pr_for_branch,
    get_copilot_inline_comments,
    get_copilot_review,
    get_head_sha,
    is_copilot_review_complete,
    request_copilot_review,
    verify_new_commits,
)
from autopilot_loop.persistence import (
    get_sessions_dir,
    get_task,
    save_agent_run,
    save_review,
    update_task,
)
from autopilot_loop.prompts import (
    fix_prompt,
    format_review_for_prompt,
    implement_prompt,
    plan_and_implement_prompt,
)

logger = logging.getLogger(__name__)

__all__ = ["Orchestrator"]

# Valid state transitions
STATES = [
    "INIT",
    "PLAN_AND_IMPLEMENT",
    "IMPLEMENT",
    "VERIFY_PR",
    "REQUEST_REVIEW",
    "WAIT_REVIEW",
    "PARSE_REVIEW",
    "FIX",
    "VERIFY_PUSH",
    "COMPLETE",
    "FAILED",
]


class Orchestrator:
    """State machine orchestrator for the autopilot loop."""

    def __init__(self, task_id, config):
        self.task_id = task_id
        self.config = config
        self.task = get_task(task_id)
        self.sessions_dir = get_sessions_dir(task_id)
        self._retry_counts = {}  # phase -> retry count

    def run(self):
        """Run the state machine until COMPLETE or FAILED."""
        state = self.task["state"]
        logger.info("[%s] Starting orchestrator from state: %s", self.task_id, state)

        while state not in ("COMPLETE", "FAILED"):
            logger.info("[%s] %s", self.task_id, state)
            try:
                state = self._transition(state)
            except Exception:
                logger.exception("[%s] Unhandled error in state %s", self.task_id, state)
                state = "FAILED"

            update_task(self.task_id, state=state)
            self.task = get_task(self.task_id)

        logger.info("[%s] %s", self.task_id, state)
        return {"state": state, "task": self.task}

    def _transition(self, state):
        """Execute the current state and return the next state."""
        handler = {
            "INIT": self._do_init,
            "PLAN_AND_IMPLEMENT": self._do_plan_and_implement,
            "IMPLEMENT": self._do_implement,
            "VERIFY_PR": self._do_verify_pr,
            "REQUEST_REVIEW": self._do_request_review,
            "WAIT_REVIEW": self._do_wait_review,
            "PARSE_REVIEW": self._do_parse_review,
            "FIX": self._do_fix,
            "VERIFY_PUSH": self._do_verify_push,
        }.get(state)

        if handler is None:
            logger.error("[%s] Unknown state: %s", self.task_id, state)
            return "FAILED"

        return handler()

    def _do_init(self):
        """Validate config, create session dir, set codespace idle timeout."""
        logger.info("[%s] INIT → Validated config, created session dir", self.task_id)

        # Set codespace idle timeout (non-fatal)
        try:
            set_idle_timeout(self.config.get("idle_timeout_minutes", 120))
            logger.info("[%s] ✓ Codespace idle timeout set to %d minutes",
                        self.task_id, self.config.get("idle_timeout_minutes", 120))
        except Exception as e:
            logger.warning("[%s] Could not set codespace idle timeout: %s", self.task_id, e)

        # Determine next state
        if self.task.get("plan_mode"):
            return "PLAN_AND_IMPLEMENT"
        return "IMPLEMENT"

    def _run_agent_with_retry(self, phase, prompt, session_name):
        """Run an agent with retry policy.

        Returns:
            AgentResult on success, or None on exhausted retries.
        """
        max_retries = self.config.get("max_retries_per_phase", 1)
        retry_count = self._retry_counts.get(phase, 0)

        session_file_name = session_name
        if retry_count > 0:
            session_file_name = "%s-retry%d" % (session_name, retry_count)

        # Create phase-specific session dir
        phase_session_dir = os.path.join(self.sessions_dir, session_file_name)
        os.makedirs(phase_session_dir, exist_ok=True)

        started_at = time.time()
        result = run_agent(
            prompt=prompt,
            session_dir=phase_session_dir,
            model=self.config.get("model", "claude-opus-4.6"),
            timeout=self.config.get("agent_timeout_seconds", 1800),
        )
        ended_at = time.time()

        # Copy session file to top-level sessions dir with a readable name
        src = result.session_file
        dst = os.path.join(self.sessions_dir, "%s.md" % session_file_name)
        if os.path.isfile(src):
            import shutil
            shutil.copy2(src, dst)
            result.session_file = dst

        save_agent_run(
            task_id=self.task_id,
            phase=phase,
            started_at=started_at,
            ended_at=ended_at,
            exit_code=result.exit_code,
            session_file=result.session_file,
            retry_count=retry_count,
        )

        if result.success:
            self._retry_counts[phase] = 0
            return result

        # Retry logic
        if retry_count < max_retries:
            logger.warning(
                "[%s] Agent failed (exit %d) in %s, retrying (%d/%d)",
                self.task_id, result.exit_code, phase, retry_count + 1, max_retries,
            )
            self._retry_counts[phase] = retry_count + 1
            return self._run_agent_with_retry(phase, prompt, session_name)

        logger.error(
            "[%s] Agent failed (exit %d) in %s after %d retries",
            self.task_id, result.exit_code, phase, max_retries,
        )
        return None

    def _do_implement(self):
        """Run copilot agent with implement prompt."""
        branch = self.task["branch"]
        prompt = implement_prompt(
            task_description=self.task["prompt"],
            branch_name=branch,
            custom_instructions=self.config.get("custom_instructions", ""),
        )

        result = self._run_agent_with_retry("IMPLEMENT", prompt, "implement")
        if result is None:
            return "FAILED"

        logger.info("[%s] ✓ Agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
        return "VERIFY_PR"

    def _do_plan_and_implement(self):
        """Run copilot agent with plan+implement prompt."""
        branch = self.task["branch"]
        prompt = plan_and_implement_prompt(
            task_description=self.task["prompt"],
            branch_name=branch,
            custom_instructions=self.config.get("custom_instructions", ""),
        )

        result = self._run_agent_with_retry("PLAN_AND_IMPLEMENT", prompt, "plan-and-implement")
        if result is None:
            return "FAILED"

        logger.info("[%s] ✓ Agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
        return "VERIFY_PR"

    def _do_verify_pr(self):
        """Verify that the agent created a PR."""
        branch = self.task["branch"]
        pr_number = find_pr_for_branch(branch)

        if pr_number:
            update_task(self.task_id, pr_number=pr_number)
            logger.info("[%s] ✓ Found PR #%d", self.task_id, pr_number)
            return "REQUEST_REVIEW"

        # PR not found — retry implement if we haven't already
        retry_key = "VERIFY_PR_IMPLEMENT_RETRY"
        if self._retry_counts.get(retry_key, 0) > 0:
            logger.error("[%s] No PR found for branch %s after retry", self.task_id, branch)
            return "FAILED"

        logger.warning("[%s] No PR found for branch %s, retrying IMPLEMENT with explicit prompt", self.task_id, branch)
        self._retry_counts[retry_key] = 1

        # Retry with a more explicit prompt
        explicit_prompt = (
            "IMPORTANT: You MUST create a git branch named `%s`, commit your changes, "
            "create a draft PR using `gh pr create --draft`, and push. "
            "The previous attempt did not create a PR.\n\n%s"
            % (branch, self.task["prompt"])
        )

        phase = "IMPLEMENT" if not self.task.get("plan_mode") else "PLAN_AND_IMPLEMENT"
        result = self._run_agent_with_retry(phase, explicit_prompt, "implement-retry")
        if result is None:
            return "FAILED"

        # Check again
        pr_number = find_pr_for_branch(branch)
        if pr_number:
            update_task(self.task_id, pr_number=pr_number)
            logger.info("[%s] ✓ Found PR #%d on retry", self.task_id, pr_number)
            return "REQUEST_REVIEW"

        logger.error("[%s] Still no PR after retry", self.task_id)
        return "FAILED"

    def _do_request_review(self):
        """Request Copilot review on the PR."""
        pr_number = self.task["pr_number"]
        try:
            request_copilot_review(pr_number)
            logger.info("[%s] ✓ Requested Copilot review on PR #%d", self.task_id, pr_number)
        except Exception as e:
            logger.error("[%s] Failed to request review: %s", self.task_id, e)
            return "FAILED"
        return "WAIT_REVIEW"

    def _do_wait_review(self):
        """Poll for Copilot review completion."""
        pr_number = self.task["pr_number"]
        poll_interval = self.config.get("review_poll_interval_seconds", 60)
        timeout = self.config.get("review_timeout_seconds", 3600)
        start_time = time.time()

        logger.info("[%s] Polling every %ds for Copilot review (timeout: %ds)...",
                    self.task_id, poll_interval, timeout)

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.warning(
                    "[%s] Copilot review timed out after %ds — PR is ready for manual review",
                    self.task_id, timeout,
                )
                return "COMPLETE"

            if is_copilot_review_complete(pr_number):
                logger.info("[%s] ✓ Copilot review received", self.task_id)
                return "PARSE_REVIEW"

            logger.debug("[%s] No review yet, waiting %ds... (%.0f/%ds elapsed)",
                         self.task_id, poll_interval, elapsed, timeout)
            time.sleep(poll_interval)

    def _do_parse_review(self):
        """Fetch and parse the Copilot review."""
        pr_number = self.task["pr_number"]
        iteration = self.task["iteration"] + 1
        max_iterations = self.task["max_iterations"]

        review = get_copilot_review(pr_number)
        review_body = review.get("body", "") if review else ""
        inline_comments = get_copilot_inline_comments(pr_number)

        # Save review data
        review_file = os.path.join(self.sessions_dir, "review-%d.json" % iteration)
        with open(review_file, "w") as f:
            json.dump({"body": review_body, "comments": inline_comments}, f, indent=2)

        save_review(self.task_id, iteration, review_body, inline_comments)
        update_task(self.task_id, iteration=iteration)

        if not inline_comments:
            logger.info("[%s] ✓ 0 inline comments. Clean!", self.task_id)
            return "COMPLETE"

        logger.info("[%s] %d inline comments found:", self.task_id, len(inline_comments))
        for i, c in enumerate(inline_comments, 1):
            logger.info("[%s]   %d. %s:%s — %s", self.task_id, i,
                        c.get("path", "?"), c.get("original_line", "?"),
                        c.get("body", "")[:80])

        if iteration >= max_iterations:
            logger.warning(
                "[%s] Reached max iterations (%d/%d) with %d remaining comments",
                self.task_id, iteration, max_iterations, len(inline_comments),
            )
            return "COMPLETE"

        return "FIX"

    def _do_fix(self):
        """Run copilot agent to address review comments."""
        pr_number = self.task["pr_number"]
        iteration = self.task["iteration"]

        # Get current review data
        review = get_copilot_review(pr_number)
        review_body = review.get("body", "") if review else ""
        inline_comments = get_copilot_inline_comments(pr_number)

        # Format for prompt
        review_text = format_review_for_prompt(review_body, inline_comments)
        prompt = fix_prompt(
            review_comments_text=review_text,
            custom_instructions=self.config.get("custom_instructions", ""),
        )

        # Record head SHA before fix
        self._pre_fix_sha = get_head_sha(self.task["branch"])

        result = self._run_agent_with_retry("FIX", prompt, "fix-%d" % iteration)
        if result is None:
            return "FAILED"

        logger.info("[%s] ✓ Fix agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
        return "VERIFY_PUSH"

    def _do_verify_push(self):
        """Verify new commits were pushed after fix."""
        branch = self.task["branch"]
        pre_sha = getattr(self, "_pre_fix_sha", None)

        if pre_sha and verify_new_commits(branch, pre_sha):
            logger.info("[%s] ✓ New commits found on %s", self.task_id, branch)
            return "REQUEST_REVIEW"

        # Maybe the agent already pushed and we just need to check
        new_sha = get_head_sha(branch)
        if new_sha and new_sha != pre_sha:
            logger.info("[%s] ✓ New commits found on %s", self.task_id, branch)
            return "REQUEST_REVIEW"

        # No new commits — retry FIX once
        retry_key = "VERIFY_PUSH_FIX_RETRY"
        if self._retry_counts.get(retry_key, 0) > 0:
            logger.error("[%s] No new commits on %s after fix retry", self.task_id, branch)
            return "FAILED"

        logger.warning("[%s] No new commits on %s, retrying FIX", self.task_id, branch)
        self._retry_counts[retry_key] = 1
        return "FIX"
