"""Prompt templates for copilot agent invocations.

Each function returns a prompt string that instructs copilot -p
on what to do, including git operations, self-review, and PR creation.
"""

__all__ = [
    "implement_prompt",
    "implement_on_existing_branch_prompt",
    "plan_and_implement_prompt",
    "fix_prompt",
    "fix_ci_prompt",
    "format_review_for_prompt",
    "format_ci_annotations_for_prompt",
    "update_description_prompt",
]


def _file_protection_instruction(prompt_file):
    """Return an instruction to protect the prompt file, or empty string."""
    if not prompt_file:
        return ""
    return (
        "IMPORTANT: Do NOT read, modify, rename, delete, or commit the file `%s`.\n"
        "It contains the task instructions and must remain unchanged.\n"
        % prompt_file
    )


def implement_prompt(task_description, branch_name, custom_instructions="", prompt_file=None):
    """Prompt for the IMPLEMENT phase.

    Agent implements the task, creates branch, commits, creates draft PR
    using the repo's PR template, pushes, and self-reviews.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

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


def implement_on_existing_branch_prompt(task_description, branch_name, custom_instructions="", prompt_file=None):
    """Prompt for the IMPLEMENT phase on an existing branch.

    Used when the user starts a new task on a branch that already exists
    (e.g. autopilot/<task-id>). The agent should NOT create a new branch
    and should commit directly on the current branch.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## Task")
    parts.append(task_description.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "IMPORTANT: You are on the existing branch `%s`. Do NOT create a new branch.\n"
        "Work directly on this branch.\n\n"
        "1. Implement the task described above.\n"
        "2. Run any relevant tests to verify your changes work.\n"
        "3. Stage and commit your changes with a proper, descriptive commit message\n"
        "   that explains what was changed and why. Do NOT use generic messages.\n"
        "4. If a PR already exists for this branch, push your changes. If no PR exists,\n"
        "   create a draft pull request using `gh pr create --draft`. Use the repo's\n"
        "   PR template (check `.github/PULL_REQUEST_TEMPLATE.md` or similar) to\n"
        "   structure the PR body. Write a clear title and fill in the template sections.\n"
        "5. Push the branch: `git push origin %s`\n"
        "6. After pushing, review your own changes by running `git diff HEAD~1` and\n"
        "   examining the output. If you find any issues (bugs, missing tests, style\n"
        "   problems), fix them, commit with a descriptive message, and push again.\n"
        % (branch_name, branch_name)
    )

    return "\n".join(parts)


