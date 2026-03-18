"""Core state machine orchestrators.

Orchestrator: INIT → IMPLEMENT → VERIFY_PR → REQUEST_REVIEW →
WAIT_REVIEW → PARSE_REVIEW → FIX → VERIFY_PUSH → RESOLVE_COMMENTS →
REQUEST_REVIEW → ... → COMPLETE.

CIOrchestrator: INIT → FETCH_ANNOTATIONS → FIX_CI → VERIFY_PUSH →
WAIT_CI → FETCH_ANNOTATIONS → ... → COMPLETE.
"""

import json
import logging
import os
import time

from autopilot_loop.agent import run_agent
from autopilot_loop.codespace import set_idle_timeout
from autopilot_loop.github_api import (
    find_pr_for_branch,
    get_check_annotations,
    get_check_states,
    get_copilot_review,
    get_failed_checks,
    get_head_sha,
    get_unresolved_review_comments,
    is_copilot_review_complete,
    reply_to_comment,
    request_copilot_review,
    resolve_review_thread,
    verify_new_commits,
)
from autopilot_loop.persistence import (
    TERMINAL_STATES,
    get_sessions_dir,
    get_task,
    save_agent_run,
    save_review,
    update_task,
)
from autopilot_loop.prompts import (
    fix_ci_prompt,
    fix_prompt,
    format_ci_annotations_for_prompt,
    format_review_for_prompt,
    implement_on_existing_branch_prompt,
    implement_prompt,
    plan_and_implement_prompt,
)

logger = logging.getLogger(__name__)

__all__ = ["Orchestrator", "CIOrchestrator"]

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
    "RESOLVE_COMMENTS",
    "COMPLETE",
    "FAILED",
    "STOPPED",
]

# Re-export from persistence (canonical definition lives there to avoid circular imports)


