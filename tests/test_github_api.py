"""Tests for GitHub API integration."""

import json
from unittest.mock import MagicMock, patch

import pytest

import autopilot_loop.github_api as github_api_module
from autopilot_loop.github_api import (
    find_pr_for_branch,
    get_check_annotations,
    get_check_states,
    get_copilot_review,
    get_failed_checks,
    get_issue,
    get_latest_copilot_review_thread_ts,
    get_repo_nwo,
    get_unresolved_review_comments,
    is_copilot_pending_reviewer,
    is_copilot_review_complete,
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

    def test_after_ts_filters_old(self):
        review_data = {"id": 100, "body": "old review", "state": "COMMENTED",
                       "submitted_at": "2026-03-18T08:00:00Z"}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(review_data),
            ]
            # Same timestamp means not newer
            assert is_copilot_review_complete(42, after_ts="2026-03-18T08:00:00Z") is False

    def test_after_ts_allows_new(self):
        review_data = {"id": 200, "body": "new review", "state": "COMMENTED",
                       "submitted_at": "2026-03-18T09:00:00Z"}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(review_data),
            ]
            assert is_copilot_review_complete(42, after_ts="2026-03-18T08:00:00Z") is True


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

    def test_malformed_json_returns_empty(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "not valid json",
            ]
            assert get_unresolved_review_comments(42) == []


class TestIsCopilotPendingReviewer:
    def test_copilot_pending(self):
        response = {"users": [{"login": "copilot-pull-request-reviewer[bot]"}], "teams": []}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(response),
            ]
            assert is_copilot_pending_reviewer(42) is True

    def test_copilot_not_pending(self):
        response = {"users": [{"login": "some-human"}], "teams": []}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(response),
            ]
            assert is_copilot_pending_reviewer(42) is False

    def test_empty_users(self):
        response = {"users": [], "teams": []}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(response),
            ]
            assert is_copilot_pending_reviewer(42) is False

    def test_empty_response(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "",
            ]
            assert is_copilot_pending_reviewer(42) is False

    def test_graphql_login_variant(self):
        """The GraphQL login variant (without [bot] suffix) is also recognised."""
        response = {"users": [{"login": "copilot-pull-request-reviewer"}], "teams": []}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(response),
            ]
            assert is_copilot_pending_reviewer(42) is True

    def test_malformed_json_returns_false(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "not valid json",
            ]
            assert is_copilot_pending_reviewer(42) is False


class TestGetLatestCopilotReviewThreadTs:
    def _graphql_response(self, threads):
        return {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": threads}
                    }
                }
            }
        }

    def test_returns_latest_timestamp(self):
        threads = [
            {"comments": {"nodes": [
                {"author": {"login": "copilot-pull-request-reviewer"},
                 "createdAt": "2026-03-18T15:29:42Z"}
            ]}},
            {"comments": {"nodes": [
                {"author": {"login": "copilot-pull-request-reviewer"},
                 "createdAt": "2026-03-18T17:50:42Z"}
            ]}},
            {"comments": {"nodes": [
                {"author": {"login": "copilot-pull-request-reviewer"},
                 "createdAt": "2026-03-18T16:16:19Z"}
            ]}},
        ]
        resp = self._graphql_response(threads)
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(resp),
            ]
            assert get_latest_copilot_review_thread_ts(42) == "2026-03-18T17:50:42Z"

    def test_returns_none_when_no_copilot_threads(self):
        threads = [
            {"comments": {"nodes": [
                {"author": {"login": "some-human"}, "createdAt": "2026-03-18T10:00:00Z"}
            ]}},
        ]
        resp = self._graphql_response(threads)
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(resp),
            ]
            assert get_latest_copilot_review_thread_ts(42) is None

    def test_returns_none_on_empty_response(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "",
            ]
            assert get_latest_copilot_review_thread_ts(42) is None

    def test_ignores_non_copilot_threads(self):
        threads = [
            {"comments": {"nodes": [
                {"author": {"login": "copilot-pull-request-reviewer"},
                 "createdAt": "2026-03-18T14:00:00Z"}
            ]}},
            {"comments": {"nodes": [
                {"author": {"login": "human-dev"},
                 "createdAt": "2026-03-18T18:00:00Z"}
            ]}},
        ]
        resp = self._graphql_response(threads)
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(resp),
            ]
            # Should return 14:00, not 18:00 (human thread ignored)
            assert get_latest_copilot_review_thread_ts(42) == "2026-03-18T14:00:00Z"

    def test_malformed_json_returns_none(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "not valid json",
            ]
            assert get_latest_copilot_review_thread_ts(42) is None