def plan_and_implement_prompt(task_description, branch_name, custom_instructions="", prompt_file=None):
    """Prompt for the PLAN_AND_IMPLEMENT phase.

    Same as implement but asks the agent to plan first.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

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


def fix_prompt(review_comments_text, custom_instructions="", previous_context="",
               bouncing_comments="", prompt_file=None):
    """Prompt for the FIX phase.

    Agent addresses PR review comments using a 3-tier verification model,
    commits, pushes, self-reviews, and writes a summary file for each comment.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    if previous_context:
        parts.append("## Previous Iteration Context")
        parts.append("")
        parts.append(previous_context.strip())
        parts.append("")

    if bouncing_comments:
        parts.append("## \u26a0\ufe0f Circular Review Loop Detected")
        parts.append("")
        parts.append(bouncing_comments.strip())
        parts.append("")

    parts.append("## Copilot Review Feedback to Address")
    parts.append("")
    parts.append(review_comments_text.strip())
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "CRITICAL: Do NOT blindly apply every review suggestion. Copilot Code Review\n"
        "(CCR) can be wrong, contradictory, or subjective. You must VERIFY each claim\n"
        "before acting. Use the 3-tier decision model below.\n"
        "\n"
        "### Verification steps (do these for EVERY comment before deciding)\n"
        "\n"
        "a. Read the surrounding code context (the full function, not just the flagged line).\n"
        "b. Check if existing tests already cover the scenario CCR mentions.\n"
        "c. If CCR suggests adding or removing something, verify the claim against actual\n"
        "   usage in the codebase (grep for callers, check API contracts, read specs).\n"
        "d. If CCR says a field should be optional/required, check the schema, callers,\n"
        "   and any API documentation to confirm.\n"
        "\n"
        "### 3-tier decision model\n"
        "\n"
        "**Tier 1 \u2014 AGREE & FIX**: CCR is clearly correct (real bug, missing null check,\n"
        "failing test, security issue). Fix it. Set status to `\"fixed\"`.\n"
        "\n"
        "**Tier 2 \u2014 DISAGREE with evidence**: CCR is wrong and you have concrete proof\n"
        "(e.g., \"this field is required by the API contract in src/schema.py:42\",\n"
        "\"the test at tests/test_foo.py:100 already covers this\"). Do NOT make the\n"
        "change. Set status to `\"dismissed\"`. You MUST include an `\"evidence\"` field\n"
        "explaining why CCR is wrong with specific file/line references.\n"
        "\n"
        "**Tier 3 \u2014 UNCERTAIN**: You cannot definitively prove CCR right or wrong.\n"
        "Do NOT make the change. Set status to `\"uncertain\"`. Include an `\"evidence\"`\n"
        "field explaining what you checked and why you are unsure. A human will review.\n"
        "\n"
        "### Steps\n"
        "\n"
        "1. For each comment, perform the verification steps above, then classify\n"
        "   using the 3-tier model.\n"
        "2. Make code changes ONLY for Tier 1 (fixed) comments.\n"
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
        '     {"comment_id": <id>, "status": "skipped", "message": "reason for skipping"},\n'
        '     {"comment_id": <id>, "status": "dismissed", "message": "summary",\n'
        '      "evidence": "concrete proof with file:line refs"},\n'
        '     {"comment_id": <id>, "status": "uncertain", "message": "summary",\n'
        '      "evidence": "what I checked and why I am unsure"}\n'
        '   ]\n'
        "   ```\n"
        "   The comment_id values are provided in the comments above (as `[comment_id: N]`).\n"
        "   Status must be `fixed`, `skipped`, `dismissed`, or `uncertain`.\n"
        "   The `evidence` field is REQUIRED for `dismissed` and `uncertain` statuses.\n"
        "   Do NOT commit this file \u2014 just write it to disk.\n"
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


def fix_ci_prompt(ci_annotations_text, custom_instructions="", prompt_file=None):
    """Prompt for the FIX_CI phase.

    Agent fixes CI failures based on annotations, runs the full test suite,
    commits, and pushes.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

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


def update_description_prompt(task_description, current_pr_body, diff_stat,
                              custom_instructions="", prompt_file=None):
    """Prompt for the UPDATE_DESCRIPTION phase.

    Agent rewrites the PR description to reflect the current state of changes.
    The output is captured and used to update the PR body via gh pr edit.
    """
    parts = []

    file_protection = _file_protection_instruction(prompt_file)
    if file_protection:
        parts.append(file_protection)

    if custom_instructions:
        parts.append(custom_instructions.strip())
        parts.append("")

    parts.append("## Task")
    parts.append("Update the PR description to accurately reflect the current state of changes.")
    parts.append("")
    parts.append("## Original Task Description")
    parts.append(task_description.strip())
    parts.append("")
    parts.append("## Current PR Description")
    parts.append("```")
    parts.append(current_pr_body.strip() if current_pr_body else "(empty)")
    parts.append("```")
    parts.append("")
    parts.append("## Current Diff Summary")
    parts.append("```")
    parts.append(diff_stat.strip() if diff_stat else "(no changes)")
    parts.append("```")
    parts.append("")
    parts.append("## Instructions")
    parts.append(
        "1. Read the current diff by running `git diff main` to understand what\n"
        "   the PR actually does now (not just what was originally planned).\n"
        "2. Write an updated PR description that accurately reflects the CURRENT\n"
        "   state of the code changes. The description should:\n"
        "   - Summarize what changed and why\n"
        "   - Mention key implementation decisions\n"
        "   - Note any trade-offs or known limitations\n"
        "3. If the repo has a PR template (check `.github/PULL_REQUEST_TEMPLATE.md`),\n"
        "   preserve its structure and fill in the sections.\n"
        "4. Update the PR using:\n"
        "   `gh pr edit --body '<updated description>'`\n"
        "   Make sure to properly escape the body content for the shell.\n"
        "5. Do NOT make any code changes. Do NOT commit anything. Only update the\n"
        "   PR description.\n"
    )

    return "\n".join(parts)
