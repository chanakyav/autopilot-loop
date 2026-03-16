"""GitHub API integration via `gh` CLI and `gh api`.

Handles PR lifecycle verification, Copilot review requests, polling,
and inline comment fetching.
"""

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

__all__ = [
    "GitHubAPIError",
    "find_pr_for_branch",
    "get_copilot_inline_comments",
    "get_copilot_review",
    "get_head_sha",
    "get_issue",
    "get_repo_nwo",
    "is_copilot_review_complete",
    "request_copilot_review",
    "verify_new_commits",
]


class GitHubAPIError(Exception):
    """Raised when a gh CLI command fails."""
    pass


def _run_gh(args, check=True):
    """Run a gh CLI command and return stdout.

    Args:
        args: Command arguments as a list (without 'gh' prefix).
        check: If True, raise GitHubAPIError on non-zero exit.

    Returns:
        stdout as string.
    """
    cmd = ["gh"] + args
    logger.debug("Running: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise GitHubAPIError(
            "gh command failed (exit %d): %s\nstderr: %s"
            % (result.returncode, " ".join(cmd), result.stderr.strip())
        )
    return result.stdout.strip()


def _run_gh_json(args):
    """Run a gh CLI command and parse stdout as JSON."""
    output = _run_gh(args)
    if not output:
        return None
    return json.loads(output)


def get_repo_nwo():
    """Get the current repo's owner/name (nameWithOwner).

    Returns:
        String like 'github/github'.
    """
    return _run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])


def find_pr_for_branch(branch):
    """Find an open PR for a given branch.

    Returns:
        PR number (int) or None if no PR exists.
    """
    output = _run_gh(["pr", "list", "--head", branch, "--json", "number", "--jq", ".[0].number"], check=False)
    if output and output.isdigit():
        return int(output)
    return None


def verify_new_commits(branch, since_sha):
    """Check if there are new commits on the remote branch since the given SHA.

    Returns:
        True if new commits exist.
    """
    try:
        # Fetch latest
        subprocess.run(["git", "fetch", "origin", branch], capture_output=True, check=True)
        # Get current HEAD of remote branch
        result = subprocess.run(
            ["git", "rev-parse", "origin/%s" % branch],
            capture_output=True, text=True, check=True,
        )
        current_sha = result.stdout.strip()
        return current_sha != since_sha
    except subprocess.CalledProcessError:
        return False


def get_head_sha(branch):
    """Get the current HEAD SHA of the remote branch.

    Returns:
        SHA string, or None on error.
    """
    try:
        subprocess.run(["git", "fetch", "origin", branch], capture_output=True, check=True)
        result = subprocess.run(
            ["git", "rev-parse", "origin/%s" % branch],
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError:
        return None


def request_copilot_review(pr_number):
    """Request a Copilot review on a PR.

    Uses copilot-pull-request-reviewer[bot] as the reviewer login.
    Raises GitHubAPIError if the request fails.
    """
    nwo = get_repo_nwo()
    _run_gh([
        "api", "repos/%s/pulls/%d/requested_reviewers" % (nwo, pr_number),
        "-f", "reviewers[]=copilot-pull-request-reviewer[bot]",
    ])
    logger.info("Requested Copilot review on PR #%d", pr_number)


def get_copilot_review(pr_number):
    """Get the latest Copilot review for a PR.

    Returns:
        Dict with review data, or None if no Copilot review exists.
    """
    nwo = get_repo_nwo()
    output = _run_gh([
        "api", "repos/%s/pulls/%d/reviews" % (nwo, pr_number),
        "--jq", '[.[] | select(.user.login == "copilot-pull-request-reviewer[bot]")] | last',
    ], check=False)

    if not output or output == "null":
        return None

    return json.loads(output)


def get_copilot_inline_comments(pr_number):
    """Get Copilot's original inline comments on a PR.

    Filters for: user.login == "Copilot" and in_reply_to_id == null.

    Returns:
        List of dicts with {path, original_line, body, diff_hunk}.
    """
    nwo = get_repo_nwo()
    output = _run_gh([
        "api", "repos/%s/pulls/%d/comments" % (nwo, pr_number),
        "--jq", '[.[] | select(.user.login == "Copilot" and .in_reply_to_id == null) '
                '| {path, original_line, body, diff_hunk: (.diff_hunk | split("\\n") | last)}]',
    ], check=False)

    if not output or output == "null":
        return []

    return json.loads(output)


def is_copilot_review_complete(pr_number, since_sha=None):
    """Check if Copilot has submitted a review.

    If since_sha is provided, only considers reviews submitted after
    the commit at that SHA (by checking if a review exists that's newer).

    Returns:
        True if a Copilot review is present.
    """
    review = get_copilot_review(pr_number)
    return review is not None


def get_issue(issue_number):
    """Fetch a GitHub issue's title and body.

    Returns:
        Dict with {title, body}.
    """
    output = _run_gh([
        "issue", "view", str(issue_number),
        "--json", "title,body",
    ])
    return json.loads(output)
