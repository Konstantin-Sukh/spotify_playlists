"""Tests for SpotifyClient."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from friday_drop.spotify_client import SpotifyClient


def test_search_artist_prefers_exact_match() -> None:
    mock_sp = MagicMock()
    mock_sp.search.return_value = {
        "artists": {
            "items": [
                {"id": "1", "name": "Muse Cover Band"},
                {"id": "2", "name": "Muse"},
            ]
        }
    }

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    assert client.search_artist("muse") == ("2", "Muse")


def test_get_recent_tracks_filters_by_six_days_utc() -> None:
    mock_sp = MagicMock()
    mock_sp.artist_albums.return_value = {
        "items": [
            {
                "id": "alb_new",
                "name": "Fresh Single",
                "release_date": "2026-11-21",
                "release_date_precision": "day",
            },
            {
                "id": "alb_old",
                "name": "Old Album",
                "release_date": "2026-11-10",
                "release_date_precision": "day",
            },
            {
                "id": "alb_year",
                "name": "Year Only",
                "release_date": "2026",
                "release_date_precision": "year",
            },
        ],
        "next": None,
    }
    mock_sp.album_tracks.return_value = {
        "items": [{"uri": "spotify:track:fresh1", "name": "Fresh Track"}],
        "next": None,
    }

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    now_utc = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)
    tracks = client.get_recent_tracks("artist_1", "Muse", lookback_days=6, now_utc=now_utc)

    assert len(tracks) == 1
    assert tracks[0].uri == "spotify:track:fresh1"
    assert tracks[0].album_name == "Fresh Single"
    mock_sp.album_tracks.assert_called_once_with("alb_new", limit=50, offset=0)


def test_create_playlist_adds_tracks_in_batches() -> None:
    mock_sp = MagicMock()
    mock_sp.current_user.return_value = {"id": "user_123"}
    mock_sp.user_playlist_create.return_value = {
        "id": "pl_123",
        "external_urls": {"spotify": "https://open.spotify.com/playlist/pl_123"},
    }

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    uris = [f"spotify:track:{i}" for i in range(105)]
    url = client.create_playlist("Friday Drop — 26.11.2026", uris, public=False)

    assert url == "https://open.spotify.com/playlist/pl_123"
    assert mock_sp.playlist_add_items.call_count == 2
    assert len(mock_sp.playlist_add_items.call_args_list[0][0][1]) == 100
    assert len(mock_sp.playlist_add_items.call_args_list[1][0][1]) == 5