class TestGetIssue:
    def test_returns_issue(self):
        issue_data = {"title": "Bug in X", "body": "Steps to reproduce..."}
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run(json.dumps(issue_data))):
            result = get_issue(123)
            assert result["title"] == "Bug in X"

    def test_invalid_json_raises_api_error(self):
        from autopilot_loop.github_api import GitHubAPIError
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("not valid json")):
            with pytest.raises(GitHubAPIError, match="Failed to parse issue"):
                get_issue(123)


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


class TestGetFailedChecks:
    def test_returns_failed_non_results(self):
        checks_json = json.dumps([
            {"name": "github (4) / github-4", "state": "FAILURE",
             "link": "https://github.com/o/r/actions/runs/111/job/222"},
            {"name": "github-results", "state": "FAILURE", "link": ""},
            {"name": "build-ubuntu (7) / build-ubuntu-7", "state": "FAILURE",
             "link": "https://github.com/o/r/actions/runs/111/job/333"},
        ])
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run(checks_json)):
            result = get_failed_checks(42)
        # Should exclude *-results
        assert len(result) == 2
        assert result[0]["name"] == "github (4) / github-4"
        assert result[0]["run_id"] == 111
        assert result[0]["job_id"] == 222
        assert result[1]["job_id"] == 333

    def test_empty_when_no_failures(self):
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("")):
            assert get_failed_checks(42) == []

    def test_handles_missing_link(self):
        checks_json = json.dumps([
            {"name": "custom-check", "state": "FAILURE", "link": "https://example.com/other"},
        ])
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run(checks_json)):
            result = get_failed_checks(42)
        assert len(result) == 1
        assert result[0]["run_id"] is None
        assert result[0]["job_id"] is None

    def test_api_error_tries_rest_fallback(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                github_api_module.GitHubAPIError("permission error"),  # gh pr checks
                "abc123",  # pr view for SHA
                "octocat/hello-world",  # get_repo_nwo
                json.dumps([{"name": "build", "conclusion": "failure",
                             "html_url": "https://github.com/o/r/actions/runs/1/job/2"}]),
            ]
            result = get_failed_checks(42)
        assert result is not None
        assert len(result) == 1
        assert result[0]["name"] == "build"

    def test_api_error_and_rest_fallback_fails_returns_none(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                github_api_module.GitHubAPIError("permission error"),  # gh pr checks
                github_api_module.GitHubAPIError("also fails"),  # pr view for SHA
            ]
            result = get_failed_checks(42)
        assert result is None

    def test_malformed_json_tries_rest_fallback(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "not valid json",  # gh pr checks returns garbage
                github_api_module.GitHubAPIError("also fails"),  # rest fallback fails
            ]
            result = get_failed_checks(42)
        assert result is None

    def test_widened_filter_catches_error_state(self):
        # The jq filter is applied by gh CLI, so in tests we just pass
        # through whatever the jq filter returns. This test verifies that
        # non-FAILURE states like ERROR are accepted by _parse_checks.
        checks_json = json.dumps([
            {"name": "check-error", "state": "ERROR",
             "link": "https://github.com/o/r/actions/runs/1/job/2"},
            {"name": "check-startup", "state": "STARTUP_FAILURE",
             "link": "https://github.com/o/r/actions/runs/1/job/3"},
        ])
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run(checks_json)):
            result = get_failed_checks(42)
        assert len(result) == 2
        assert result[0]["name"] == "check-error"
        assert result[1]["name"] == "check-startup"


