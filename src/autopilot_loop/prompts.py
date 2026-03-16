"""Prompt templates for copilot agent invocations.

Each function returns a prompt string that instructs copilot -p
on what to do, including git operations, self-review, and PR creation.
"""

__all__ = [
    "implement_prompt",
    "plan_and_implement_prompt",
    "fix_prompt",
    "fix_ci_prompt",
    "format_review_for_prompt",
    "format_ci_annotations_for_prompt",
]


def implement_prompt(task_description, branch_name, custom_instructions=""):
    """Prompt for the IMPLEMENT phase.

    Agent implements the task, creates branch, commits, creates draft PR
    using the repo's PR template, pushes, and self-reviews.
    """
    parts = []

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## Task")
    parts.append(task_description.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "1. Implement the task described above.\n"
        "2. Run any relevant tests to verify your changes work.\n"
        "3. Create a new git branch named `%s`:\n"
        "   git checkout -b %s\n"
        "4. Stage and commit your changes with a proper, descriptive commit message\n"
        "   that explains what was changed and why. Do NOT use generic messages.\n"
        "5. Create a draft pull request using `gh pr create --draft`. Use the repo's\n"
        "   PR template (check `.github/PULL_REQUEST_TEMPLATE.md` or similar) to\n"
        "   structure the PR body. Write a clear title and fill in the template sections.\n"
        "6. Push the branch: `git push -u origin %s`\n"
        "7. After pushing, review your own changes by running `git diff main` and\n"
        "   examining the output. If you find any issues (bugs, missing tests, style\n"
        "   problems), fix them, commit with a descriptive message, and push again.\n"
        % (branch_name, branch_name, branch_name)
    )

    return "\n".join(parts)


def plan_and_implement_prompt(task_description, branch_name, custom_instructions=""):
    """Prompt for the PLAN_AND_IMPLEMENT phase.

    Same as implement but asks the agent to plan first.
    """
    parts = []

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## Task")
    parts.append(task_description.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "1. First, analyze the task and create a plan. Think through:\n"
        "   - What files need to be changed\n"
        "   - What the implementation approach should be\n"
        "   - What tests are needed\n"
        "   - Any edge cases or risks\n"
        "2. Then implement the plan.\n"
        "3. Run any relevant tests to verify your changes work.\n"
        "4. Create a new git branch named `%s`:\n"
        "   git checkout -b %s\n"
        "5. Stage and commit your changes with a proper, descriptive commit message\n"
        "   that explains what was changed and why. Do NOT use generic messages.\n"
        "6. Create a draft pull request using `gh pr create --draft`. Use the repo's\n"
        "   PR template (check `.github/PULL_REQUEST_TEMPLATE.md` or similar) to\n"
        "   structure the PR body. Write a clear title and fill in the template sections.\n"
        "7. Push the branch: `git push -u origin %s`\n"
        "8. After pushing, review your own changes by running `git diff main` and\n"
        "   examining the output. If you find any issues (bugs, missing tests, style\n"
        "   problems), fix them, commit with a descriptive message, and push again.\n"
        % (branch_name, branch_name, branch_name)
    )

    return "\n".join(parts)


def fix_prompt(review_comments_text, custom_instructions=""):
    """Prompt for the FIX phase.

    Agent addresses PR review comments, commits, pushes, self-reviews,
    and writes a summary file for each comment.
    """
    parts = []

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## Copilot Review Feedback to Address")
    parts.append("")
    parts.append(review_comments_text.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "1. Review each comment above. Use your judgment to decide what's worth\n"
        "   addressing. If a comment is a valid concern, fix it. If it's not worth\n"
        "   changing (e.g., subjective style preference, or the current code is\n"
        "   already correct), skip it.\n"
        "2. Make the necessary code changes.\n"
        "3. Run any relevant tests to verify your fixes work.\n"
        "4. Commit with a descriptive message that explains what review feedback\n"
        "   was addressed. Do NOT use generic messages like 'fix review comments'.\n"
        "   Instead, describe the specific changes, e.g.:\n"
        "   'Fix dirty-tracking semantics in user_queries, add missing\n"
        "    type annotation to billing_service'\n"
        "5. Push the changes.\n"
        "6. After pushing, review your own fix by running `git diff HEAD~1` and\n"
        "   examining the output. If you introduced any new issues while fixing\n"
        "   the review comments, fix them, commit, and push again.\n"
        "7. IMPORTANT: Write a JSON file at `.autopilot-fix-summary.json` in the\n"
        "   repo root with your resolution for EACH comment. Use this exact format:\n"
        "   ```json\n"
        '   [\n'
        '     {"comment_id": <id>, "status": "fixed", "message": "brief description of fix"},\n'
        '     {"comment_id": <id>, "status": "skipped", "message": "reason for skipping"}\n'
        '   ]\n'
        "   ```\n"
        "   The comment_id values are provided in the comments above (as `[comment_id: N]`).\n"
        "   Status must be either `fixed` or `skipped`.\n"
        "   Do NOT commit this file — just write it to disk.\n"
    )

    return "\n".join(parts)