class BaseOrchestrator:
    """Shared infrastructure for state machine orchestrators."""

    def __init__(self, task_id, config):
        self.task_id = task_id
        self.config = config
        self.task = get_task(task_id)
        self.sessions_dir = get_sessions_dir(task_id)
        self._retry_counts = {}  # phase -> retry count

    def _get_handlers(self):
        """Return a dict mapping state names to handler methods. Subclasses must override."""
        raise NotImplementedError

    def run(self):
        """Run the state machine until a terminal state (COMPLETE, FAILED, or STOPPED)."""
        state = self.task["state"]
        logger.info("[%s] Starting orchestrator from state: %s", self.task_id, state)

        while state not in TERMINAL_STATES:
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
        handler = self._get_handlers().get(state)
        if handler is None:
            logger.error("[%s] Unknown state: %s", self.task_id, state)
            return "FAILED"
        return handler()

    def _do_init(self):
        """Validate config, create session dir, set codespace idle timeout."""
        logger.info("[%s] INIT \u2192 Validated config, created session dir", self.task_id)

        # Set codespace idle timeout (non-fatal, only in Codespaces)
        if not os.environ.get("CODESPACE_NAME"):
            logger.debug("[%s] Not in a Codespace, skipping idle timeout", self.task_id)
        elif not self.config.get("idle_timeout_enabled", True):
            logger.info("[%s] Idle timeout extension disabled by config", self.task_id)
        else:
            try:
                set_idle_timeout(self.config.get("idle_timeout_minutes", 120))
                logger.info("[%s] \u2713 Codespace idle timeout set to %d minutes",
                            self.task_id, self.config.get("idle_timeout_minutes", 120))
            except Exception as e:
                logger.warning("[%s] Could not set codespace idle timeout: %s", self.task_id, e)

        return self._init_next_state()

    def _init_next_state(self):
        """Return the state to transition to after INIT. Subclasses must override."""
        raise NotImplementedError

    def _get_extra_flags(self):
        """Build extra CLI flags for the agent (e.g. --add-dir for sibling repos)."""
        flags = []
        for d in self._get_workspace_dirs():
            flags.extend(["--add-dir", d])
        return flags if flags else None

    def _get_workspace_dirs(self):
        """Discover sibling git repos in the workspace to give the agent read access.

        Auto-detects repos under the parent of CWD (e.g. /workspaces/*/).
        Excludes the current repo. Can be overridden via config add_dirs.
        """
        configured = self.config.get("add_dirs")
        if configured is not None:
            return configured

        cwd = os.getcwd()
        parent = os.path.dirname(cwd)
        dirs = []
        try:
            for name in os.listdir(parent):
                candidate = os.path.join(parent, name)
                if candidate == cwd:
                    continue
                if os.path.isdir(os.path.join(candidate, ".git")):
                    dirs.append(candidate)
        except OSError:
            pass
        if dirs:
            logger.debug("Auto-discovered %d sibling repo(s): %s", len(dirs),
                         ", ".join(os.path.basename(d) for d in dirs))
        return dirs

    def _run_agent_with_retry(self, phase, prompt, session_name):
        """Run an agent with retry policy.

        Returns:
            AgentResult on success, or None on exhausted retries.
        """
        max_retries = self.config.get("max_retries_per_phase", 1)

        for attempt in range(max_retries + 1):
            session_file_name = session_name
            if attempt > 0:
                session_file_name = "%s-retry%d" % (session_name, attempt)

            # Create phase-specific session dir
            phase_session_dir = os.path.join(self.sessions_dir, session_file_name)
            os.makedirs(phase_session_dir, exist_ok=True)

            started_at = time.time()
            result = run_agent(
                prompt=prompt,
                session_dir=phase_session_dir,
                model=self.config.get("model", "claude-opus-4.6"),
                timeout=self.config.get("agent_timeout_seconds", 1800),
                extra_flags=self._get_extra_flags(),
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
                retry_count=attempt,
            )

            if result.success:
                return result

            if attempt < max_retries:
                logger.warning(
                    "[%s] Agent failed (exit %d) in %s, retrying (%d/%d)",
                    self.task_id, result.exit_code, phase, attempt + 1, max_retries,
                )

        logger.error(
            "[%s] Agent failed (exit %d) in %s after %d retries",
            self.task_id, result.exit_code, phase, max_retries,
        )
        return None

    def _do_verify_push(self):
        """Verify new commits were pushed after fix."""
        branch = self.task["branch"]
        pre_sha = getattr(self, "_pre_fix_sha", None)

        if pre_sha and verify_new_commits(branch, pre_sha):
            logger.info("[%s] \u2713 New commits found on %s", self.task_id, branch)
            return self._after_verify_push()

        # Maybe the agent already pushed and we just need to check
        new_sha = get_head_sha(branch)
        if new_sha and new_sha != pre_sha:
            logger.info("[%s] \u2713 New commits found on %s", self.task_id, branch)
            return self._after_verify_push()

        # No new commits \u2014 retry FIX once
        retry_key = "VERIFY_PUSH_FIX_RETRY"
        if self._retry_counts.get(retry_key, 0) > 0:
            logger.error("[%s] No new commits on %s after fix retry", self.task_id, branch)
            return "FAILED"

        logger.warning("[%s] No new commits on %s, retrying fix", self.task_id, branch)
        self._retry_counts[retry_key] = 1
        return self._retry_fix_state()

    def _after_verify_push(self):
        """State to transition to after VERIFY_PUSH succeeds. Subclasses must override."""
        raise NotImplementedError

    def _retry_fix_state(self):
        """State to retry when VERIFY_PUSH finds no new commits. Subclasses must override."""
        raise NotImplementedError


