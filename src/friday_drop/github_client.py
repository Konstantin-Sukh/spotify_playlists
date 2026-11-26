"""GitHub client for reading artist requests and reporting playlist results."""

from __future__ import annotations

from github import Auth, Github
from github.Issue import Issue


class GitHubClient:
    """Wrapper around PyGithub for Friday Drop issue operations."""

    def __init__(
        self,
        token: str,
        repository: str,
        client: Github | None = None,
    ) -> None:
        self._client = client or Github(auth=Auth.Token(token))
        self._repo = self._client.get_repo(repository)

    def find_open_issue(self, label: str = "friday-drop") -> Issue | None:
        """Return the most recent open issue with the given label, or None."""
        issues = self._repo.get_issues(state="open", labels=[label], sort="created", direction="desc")
        for issue in issues:
            if issue.pull_request is None:
                return issue
        return None

    def get_issue_comments(self, issue: Issue) -> list[str]:
        """Return non-empty comment bodies from the issue."""
        return [
            comment.body.strip()
            for comment in issue.get_comments()
            if comment.body and comment.body.strip()
        ]

    def close_issue_no_artists(self, issue: Issue) -> None:
        """Comment that no artists were provided and close the issue."""
        issue.create_comment(
            "Артисты не указаны. Пропускаем генерацию плейлиста на этой неделе."
        )
        issue.edit(state="closed")

    def close_issue_with_report(self, issue: Issue, report: str) -> None:
        """Post the final report comment and close the issue."""
        issue.create_comment(report)
        issue.edit(state="closed")