def format_review_for_prompt(review_body, inline_comments):
    """Format review data as text for the fix prompt.

    Args:
        review_body: The top-level review body text (overview).
        inline_comments: List of dicts with {path, original_line, body, diff_hunk}.

    Returns:
        Formatted string ready to embed in the fix prompt.
    """
    parts = []

    if review_body:
        parts.append("### Review Summary")
        parts.append(review_body.strip())
        parts.append("")

    if inline_comments:
        parts.append("### Inline Comments (%d)" % len(inline_comments))
        parts.append("")
        for i, comment in enumerate(inline_comments, 1):
            comment_id = comment.get("id", "?")
            path = comment.get("path", "unknown")
            line = comment.get("line", comment.get("original_line", "?"))
            body = comment.get("body", "").strip()
            parts.append("**Comment %d** [comment_id: %s] — `%s` (line %s):" % (i, comment_id, path, line))
            parts.append(body)
            parts.append("")
    else:
        parts.append("No inline comments.")

    return "\n".join(parts)


def fix_ci_prompt(ci_annotations_text, custom_instructions=""):
    """Prompt for the FIX_CI phase.

    Agent fixes CI failures based on annotations, runs the full test suite,
    commits, and pushes.
    """
    parts = []

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## CI Failures to Fix")
    parts.append("")
    parts.append(ci_annotations_text.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "1. Read each CI failure above. The path and line number point to the\n"
        "   exact location of the failure. Open the file and understand what broke.\n"
        "2. Fix the root cause. If multiple failures share the same root cause\n"
        "   (e.g., a missing test case for a new route), fix it once.\n"
        "3. Run the full test suite to verify your fixes don't break anything \u2014\n"
        "   not just the tests that failed. Adjacent test files often break too.\n"
        "4. Commit with a descriptive message that explains what CI failures\n"
        "   were addressed. Do NOT use generic messages like 'fix CI'.\n"
        "   Instead, describe the specific changes.\n"
        "5. Push the changes.\n"
        "6. After pushing, review your own fix by running `git diff HEAD~1` and\n"
        "   examining the output. If you introduced any new issues, fix them,\n"
        "   commit, and push again.\n"
    )

    return "\n".join(parts)


def format_ci_annotations_for_prompt(annotations):
    """Format CI failure annotations for the fix_ci prompt.

    Args:
        annotations: List of dicts with {path, start_line, end_line, title, message}.

    Returns:
        Formatted string ready to embed in the fix_ci prompt.
    """
    if not annotations:
        return "No CI failure annotations found."

    parts = ["### CI Failure Annotations (%d)" % len(annotations), ""]
    for i, ann in enumerate(annotations, 1):
        path = ann.get("path", "unknown")
        line = ann.get("start_line", "?")
        title = ann.get("title", "").strip()
        message = ann.get("message", "").strip()

        parts.append("**Failure %d** \u2014 `%s` (line %s):" % (i, path, line))
        if title:
            parts.append("  %s" % title)
        if message:
            # Limit message length to avoid prompt bloat
            if len(message) > 1000:
                message = message[:1000] + "\n  ... (truncated)"
            parts.append(message)
        parts.append("")

    return "\n".join(parts)
