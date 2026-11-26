"""Tests for GitHubClient."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from friday_drop.github_client import GitHubClient


def test_find_open_issue_ignores_pull_requests() -> None:
    pr_issue = SimpleNamespace(number=1, pull_request=object())
    real_issue = SimpleNamespace(number=2, pull_request=None)

    mock_repo = MagicMock()
    mock_repo.get_issues.return_value = [pr_issue, real_issue]
    mock_gh = MagicMock()
    mock_gh.get_repo.return_value = mock_repo

    client = GitHubClient(token="ghp_test", repository="owner/repo", client=mock_gh)
    found = client.find_open_issue(label="friday-drop")

    assert found is real_issue


def test_get_issue_comments_skips_empty_bodies() -> None:
    mock_issue = MagicMock()
    mock_issue.get_comments.return_value = [
        SimpleNamespace(body="  "),
        SimpleNamespace(body="Daft Punk"),
        SimpleNamespace(body=None),
        SimpleNamespace(body="  Radiohead \n"),
    ]
    mock_gh = MagicMock()

    client = GitHubClient(token="ghp_test", repository="owner/repo", client=mock_gh)
    comments = client.get_issue_comments(mock_issue)

    assert comments == ["Daft Punk", "Radiohead"]


def test_close_issue_no_artists_comments_and_closes() -> None:
    mock_issue = MagicMock()
    mock_gh = MagicMock()
    client = GitHubClient(token="ghp_test", repository="owner/repo", client=mock_gh)

    client.close_issue_no_artists(mock_issue)

    mock_issue.create_comment.assert_called_once()
    comment_text = mock_issue.create_comment.call_args[0][0]
    assert "Артисты не указаны" in comment_text
    mock_issue.edit.assert_called_once_with(state="closed")