class Orchestrator(BaseOrchestrator):
    """State machine orchestrator for the review-fix autopilot loop."""

    def __init__(self, task_id, config):
        super().__init__(task_id, config)
        self._last_review_ts = None  # timestamp-based review detection

    def _get_handlers(self):
        return {
            "INIT": self._do_init,
            "PLAN_AND_IMPLEMENT": self._do_plan_and_implement,
            "IMPLEMENT": self._do_implement,
            "VERIFY_PR": self._do_verify_pr,
            "REQUEST_REVIEW": self._do_request_review,
            "WAIT_REVIEW": self._do_wait_review,
            "PARSE_REVIEW": self._do_parse_review,
            "FIX": self._do_fix,
            "VERIFY_PUSH": self._do_verify_push,
            "RESOLVE_COMMENTS": self._do_resolve_comments,
        }

    def _init_next_state(self):
        if self.task.get("plan_mode"):
            return "PLAN_AND_IMPLEMENT"
        return "IMPLEMENT"

    def _after_verify_push(self):
        return "RESOLVE_COMMENTS"

    def _retry_fix_state(self):
        return "FIX"

    def _do_implement(self):
        """Run copilot agent with implement prompt."""
        branch = self.task["branch"]

        # Snapshot review timestamp before the agent pushes (same pattern as _do_fix)
        pr_number = self.task.get("pr_number")
        if pr_number:
            current_review = get_copilot_review(pr_number)
            if current_review:
                self._last_review_ts = current_review.get("submitted_at", "")

        # Use existing-branch prompt if the branch already exists remotely
        if self.task.get("existing_branch"):
            prompt = implement_on_existing_branch_prompt(
                task_description=self.task["prompt"],
                branch_name=branch,
                custom_instructions=self.config.get("custom_instructions", ""),
            )
        else:
            prompt = implement_prompt(
                task_description=self.task["prompt"],
                branch_name=branch,
                custom_instructions=self.config.get("custom_instructions", ""),
            )

        result = self._run_agent_with_retry("IMPLEMENT", prompt, "implement")
        if result is None:
            return "FAILED"

        logger.info("[%s] \u2713 Agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
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

        logger.info("[%s] \u2713 Agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
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

        # Review ID snapshot is now taken in _do_fix (before the agent pushes)
        # to avoid the race where Copilot auto-reviews the push before we snapshot.

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

            if is_copilot_review_complete(pr_number, after_ts=self._last_review_ts):
                logger.info("[%s] ✓ Copilot review received", self.task_id)
                return "PARSE_REVIEW"

            logger.debug("[%s] No review yet, waiting %ds... (%.0f/%ds elapsed)",
                         self.task_id, poll_interval, elapsed, timeout)
            time.sleep(poll_interval)

    def _do_parse_review(self):
        """Fetch and parse unresolved Copilot review comments."""
        pr_number = self.task["pr_number"]
        iteration = self.task["iteration"] + 1
        max_iterations = self.task["max_iterations"]

        # Get only UNRESOLVED Copilot comments (already-resolved ones are skipped)
        unresolved = get_unresolved_review_comments(pr_number)

        # Also get the latest review body for logging
        review = get_copilot_review(pr_number)
        review_body = review.get("body", "") if review else ""

        # Save review data
        review_file = os.path.join(self.sessions_dir, "review-%d.json" % iteration)
        with open(review_file, "w") as f:
            json.dump({"body": review_body, "comments": unresolved}, f, indent=2)

        save_review(self.task_id, iteration, review_body, unresolved)
        update_task(self.task_id, iteration=iteration)

        # Store for use by FIX state
        self._current_comments = unresolved

        if not unresolved:
            logger.info("[%s] ✓ 0 unresolved comments. Clean!", self.task_id)
            return "COMPLETE"

        logger.info("[%s] %d unresolved comments found:", self.task_id, len(unresolved))
        for i, c in enumerate(unresolved, 1):
            logger.info("[%s]   %d. %s:%s — %s", self.task_id, i,
                        c.get("path", "?"), c.get("line", "?"),
                        c.get("body", "")[:80])

        if iteration >= max_iterations:
            logger.warning(
                "[%s] Reached max iterations (%d/%d) with %d unresolved comments",
                self.task_id, iteration, max_iterations, len(unresolved),
            )
            return "COMPLETE"

        return "FIX"

    def _load_previous_fix_summary(self, current_iteration):
        """Load the previous iteration's fix summary for context carry-forward.

        Returns a formatted string describing what the previous agent decided,
        or empty string if no previous summary exists.
        """
        prev_iteration = current_iteration - 1
        if prev_iteration < 1:
            return ""

        summary_path = os.path.join(self.sessions_dir, "fix-summary-%d.json" % prev_iteration)
        if not os.path.isfile(summary_path):
            return ""

        try:
            with open(summary_path, "r") as f:
                entries = json.load(f)
        except (json.JSONDecodeError, OSError):
            return ""

        if not entries:
            return ""

        lines = [
            "In iteration %d, the agent made these decisions:" % prev_iteration,
            "",
        ]
        for entry in entries:
            cid = entry.get("comment_id", "?")
            status = entry.get("status", "unknown")
            message = entry.get("message", "")
            if status == "fixed":
                lines.append("- Comment %s: FIXED — %s" % (cid, message or "no details"))
            elif status == "skipped":
                lines.append("- Comment %s: SKIPPED — %s" % (cid, message or "no reason given"))
            else:
                lines.append("- Comment %s: %s — %s" % (cid, status.upper(), message))

        lines.append("")
        lines.append(
            "The comments below are STILL unresolved after re-review. "
            "The previous fix may not have fully addressed the concern, "
            "or Copilot found new issues. Adjust your approach accordingly."
        )

        return "\n".join(lines)

    def _do_fix(self):
        """Run copilot agent to address review comments."""
        pr_number = self.task["pr_number"]
        iteration = self.task["iteration"]

        # Use the unresolved comments fetched in PARSE_REVIEW
        unresolved = getattr(self, "_current_comments", None)
        if unresolved is None:
            unresolved = get_unresolved_review_comments(pr_number)

        # Get latest review body for context
        review = get_copilot_review(pr_number)
        review_body = review.get("body", "") if review else ""

        # Load previous iteration's fix summary for context carry-forward
        previous_context = self._load_previous_fix_summary(iteration)

        # Format for prompt
        review_text = format_review_for_prompt(review_body, unresolved)
        prompt = fix_prompt(
            review_comments_text=review_text,
            custom_instructions=self.config.get("custom_instructions", ""),
            previous_context=previous_context,
        )

        # Record head SHA before fix
        self._pre_fix_sha = get_head_sha(self.task["branch"])

        # Snapshot the review timestamp BEFORE the agent pushes,
        # so any review arriving after the push (auto-triggered or
        # explicitly requested) has a newer submitted_at timestamp.
        current_review = get_copilot_review(pr_number)
        if current_review:
            self._last_review_ts = current_review.get("submitted_at", "")
            logger.debug("[%s] Snapshot review timestamp before fix: %s", self.task_id, self._last_review_ts)

        result = self._run_agent_with_retry("FIX", prompt, "fix-%d" % iteration)
        if result is None:
            return "FAILED"

        logger.info("[%s] \u2713 Fix agent completed (exit %d, %.1fs)", self.task_id, result.exit_code, result.duration)
        return "VERIFY_PUSH"

    def _do_resolve_comments(self):
        """Reply to and resolve review comments based on fix summary."""
        pr_number = self.task["pr_number"]

        # Read the fix summary file written by the agent
        summary_file = os.path.join(os.getcwd(), ".autopilot-fix-summary.json")
        summaries = {}
        if os.path.isfile(summary_file):
            try:
                with open(summary_file, "r") as f:
                    raw = json.load(f)
                for entry in raw:
                    cid = entry.get("comment_id")
                    if cid is not None:
                        summaries[int(cid)] = entry
                logger.info("[%s] Loaded fix summary: %d entries", self.task_id, len(summaries))

                # Persist to sessions dir for context carry-forward to next iteration
                import shutil
                iteration = self.task["iteration"]
                saved = os.path.join(self.sessions_dir, "fix-summary-%d.json" % iteration)
                shutil.copy2(summary_file, saved)
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("[%s] Could not parse fix summary: %s", self.task_id, e)

            # Clean up the file
            try:
                os.remove(summary_file)
            except OSError:
                pass
        else:
            logger.info("[%s] No fix summary file found, resolving all as addressed", self.task_id)

        # Get the latest commit SHA for referencing in replies
        head_sha = get_head_sha(self.task["branch"]) or "latest commit"
        short_sha = head_sha[:7] if len(head_sha) >= 7 else head_sha

        # Get the comments we need to resolve
        comments = getattr(self, "_current_comments", None)
        if comments is None:
            comments = get_unresolved_review_comments(pr_number)

        resolved_count = 0
        for comment in comments:
            comment_id = comment.get("id")
            thread_id = comment.get("thread_id")
            if not comment_id or not thread_id:
                continue

            summary = summaries.get(comment_id, {})
            status = summary.get("status", "fixed")
            message = summary.get("message", "")

            # Build reply
            PREFIX = "\U0001f916 **autopilot-loop**" # Robot emoji for clarity in PR comments
            if status == "skipped":
                reply_body = (
                    "%s: Skipped \u2014 %s" % (PREFIX, message)
                    if message
                    else "%s: Skipped \u2014 determined not worth addressing" % PREFIX
                )
            else:
                reply_body = (
                    "%s: Addressed in %s \u2014 %s" % (PREFIX, short_sha, message)
                    if message
                    else "%s: Addressed in %s" % (PREFIX, short_sha)
                )

            try:
                reply_to_comment(pr_number, comment_id, reply_body)
                resolve_review_thread(thread_id)
                resolved_count += 1
                logger.debug("[%s] Resolved comment %d: %s", self.task_id, comment_id, status)
            except Exception as e:
                logger.warning("[%s] Failed to resolve comment %d: %s", self.task_id, comment_id, e)

        logger.info("[%s] \u2713 Resolved %d/%d comments", self.task_id, resolved_count, len(comments))
        return "REQUEST_REVIEW"


class CIOrchestrator(BaseOrchestrator):
    """State machine orchestrator for fixing CI failures.

    Loop: INIT → FETCH_ANNOTATIONS → FIX_CI → VERIFY_PUSH → WAIT_CI →
    FETCH_ANNOTATIONS → ... → COMPLETE.
    """

    def _get_handlers(self):
        return {
            "INIT": self._do_init,
            "FETCH_ANNOTATIONS": self._do_fetch_annotations,
            "FIX_CI": self._do_fix_ci,
            "VERIFY_PUSH": self._do_verify_push,
            "WAIT_CI": self._do_wait_ci,
        }

    def _init_next_state(self):
        return "FETCH_ANNOTATIONS"

    def _after_verify_push(self):
        return "WAIT_CI"

    def _retry_fix_state(self):
        return "FIX_CI"

    def _do_fetch_annotations(self):
        """Fetch failure annotations for the selected CI checks."""
        pr_number = self.task["pr_number"]
        iteration = self.task["iteration"] + 1
        max_iterations = self.task["max_iterations"]

        # Get the user-selected check names from the task
        ci_check_names = json.loads(self.task.get("ci_check_names") or "[]")
        if not ci_check_names:
            logger.error("[%s] No CI check names configured", self.task_id)
            return "FAILED"

        # Get current failed checks to find job IDs
        all_failed = get_failed_checks(pr_number)
        if all_failed is None:
            logger.error("[%s] Could not fetch CI checks (API error)", self.task_id)
            return "FAILED"
        selected = [c for c in all_failed if c["name"] in ci_check_names]

        # Collect job IDs for annotation fetching
        job_ids = [c["job_id"] for c in selected if c.get("job_id")]

        if not job_ids:
            # Check if the selected checks are now passing
            states = get_check_states(pr_number, ci_check_names)
            if states is None:
                logger.error("[%s] Could not fetch check states (API error)", self.task_id)
                return "FAILED"
            all_passing = all(s == "SUCCESS" for s in states.values())
            if all_passing:
                logger.info("[%s] \u2713 All selected checks are passing!", self.task_id)
                return "COMPLETE"

            # Checks are in a non-failure state (pending, etc.) or have no job IDs
            logger.warning("[%s] No job IDs found for selected checks. States: %s", self.task_id, states)
            return "COMPLETE"

        annotations = get_check_annotations(job_ids)

        # Save annotation data
        ann_file = os.path.join(self.sessions_dir, "ci-annotations-%d.json" % iteration)
        with open(ann_file, "w") as f:
            json.dump(annotations, f, indent=2)

        update_task(self.task_id, iteration=iteration)

        if not annotations:
            logger.info("[%s] \u2713 No actionable CI annotations found", self.task_id)
            return "COMPLETE"

        logger.info("[%s] %d CI failure annotations found:", self.task_id, len(annotations))
        for i, a in enumerate(annotations, 1):
            logger.info("[%s]   %d. %s:%s \u2014 %s",
                        self.task_id, i, a.get("path", "?"), a.get("start_line", "?"),
                        a.get("title", "")[:80])

        if iteration >= max_iterations:
            logger.warning(
                "[%s] Reached max iterations (%d/%d) with %d CI failures remaining",
                self.task_id, iteration, max_iterations, len(annotations),
            )
            return "COMPLETE"

        # Store for FIX_CI
        self._current_annotations = annotations
        return "FIX_CI"

    def _do_fix_ci(self):
        """Run copilot agent to fix CI failures."""
        iteration = self.task["iteration"]

        annotations = getattr(self, "_current_annotations", None)
        if annotations is None:
            # Re-fetch if not cached (e.g., after restart)
            ci_check_names = json.loads(self.task.get("ci_check_names") or "[]")
            all_failed = get_failed_checks(self.task["pr_number"])
            if all_failed is None:
                all_failed = []
            selected = [c for c in all_failed if c["name"] in ci_check_names]
            job_ids = [c["job_id"] for c in selected if c.get("job_id")]
            annotations = get_check_annotations(job_ids)

        annotations_text = format_ci_annotations_for_prompt(annotations)
        prompt = fix_ci_prompt(
            ci_annotations_text=annotations_text,
            custom_instructions=self.config.get("custom_instructions", ""),
        )

        self._pre_fix_sha = get_head_sha(self.task["branch"])

        result = self._run_agent_with_retry("FIX_CI", prompt, "fix-ci-%d" % iteration)
        if result is None:
            return "FAILED"

        logger.info("[%s] \u2713 CI fix agent completed (exit %d, %.1fs)",
                    self.task_id, result.exit_code, result.duration)
        return "VERIFY_PUSH"

    def _do_wait_ci(self):
        """Poll selected CI checks until they pass or timeout."""
        pr_number = self.task["pr_number"]
        ci_check_names = json.loads(self.task.get("ci_check_names") or "[]")
        poll_interval = self.config.get("ci_poll_interval_seconds", 120)
        timeout = self.config.get("ci_poll_timeout_seconds", 5400)
        start_time = time.time()

        logger.info("[%s] Polling %d checks every %ds (timeout: %ds)...",
                    self.task_id, len(ci_check_names), poll_interval, timeout)

        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout:
                logger.warning(
                    "[%s] CI poll timed out after %ds",
                    self.task_id, timeout,
                )
                return "COMPLETE"

            states = get_check_states(pr_number, ci_check_names)
            if states is None:
                logger.warning("[%s] Could not fetch check states, will retry", self.task_id)
                time.sleep(poll_interval)
                continue

            # Check if all selected checks have completed
            pending = [n for n, s in states.items() if s not in ("SUCCESS", "FAILURE", "ERROR")]
            failed = [n for n, s in states.items() if s in ("FAILURE", "ERROR")]
            passed = [n for n, s in states.items() if s == "SUCCESS"]

            if not pending:
                if not failed:
                    logger.info("[%s] \u2713 All %d selected checks passed!", self.task_id, len(passed))
                    return "COMPLETE"
                else:
                    logger.info("[%s] %d checks still failing: %s",
                                self.task_id, len(failed), ", ".join(failed))
                    return "FETCH_ANNOTATIONS"

            logger.debug("[%s] %d checks pending, %d passed, %d failed (%.0f/%ds elapsed)",
                         self.task_id, len(pending), len(passed), len(failed), elapsed, timeout)
            time.sleep(poll_interval)
