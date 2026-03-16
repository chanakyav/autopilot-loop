"""GitHub API integration via `gh` CLI and `gh api`.

Handles PR lifecycle verification, Copilot review requests, polling,
and inline comment fetching.
"""

import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

__all__ = [
    "GitHubAPIError",
    "find_pr_for_branch",
    "get_check_annotations",
    "get_check_states",
    "get_copilot_review",
    "get_failed_checks",
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


_nwo_cache = None


def get_repo_nwo():
    """Get the current repo's owner/name (nameWithOwner).

    Cached after the first call to avoid repeated `gh repo view` subprocess calls.

    Returns:
        String like 'owner/repo'.
    """
    global _nwo_cache
    if _nwo_cache is None:
        _nwo_cache = _run_gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"])
    return _nwo_cache


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
    current_sha = get_head_sha(branch)
    return current_sha is not None and current_sha != since_sha


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
    query = 'mutation($id: ID!) { resolveReviewThread(input: {threadId: $id}) { thread { isResolved } } }'
    _run_gh(["api", "graphql", "-f", "query=%s" % query, "-F", "id=%s" % thread_node_id])
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
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
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
    """

    output = _run_gh([
        "api", "graphql",
        "-f", "query=%s" % query.strip(),
        "-F", "owner=%s" % owner,
        "-F", "name=%s" % repo,
        "-F", "number=%d" % pr_number,
    ], check=False)
    if not output:
        return []

    data = json.loads(output)

    # Check for GraphQL errors
    if "errors" in data:
        msgs = [e.get("message", str(e)) for e in data["errors"]]
        logger.warning("GraphQL errors in get_unresolved_review_comments: %s", "; ".join(msgs))
        if not data.get("data"):
            return []

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


# --- CI check functions ---

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text):
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


def get_failed_checks(pr_number):
    """Get failed CI checks for a PR.

    Excludes aggregation gate checks (names ending in '-results').
    Parses run_id and job_id from the check link URL.

    Returns:
        List of dicts with {name, link, run_id, job_id}.
    """
    output = _run_gh([
        "pr", "checks", str(pr_number),
        "--json", "name,state,link",
        "--jq", '[.[] | select(.state == "FAILURE")]',
    ], check=False)

    if not output:
        return []

    raw_checks = json.loads(output)
    checks = []
    for c in raw_checks:
        name = c.get("name", "")
        # Skip aggregation gates (e.g., "build-results", "test-results")
        if name.endswith("-results"):
            continue

        link = c.get("link", "")
        run_id = None
        job_id = None
        # Parse from URL: /actions/runs/{run_id}/job/{job_id}
        m = re.search(r"/actions/runs/(\d+)/job/(\d+)", link)
        if m:
            run_id = int(m.group(1))
            job_id = int(m.group(2))

        checks.append({
            "name": name,
            "link": link,
            "run_id": run_id,
            "job_id": job_id,
        })

    return checks


def get_check_annotations(job_ids):
    """Fetch failure annotations for CI jobs.

    Calls /check-runs/{id}/annotations for each job, filters to
    annotation_level == 'failure', deduplicates by (path, start_line),
    strips ANSI codes, and excludes generic 'Process completed' messages.

    Args:
        job_ids: List of job IDs (ints).

    Returns:
        List of dicts with {path, start_line, end_line, title, message}.
    """
    nwo = get_repo_nwo()
    seen = set()  # (path, start_line) for dedup
    annotations = []

    for job_id in job_ids:
        output = _run_gh([
            "api", "repos/%s/check-runs/%d/annotations" % (nwo, job_id),
        ], check=False)

        if not output:
            continue

        raw = json.loads(output)
        for ann in raw:
            if ann.get("annotation_level") != "failure":
                continue

            path = ann.get("path", "")
            start_line = ann.get("start_line", 0)
            message = _strip_ansi(ann.get("message", ""))
            title = _strip_ansi(ann.get("title", ""))

            # Skip generic "Process completed with exit code N" messages
            if message.startswith("Process completed with exit code"):
                continue

            key = (path, start_line)
            if key in seen:
                continue
            seen.add(key)

            annotations.append({
                "path": path,
                "start_line": start_line,
                "end_line": ann.get("end_line", start_line),
                "title": title,
                "message": message,
            })

    return annotations[:50]  # Cap to avoid prompt bloat


def get_check_states(pr_number, check_names):
    """Get the current state of specific checks on a PR.

    Args:
        pr_number: PR number.
        check_names: List of check names to query.

    Returns:
        Dict mapping check name to state string (e.g., 'SUCCESS', 'FAILURE', 'PENDING').
    """
    output = _run_gh([
        "pr", "checks", str(pr_number),
        "--json", "name,state",
    ], check=False)

    if not output:
        return {name: "UNKNOWN" for name in check_names}

    raw = json.loads(output)
    state_map = {c["name"]: c["state"] for c in raw}
    return {name: state_map.get(name, "UNKNOWN") for name in check_names}
