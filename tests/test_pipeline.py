"""Integration-style unit tests for the Friday Drop pipeline."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from friday_drop.config import Settings
from friday_drop.models import PlaylistSummary, TrackCandidate
from friday_drop.pipeline import format_report, run_pipeline, send_ntfy_notification


def _make_settings() -> Settings:
    return Settings(
        github_token="ghp_test",
        github_repository="owner/repo",
        gemini_api_key="gemini_test",
        spotify_client_id="sp_id",
        spotify_client_secret="sp_secret",
        spotify_refresh_token="sp_refresh",
    )


def test_pipeline_exits_cleanly_when_no_open_issue_and_no_liked_artists() -> None:
    gh = MagicMock()
    gh.find_open_issue.return_value = None
    ai = MagicMock()
    sp = MagicMock()
    sp.get_liked_artists.return_value = []

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    ai.parse_comments.assert_not_called()
    sp.search_artist.assert_not_called()


def test_pipeline_closes_empty_issue_when_no_liked_artists_either() -> None:
    issue = SimpleNamespace(number=42)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = []
    ai = MagicMock()
    sp = MagicMock()
    sp.get_liked_artists.return_value = []

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    gh.close_issue_no_artists.assert_called_once_with(issue)
    ai.parse_comments.assert_not_called()
    sp.create_playlist.assert_not_called()


def test_pipeline_runs_with_liked_artists_even_when_issue_has_no_comments() -> None:
    issue = SimpleNamespace(number=43)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = []
    ai = MagicMock()
    sp = MagicMock()
    sp.get_liked_artists.return_value = [("artist_liked", "Muse")]
    sp.get_recent_tracks.return_value = [
        TrackCandidate(
            uri="spotify:track:m1",
            name="Starlight",
            artist_name="Muse",
            album_name="New Single",
            release_date="2026-11-24",
        )
    ]
    sp.create_playlist.return_value = "https://open.spotify.com/playlist/liked1"

    now_utc = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    result = run_pipeline(
        _make_settings(),
        github_client=gh,
        ai_parser=ai,
        spotify_client=sp,
        now_utc=now_utc,
    )

    assert result is not None
    assert result.track_count == 1
    assert result.matched_artists == ["Muse"]
    ai.parse_comments.assert_not_called()
    gh.close_issue_with_report.assert_called_once()


def test_pipeline_happy_path_combines_liked_and_issue_artists_without_duplicates() -> None:
    issue = SimpleNamespace(number=44)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = ["Muse, Fred again.., UnknownArtist"]

    ai = MagicMock()
    ai.parse_comments.return_value = ["Muse", "Fred again..", "UnknownArtist"]

    sp = MagicMock()
    sp.get_liked_artists.return_value = [("artist_muse", "Muse")]
    sp.search_artist.side_effect = [
        ("artist_muse", "Muse"),
        ("artist_1", "Fred again.."),
        None,
    ]
    sp.get_recent_tracks.side_effect = [
        [],  # Muse has no new releases
        [
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
        ],
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
    assert result.no_releases_artists == ["Muse"]
    assert result.not_found_artists == ["UnknownArtist"]
    assert sp.get_recent_tracks.call_count == 2

    sp.create_playlist.assert_called_once_with(
        name="Friday Drop — 27.11.2026",
        track_uris=["spotify:track:1"],
        public=False,
    )
    gh.close_issue_with_report.assert_called_once()
    report = gh.close_issue_with_report.call_args[0][1]
    assert "https://open.spotify.com/playlist/abc" in report
    assert "UnknownArtist" in report


def test_format_report_summarizes_many_no_release_artists_as_count() -> None:
    summary = PlaylistSummary(
        playlist_url="https://open.spotify.com/playlist/xyz",
        playlist_name="Friday Drop — 27.11.2026",
        track_count=3,
        matched_artists=["Muse"],
        no_releases_artists=[f"Artist {i}" for i in range(15)],
    )
    report = format_report(summary, lookback_days=6)
    assert "15 артистов" in report


def test_send_ntfy_notification_sends_click_header_with_playlist_url() -> None:
    summary = PlaylistSummary(
        playlist_url="https://open.spotify.com/playlist/xyz",
        playlist_name="Friday Drop — 27.11.2026",
        track_count=7,
    )
    with patch("friday_drop.pipeline.urllib.request.urlopen") as mock_urlopen:
        send_ntfy_notification("my-secret-topic", summary)

    mock_urlopen.assert_called_once()
    req = mock_urlopen.call_args[0][0]
    assert req.full_url == "https://ntfy.sh/my-secret-topic"
    assert req.headers["Click"] == "https://open.spotify.com/playlist/xyz"
    assert "7 треков" in req.data.decode("utf-8")
