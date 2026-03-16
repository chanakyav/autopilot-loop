"""Tests for GitHub API integration."""

import json
from unittest.mock import MagicMock, patch

import pytest

import autopilot_loop.github_api as github_api_module
from autopilot_loop.github_api import (
    find_pr_for_branch,
    get_copilot_review,
    get_issue,
    get_repo_nwo,
    get_unresolved_review_comments,
    reply_to_comment,
    request_copilot_review,
    resolve_review_thread,
    verify_new_commits,
)


@pytest.fixture(autouse=True)
def reset_nwo_cache():
    """Reset the NWO cache between tests."""
    github_api_module._nwo_cache = None
    yield
    github_api_module._nwo_cache = None


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

    def test_caches_result(self):
        mock_result = _mock_run("octocat/hello-world")
        with patch("autopilot_loop.github_api.subprocess.run", return_value=mock_result) as mock_run:
            get_repo_nwo()
            get_repo_nwo()
            # Should only call subprocess once due to caching
            assert mock_run.call_count == 1


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

    def test_after_id_filters_old(self):
        review_data = {"id": 100, "body": "old review", "state": "COMMENTED"}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(review_data),
            ]
            # Review id 100 is not > after_id 100
            assert get_copilot_review(42, after_id=100) is None

    def test_after_id_allows_new(self):
        review_data = {"id": 200, "body": "new review", "state": "COMMENTED"}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(review_data),
            ]
            result = get_copilot_review(42, after_id=100)
            assert result == review_data


class TestReplyToComment:
    def test_posts_reply(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",  # get_repo_nwo
                "",  # API call
            ]
            reply_to_comment(42, 123, "test reply")
            api_call = mock_gh.call_args_list[1]
            args = api_call[0][0]
            assert "comments" in args[1]
            assert "in_reply_to" in " ".join(args)


class TestResolveReviewThread:
    def test_resolves_thread(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            resolve_review_thread("thread_node_123")
            call_args = mock_gh.call_args[0][0]
            assert "graphql" in call_args
            assert "resolveReviewThread" in " ".join(call_args)
            # Verify parameterized variable is used (not string interpolation)
            assert any("id=thread_node_123" in a for a in call_args)


class TestGetUnresolvedReviewComments:
    def test_returns_unresolved_copilot_comments(self):
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {
                                    "id": "T1",
                                    "isResolved": False,
                                    "comments": {"nodes": [
                                        {"id": "C1", "databaseId": 100,
                                         "author": {"login": "copilot-pull-request-reviewer"},
                                         "body": "fix this", "path": "a.rb", "line": 10}
                                    ]},
                                },
                                {
                                    "id": "T2",
                                    "isResolved": True,
                                    "comments": {"nodes": [
                                        {"id": "C2", "databaseId": 200,
                                         "author": {"login": "copilot-pull-request-reviewer"},
                                         "body": "already resolved", "path": "b.rb", "line": 20}
                                    ]},
                                },
                                {
                                    "id": "T3",
                                    "isResolved": False,
                                    "comments": {"nodes": [
                                        {"id": "C3", "databaseId": 300, "author": {"login": "human-dev"},
                                         "body": "human comment", "path": "c.rb", "line": 5}
                                    ]},
                                },
                            ]
                        }
                    }
                }
            }
        }
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",  # get_repo_nwo
                json.dumps(graphql_response),  # GraphQL query
            ]
            result = get_unresolved_review_comments(42)
            # Only T1 should be returned (unresolved + Copilot author)
            assert len(result) == 1
            assert result[0]["id"] == 100
            assert result[0]["thread_id"] == "T1"
            assert result[0]["body"] == "fix this"

            # Verify parameterized variables are used
            gql_call = mock_gh.call_args_list[1]
            gql_args = gql_call[0][0]
            assert any("owner=octocat" in a for a in gql_args)
            assert any("name=hello-world" in a for a in gql_args)
            assert any("number=42" in a for a in gql_args)

    def test_empty_when_all_resolved(self):
        graphql_response = {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "nodes": [
                                {"id": "T1", "isResolved": True, "comments": {"nodes": [
                                    {"id": "C1", "databaseId": 100,
                                     "author": {"login": "copilot-pull-request-reviewer"},
                                     "body": "resolved", "path": "a.rb", "line": 10}
                                ]}},
                            ]
                        }
                    }
                }
            }
        }
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(graphql_response),
            ]
            assert get_unresolved_review_comments(42) == []


class TestGetIssue:
    def test_returns_issue(self):
        issue_data = {"title": "Bug in X", "body": "Steps to reproduce..."}
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run(json.dumps(issue_data))):
            result = get_issue(123)
            assert result["title"] == "Bug in X"


class TestVerifyNewCommits:
    @patch("autopilot_loop.github_api.get_head_sha")
    def test_new_commits(self, mock_sha):
        mock_sha.return_value = "newsha123"
        assert verify_new_commits("branch", "oldsha456") is True

    @patch("autopilot_loop.github_api.get_head_sha")
    def test_no_new_commits(self, mock_sha):
        mock_sha.return_value = "samesha"
        assert verify_new_commits("branch", "samesha") is False

    @patch("autopilot_loop.github_api.get_head_sha")
    def test_error_returns_false(self, mock_sha):
        mock_sha.return_value = None
        assert verify_new_commits("branch", "sha") is False
