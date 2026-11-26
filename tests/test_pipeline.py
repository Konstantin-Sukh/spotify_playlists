"""Integration-style unit tests for the Friday Drop pipeline."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

from friday_drop.config import Settings
from friday_drop.models import TrackCandidate
from friday_drop.pipeline import run_pipeline


def _make_settings() -> Settings:
    return Settings(
        github_token="ghp_test",
        github_repository="owner/repo",
        gemini_api_key="gemini_test",
        spotify_client_id="sp_id",
        spotify_client_secret="sp_secret",
        spotify_refresh_token="sp_refresh",
    )


def test_pipeline_exits_cleanly_when_no_open_issue() -> None:
    gh = MagicMock()
    gh.find_open_issue.return_value = None
    ai = MagicMock()
    sp = MagicMock()

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    ai.parse_comments.assert_not_called()
    sp.search_artist.assert_not_called()


def test_pipeline_closes_empty_issue_without_error() -> None:
    issue = SimpleNamespace(number=42)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = []
    ai = MagicMock()
    sp = MagicMock()

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    gh.close_issue_no_artists.assert_called_once_with(issue)
    ai.parse_comments.assert_not_called()
    sp.create_playlist.assert_not_called()


def test_pipeline_closes_issue_when_ai_finds_no_artists() -> None:
    issue = SimpleNamespace(number=43)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = ["просто привет всем"]
    ai = MagicMock()
    ai.parse_comments.return_value = []
    sp = MagicMock()

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    gh.close_issue_no_artists.assert_called_once_with(issue)
    sp.create_playlist.assert_not_called()


def test_pipeline_happy_path_creates_playlist_and_reports() -> None:
    issue = SimpleNamespace(number=44)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = ["Fred again.., UnknownArtist"]

    ai = MagicMock()
    ai.parse_comments.return_value = ["Fred again..", "UnknownArtist"]

    sp = MagicMock()
    sp.search_artist.side_effect = [("artist_1", "Fred again.."), None]
    sp.get_recent_tracks.return_value = [
        TrackCandidate(
            uri="spotify:track:1",
            name="Ten",
            artist_name="Fred again..",
            album_name="Ten Days",
            release_date="2026-11-24",
        ),
        TrackCandidate(
            uri="spotify:track:2",
            name="Ten (Sped Up)",
            artist_name="Fred again..",
            album_name="Ten Days",
            release_date="2026-11-24",
        ),
    ]
    sp.create_playlist.return_value = "https://open.spotify.com/playlist/abc"

    now_utc = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    result = run_pipeline(
        _make_settings(),
        github_client=gh,
        ai_parser=ai,
        spotify_client=sp,
        now_utc=now_utc,
    )

    assert result is not None
    assert result.playlist_name == "Friday Drop — 27.11.2026"
    assert result.playlist_url == "https://open.spotify.com/playlist/abc"
    assert result.track_count == 1
    assert result.matched_artists == ["Fred again.."]
    assert result.not_found_artists == ["UnknownArtist"]

    sp.create_playlist.assert_called_once_with(
        name="Friday Drop — 27.11.2026",
        track_uris=["spotify:track:1"],
        public=False,
    )
    gh.close_issue_with_report.assert_called_once()
    report = gh.close_issue_with_report.call_args[0][1]
    assert "https://open.spotify.com/playlist/abc" in report
    assert "UnknownArtist" in report