class TestGetCheckAnnotations:
    def test_returns_failure_annotations_deduped(self):
        ann1 = json.dumps([
            {"annotation_level": "failure", "path": "test/a.rb", "start_line": 40,
             "end_line": 40, "title": "Test failure", "message": "Expected true got false"},
            {"annotation_level": "warning", "path": ".github", "start_line": 1,
             "title": "Deprecation", "message": "Node 20 deprecated"},
            {"annotation_level": "failure", "path": ".github", "start_line": 9999,
             "title": "", "message": "Process completed with exit code 1."},
        ])
        ann2 = json.dumps([
            # Duplicate of the first annotation (same path + line)
            {"annotation_level": "failure", "path": "test/a.rb", "start_line": 40,
             "end_line": 40, "title": "Test failure", "message": "Expected true got false"},
            {"annotation_level": "failure", "path": "test/b.rb", "start_line": 10,
             "end_line": 10, "title": "Another failure", "message": "Missing method"},
        ])
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",  # get_repo_nwo (cached after first)
                ann1,
                ann2,
            ]
            result = get_check_annotations([100, 200])
        #  Expect 2: a.rb:40 (deduped), b.rb:10. Skipped: warning, "Process completed"
        assert len(result) == 2
        assert result[0]["path"] == "test/a.rb"
        assert result[1]["path"] == "test/b.rb"

    def test_strips_ansi(self):
        ann = json.dumps([
            {"annotation_level": "failure", "path": "test/x.rb", "start_line": 1,
             "end_line": 1, "title": "\x1b[31mRed title\x1b[0m",
             "message": "\x1b[49;31mcolored\x1b[0m text"},
        ])
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = ["octocat/hello-world", ann]
            result = get_check_annotations([100])
        assert result[0]["title"] == "Red title"
        assert result[0]["message"] == "colored text"

    def test_empty_when_no_annotations(self):
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = ["octocat/hello-world", ""]
            assert get_check_annotations([100]) == []

    def test_malformed_json_skips_job(self):
        good_ann = json.dumps([
            {"annotation_level": "failure", "path": "test/a.rb", "start_line": 1,
             "end_line": 1, "title": "Fail", "message": "msg"},
        ])
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                "not valid json",  # job 100 returns garbage
                good_ann,  # job 200 succeeds
            ]
            result = get_check_annotations([100, 200])
        assert len(result) == 1
        assert result[0]["path"] == "test/a.rb"


class TestGetCheckStates:
    def test_returns_states_for_selected(self):
        checks_json = json.dumps([
            {"name": "check-a", "state": "SUCCESS"},
            {"name": "check-b", "state": "FAILURE"},
            {"name": "check-c", "state": "PENDING"},
        ])
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run(checks_json)):
            result = get_check_states(42, ["check-a", "check-b", "check-d"])
        assert result["check-a"] == "SUCCESS"
        assert result["check-b"] == "FAILURE"
        assert result["check-d"] == "UNKNOWN"

    def test_api_error_returns_none(self):
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("", returncode=1,
                                          stderr="GraphQL: Resource not accessible")):
            result = get_check_states(42, ["check-a"])
        assert result is None

    def test_malformed_json_returns_none(self):
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("not json")):
            result = get_check_states(42, ["check-a"])
        assert result is None


class TestGetPrDescription:
    def test_returns_title_and_body(self):
        from autopilot_loop.github_api import get_pr_description
        output = json.dumps({"title": "My PR", "body": "PR body text"})
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run(output)):
            result = get_pr_description(42)
        assert result["title"] == "My PR"
        assert result["body"] == "PR body text"

    def test_api_error_raises(self):
        from autopilot_loop.github_api import GitHubAPIError, get_pr_description
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("", returncode=1, stderr="not found")):
            with pytest.raises(GitHubAPIError):
                get_pr_description(999)

    def test_invalid_json_raises_api_error(self):
        from autopilot_loop.github_api import GitHubAPIError, get_pr_description
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("bad json data")):
            with pytest.raises(GitHubAPIError, match="Failed to parse PR description"):
                get_pr_description(42)


class TestUpdatePrDescription:
    def test_calls_gh_pr_edit(self):
        from autopilot_loop.github_api import update_pr_description
        with patch("autopilot_loop.github_api.subprocess.run", return_value=_mock_run("")) as mock_run:
            update_pr_description(42, "new body")
        cmd = mock_run.call_args[0][0]
        assert "pr" in cmd
        assert "edit" in cmd
        assert "42" in cmd
        assert "new body" in cmd

    def test_api_error_raises(self):
        from autopilot_loop.github_api import GitHubAPIError, update_pr_description
        with patch("autopilot_loop.github_api.subprocess.run",
                   return_value=_mock_run("", returncode=1, stderr="error")):
            with pytest.raises(GitHubAPIError):
                update_pr_description(42, "body")


