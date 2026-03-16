"""Tests for GitHub API integration."""

import json
from unittest.mock import MagicMock, patch

from autopilot_loop.github_api import (
    find_pr_for_branch,
    get_copilot_inline_comments,
    get_copilot_review,
    get_issue,
    get_repo_nwo,
    request_copilot_review,
    verify_new_commits,
)


def _mock_run(stdout="", returncode=0, stderr=""):
    """Create a mock subprocess.run result."""
    result = MagicMock()
    result.stdout = stdout
    result.stderr = stderr
    result.returncode = returncode
    return result


class TestGetRepoNwo:
    def test_returns_nwo(self):
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("octocat/hello-world")):
            assert get_repo_nwo() == "octocat/hello-world"


class TestFindPrForBranch:
    def test_found(self):
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("42")):
            assert find_pr_for_branch("autopilot/abc") == 42

    def test_not_found(self):
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("")):
            assert find_pr_for_branch("autopilot/abc") is None

    def test_non_numeric(self):
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("null")):
            assert find_pr_for_branch("autopilot/abc") is None


class TestRequestCopilotReview:
    def test_api_success(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",  # get_repo_nwo
                "",  # API call
            ]
            request_copilot_review(42)
            # Verify it used the correct bot login
            api_call = mock_gh.call_args_list[1]
            assert "copilot-pull-request-reviewer[bot]" in api_call[0][0][-1]


class TestGetCopilotReview:
    def test_found(self):
        review_data = {"id": 123, "body": "## Overview", "state": "COMMENTED"}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(review_data),
            ]
            result = get_copilot_review(42)
            assert result == review_data

    def test_not_found(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "null",
            ]
            assert get_copilot_review(42) is None


class TestGetCopilotInlineComments:
    def test_found(self):
        comments = [
            {"path": "a.rb", "original_line": 10, "body": "fix this", "diff_hunk": "+code"}
        ]
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(comments),
            ]
            result = get_copilot_inline_comments(42)
            assert len(result) == 1
            assert result[0]["path"] == "a.rb"

    def test_empty(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "[]",
            ]
            assert get_copilot_inline_comments(42) == []


class TestGetIssue:
    def test_returns_issue(self):
        issue_data = {"title": "Bug in X", "body": "Steps to reproduce..."}
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run(json.dumps(issue_data))):
            result = get_issue(123)
            assert result["title"] == "Bug in X"


class TestVerifyNewCommits:
    def test_new_commits(self):
        with patch("autopilot_loop.github_api.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _mock_run(),  # git fetch
                _mock_run("newsha123"),  # git rev-parse
            ]
            assert verify_new_commits("branch", "oldsha456") is True

    def test_no_new_commits(self):
        with patch("autopilot_loop.github_api.subprocess.run") as mock_run:
            mock_run.side_effect = [
                _mock_run(),  # git fetch
                _mock_run("samesha"),  # git rev-parse
            ]
            assert verify_new_commits("branch", "samesha") is False
