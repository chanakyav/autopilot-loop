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
    "get_copilot_review",
    "get_head_sha",
    "get_issue",
    "get_repo_nwo",
    "get_unresolved_review_comments",
    "is_copilot_review_complete",
    "reply_to_comment",
    "request_copilot_review",
    "resolve_review_thread",
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
        String like 'owner/repo'.
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


def get_copilot_review(pr_number, after_id=None):
    """Get the latest Copilot review for a PR.

    Args:
        pr_number: PR number.
        after_id: If set, only return a review with id > after_id.

    Returns:
        Dict with review data, or None if no matching Copilot review exists.
    """
    nwo = get_repo_nwo()
    output = _run_gh([
        "api", "repos/%s/pulls/%d/reviews" % (nwo, pr_number),
        "--jq", '[.[] | select(.user.login == "copilot-pull-request-reviewer[bot]")] | sort_by(.id) | last',
    ], check=False)

    if not output or output == "null":
        return None

    review = json.loads(output)
    if after_id and review.get("id", 0) <= after_id:
        return None

    return review


def is_copilot_review_complete(pr_number, after_id=None):
    """Check if Copilot has submitted a new review.

    Args:
        pr_number: PR number.
        after_id: If set, only returns True for reviews with id > after_id.

    Returns:
        True if a new Copilot review is present.
    """
    review = get_copilot_review(pr_number, after_id=after_id)
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


def reply_to_comment(pr_number, comment_id, body):
    """Reply to an inline PR review comment.

    Args:
        pr_number: PR number.
        comment_id: The REST API comment ID to reply to.
        body: Reply text.
    """
    nwo = get_repo_nwo()
    _run_gh([
        "api", "repos/%s/pulls/%d/comments" % (nwo, pr_number),
        "-f", "body=%s" % body,
        "-F", "in_reply_to=%d" % comment_id,
    ])
    logger.debug("Replied to comment %d on PR #%d", comment_id, pr_number)


def resolve_review_thread(thread_node_id):
    """Resolve a review thread via GraphQL.

    Args:
        thread_node_id: The GraphQL node ID of the review thread.
    """
    query = 'mutation { resolveReviewThread(input: {threadId: "%s"}) { thread { isResolved } } }' % thread_node_id
    _run_gh(["api", "graphql", "-f", "query=%s" % query])
    logger.debug("Resolved thread %s", thread_node_id)


def get_unresolved_review_comments(pr_number):
    """Get unresolved Copilot review comments via GraphQL.

    Returns only comments from unresolved threads authored by Copilot.

    Returns:
        List of dicts with {id, node_id, thread_id, path, line, body}.
    """
    nwo = get_repo_nwo()
    owner, repo = nwo.split("/", 1)
    query = """
    {
      repository(owner: "%s", name: "%s") {
        pullRequest(number: %d) {
          reviewThreads(first: 100) {
            nodes {
              id
              isResolved
              comments(first: 10) {
                nodes {
                  id
                  databaseId
                  author { login }
                  body
                  path
                  line
                }
              }
            }
          }
        }
      }
    }
    """ % (owner, repo, pr_number)

    output = _run_gh(["api", "graphql", "-f", "query=%s" % query.strip()], check=False)
    if not output:
        return []

    data = json.loads(output)
    threads = (
        data.get("data", {})
        .get("repository", {})
        .get("pullRequest", {})
        .get("reviewThreads", {})
        .get("nodes", [])
    )

    comments = []
    for thread in threads:
        if thread.get("isResolved"):
            continue
        thread_id = thread.get("id")
        thread_comments = thread.get("comments", {}).get("nodes", [])
        if not thread_comments:
            continue
        # First comment in thread is the original review comment
        first = thread_comments[0]
        author = first.get("author", {}).get("login", "")
        # Only include Copilot comments (login varies by API: REST vs GraphQL)
        if author not in ("Copilot", "copilot-pull-request-reviewer[bot]", "copilot-pull-request-reviewer"):
            continue
        comments.append({
            "id": first.get("databaseId"),
            "node_id": first.get("id"),
            "thread_id": thread_id,
            "path": first.get("path", ""),
            "line": first.get("line"),
            "body": first.get("body", ""),
        })

    return comments