class TestRunGhRetry:
    """Tests for _run_gh retry logic on transient errors."""

    def test_retries_on_rate_limit(self):
        from autopilot_loop.github_api import _run_gh
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if len(calls) < 3:
                return _mock_run("", returncode=1, stderr="API rate limit exceeded")
            return _mock_run("success")

        with patch("autopilot_loop.github_api.subprocess.run", side_effect=fake_run):
            with patch("autopilot_loop.github_api.time.sleep"):
                result = _run_gh(["api", "test"])

        assert result == "success"
        assert len(calls) == 3

    def test_no_retry_on_permanent_error(self):
        from autopilot_loop.github_api import GitHubAPIError, _run_gh
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            return _mock_run("", returncode=1, stderr="Not Found (HTTP 404)")

        with patch("autopilot_loop.github_api.subprocess.run", side_effect=fake_run):
            with pytest.raises(GitHubAPIError):
                _run_gh(["api", "test"])

        assert len(calls) == 1

    def test_retries_on_server_error(self):
        from autopilot_loop.github_api import _run_gh
        calls = []

        def fake_run(cmd, **kw):
            calls.append(cmd)
            if len(calls) < 2:
                return _mock_run("", returncode=1, stderr="502 Bad Gateway")
            return _mock_run("ok")

        with patch("autopilot_loop.github_api.subprocess.run", side_effect=fake_run):
            with patch("autopilot_loop.github_api.time.sleep"):
                result = _run_gh(["api", "test"])

        assert result == "ok"
        assert len(calls) == 2

    def test_gives_up_after_max_retries(self):
        from autopilot_loop.github_api import GitHubAPIError, _run_gh

        def fake_run(cmd, **kw):
            return _mock_run("", returncode=1, stderr="503 Service Unavailable")

        with patch("autopilot_loop.github_api.subprocess.run", side_effect=fake_run):
            with patch("autopilot_loop.github_api.time.sleep"):
                with pytest.raises(GitHubAPIError):
                    _run_gh(["api", "test"])

    def test_check_false_no_raise(self):
        from autopilot_loop.github_api import _run_gh

        def fake_run(cmd, **kw):
            return _mock_run("", returncode=1, stderr="Not Found")

        with patch("autopilot_loop.github_api.subprocess.run", side_effect=fake_run):
            result = _run_gh(["api", "test"], check=False)

        assert result == ""


class TestGraphQLErrorRaising:
    """Tests for GraphQL error raising in review comment functions."""

    def test_unresolved_comments_raises_on_graphql_error_no_data(self):
        from autopilot_loop.github_api import GitHubAPIError
        error_response = {"errors": [{"message": "auth required"}]}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(error_response),
            ]
            with pytest.raises(GitHubAPIError, match="auth required"):
                get_unresolved_review_comments(42)

    def test_unresolved_comments_partial_error_returns_data(self):
        partial_response = {
            "errors": [{"message": "some warning"}],
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": []}
                    }
                }
            },
        }
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(partial_response),
            ]
            result = get_unresolved_review_comments(42)
            assert result == []

    def test_latest_thread_ts_raises_on_graphql_error_no_data(self):
        from autopilot_loop.github_api import GitHubAPIError
        error_response = {"errors": [{"message": "rate limited"}]}
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(error_response),
            ]
            with pytest.raises(GitHubAPIError, match="rate limited"):
                get_latest_copilot_review_thread_ts(42)

    def test_latest_thread_ts_partial_error_returns_data(self):
        partial_response = {
            "errors": [{"message": "some warning"}],
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {"nodes": []}
                    }
                }
            },
        }
        with patch("autopilot_loop.github_api._run_gh") as mock_gh:
            mock_gh.side_effect = [
                "octocat/hello-world",
                json.dumps(partial_response),
            ]
            result = get_latest_copilot_review_thread_ts(42)
            assert result is None
