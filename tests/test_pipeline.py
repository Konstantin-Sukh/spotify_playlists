"""Integration-style unit tests for the Friday Drop pipeline."""

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from friday_drop.config import Settings
from friday_drop.models import PlaylistSummary, TrackCandidate
from friday_drop.pipeline import (
    format_report,
    load_liked_artists,
    normalize_artist_name,
    run_pipeline,
    send_ntfy_notification,
)


def _make_settings() -> Settings:
    return Settings(
        github_token="ghp_test",
        github_repository="owner/repo",
        gemini_api_key="gemini_test",
        spotify_client_id="sp_id",
        spotify_client_secret="sp_secret",
        spotify_refresh_token="sp_refresh",
        liked_cache_ttl_hours=0,
    )


def test_normalize_artist_name_handles_whitespace_case_and_yo() -> None:
    assert normalize_artist_name("  Ёлка  ") == "елка"
    assert normalize_artist_name("Fred   again..") == "fred again.."


def test_pipeline_exits_cleanly_when_no_open_issue() -> None:
    gh = MagicMock()
    gh.find_open_issue.return_value = None
    ai = MagicMock()
    sp = MagicMock()

    result = run_pipeline(_make_settings(), github_client=gh, ai_parser=ai, spotify_client=sp)

    assert result is None
    ai.parse_comments.assert_not_called()
    sp.get_liked_artists.assert_not_called()


def test_pipeline_closes_empty_issue_without_calling_spotify() -> None:
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
    sp.get_liked_artists.assert_not_called()


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
    sp.get_liked_artists.assert_not_called()


def test_pipeline_intersects_issue_singles_with_liked_artists() -> None:
    issue = SimpleNamespace(number=44)
    gh = MagicMock()
    gh.find_open_issue.return_value = issue
    gh.get_issue_comments.return_value = ["Fred again.. - Ten\nRandom Artist - Song"]

    ai = MagicMock()
    ai.parse_comments.return_value = ["fred again..", "Random Artist"]

    sp = MagicMock()
    sp.get_liked_artists.return_value = [
        ("artist_fred", "Fred again.."),
        ("artist_muse", "Muse"),  # Not in Thursday Issue -> must NOT be queried!
    ]
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
    assert result.skipped_not_liked_count == 1

    # Only Fred again.. should be queried (Muse wasn't in Issue; Random Artist wasn't liked)
    sp.get_recent_tracks.assert_called_once_with(
        artist_id="artist_fred",
        artist_name="Fred again..",
        lookback_days=6,
        now_utc=now_utc,
        max_tracks_per_release=4,
    )
    sp.search_artist.assert_not_called()
    sp.create_playlist.assert_called_once_with(
        name="Friday Drop — 27.11.2026",
        track_uris=["spotify:track:1"],
        public=False,
    )
    gh.close_issue_with_report.assert_called_once()
    report = gh.close_issue_with_report.call_args[0][1]
    assert "https://open.spotify.com/playlist/abc" in report
    assert "Нет в «Любимых треках» (пропущено):** 1" in report


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


def test_load_liked_artists_uses_fresh_cache_and_refreshes_when_expired(tmp_path: Path) -> None:
    cache_file = tmp_path / "liked_artists.json"
    sp = MagicMock()
    sp.get_liked_artists.return_value = [("a1", "Husky"), ("a2", "Muse")]

    t0 = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
    first = load_liked_artists(sp, min_tracks=1, cache_path=str(cache_file), cache_ttl_hours=72, now_utc=t0)
    assert first == [("a1", "Husky"), ("a2", "Muse")]
    assert sp.get_liked_artists.call_count == 1
    assert cache_file.exists()

    # 10 hours later -> cache hit, no API call
    t1 = datetime(2026, 11, 27, 19, 0, tzinfo=timezone.utc)
    second = load_liked_artists(sp, min_tracks=1, cache_path=str(cache_file), cache_ttl_hours=72, now_utc=t1)
    assert second == [("a1", "Husky"), ("a2", "Muse")]
    assert sp.get_liked_artists.call_count == 1

    # 80 hours later -> expired, refreshes from Spotify
    t2 = datetime(2026, 11, 30, 17, 0, tzinfo=timezone.utc)
    third = load_liked_artists(sp, min_tracks=1, cache_path=str(cache_file), cache_ttl_hours=72, now_utc=t2)
    assert third == [("a1", "Husky"), ("a2", "Muse")]
    assert sp.get_liked_artists.call_count == 2
