"""Tests for SpotifyClient."""

from datetime import datetime, timezone
from unittest.mock import MagicMock

from spotipy.exceptions import SpotifyException

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
    mock_sp.current_user_playlist_create.return_value = {
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


def test_get_liked_artists_filters_by_min_tracks_and_handles_limit_fallback() -> None:
    mock_sp = MagicMock()
    err_400 = SpotifyException(http_status=400, code=-1, msg="Invalid limit")
    page = {
        "items": [
            {"track": {"artists": [{"id": "a1", "name": "Muse"}]}},
            {"track": {"artists": [{"id": "a1", "name": "Muse"}, {"id": "a2", "name": "Guest"}]}},
            {"track": {"artists": [{"id": "a3", "name": "Daft Punk"}]}},
            {"track": {"artists": [{"id": "a3", "name": "Daft Punk"}]}},
            {"track": {"artists": [{"id": "a3", "name": "Daft Punk"}]}},
        ],
        "next": None,
    }
    mock_sp.current_user_saved_tracks.side_effect = [err_400, page]

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    liked = client.get_liked_artists(min_tracks=2)

    assert liked == [("a3", "Daft Punk"), ("a1", "Muse")]
    assert mock_sp.current_user_saved_tracks.call_args_list[0].kwargs == {"limit": 50, "offset": 0}
    assert mock_sp.current_user_saved_tracks.call_args_list[1].kwargs == {"limit": 10, "offset": 0}


def test_get_recent_tracks_skips_releases_with_more_than_four_tracks() -> None:
    mock_sp = MagicMock()
    mock_sp.artist_albums.return_value = {
        "items": [
            {
                "id": "alb_big",
                "name": "Full LP",
                "release_date": "2026-11-24",
                "release_date_precision": "day",
                "total_tracks": 12,
            },
            {
                "id": "alb_ep",
                "name": "Small EP",
                "release_date": "2026-11-24",
                "release_date_precision": "day",
                "total_tracks": 4,
            },
        ],
        "next": None,
    }
    mock_sp.album_tracks.return_value = {
        "items": [{"uri": f"spotify:track:{i}", "name": f"Track {i}"} for i in range(4)],
        "next": None,
    }

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    now_utc = datetime(2026, 11, 26, 9, 0, tzinfo=timezone.utc)
    tracks = client.get_recent_tracks(
        "artist_1", "Muse", lookback_days=6, now_utc=now_utc, max_tracks_per_release=4
    )

    assert len(tracks) == 4
    mock_sp.album_tracks.assert_called_once_with("alb_ep", limit=50, offset=0)


def test_get_recent_tracks_queries_singles_albums_and_appears_on() -> None:
    mock_sp = MagicMock()
    mock_sp.artist_albums.side_effect = [
        {
            "items": [
                {
                    "id": "single_1",
                    "name": "Божья коровка",
                    "release_date": "2026-11-27",
                    "release_date_precision": "day",
                    "total_tracks": 1,
                }
            ],
            "next": None,
        },
        {
            "items": [
                {
                    "id": "album_1",
                    "name": "Партизан",
                    "release_date": "2025-11-28",
                    "release_date_precision": "day",
                    "total_tracks": 15,
                }
            ],
            "next": None,
        },
        {
            "items": [
                {
                    "id": "comp_1",
                    "album_type": "compilation",
                    "name": "Best Hits 2026",
                    "release_date": "2026-11-27",
                    "release_date_precision": "day",
                    "total_tracks": 2,
                },
                {
                    "id": "feat_single",
                    "album_type": "single",
                    "name": "Other Maxi",
                    "release_date": "2026-11-26",
                    "release_date_precision": "day",
                    "total_tracks": 2,
                },
            ],
            "next": None,
        },
    ]
    mock_sp.album_tracks.side_effect = [
        {
            "items": [
                {
                    "uri": "spotify:track:bk",
                    "name": "Божья коровка",
                    "artists": [{"id": "husky_id"}],
                }
            ],
            "next": None,
        },
        {
            "items": [
                {
                    "uri": "spotify:track:other_only",
                    "name": "Solo Track",
                    "artists": [{"id": "other_id"}],
                },
                {
                    "uri": "spotify:track:feat_track",
                    "name": "Joint Track",
                    "artists": [{"id": "other_id"}, {"id": "husky_id"}],
                },
            ],
            "next": None,
        },
    ]

    client = SpotifyClient(
        client_id="id",
        client_secret="secret",
        refresh_token="refresh",
        sp=mock_sp,
    )
    now_utc = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)
    tracks = client.get_recent_tracks("husky_id", "Husky", lookback_days=6, now_utc=now_utc)

    assert [t.name for t in tracks] == ["Божья коровка", "Joint Track"]
    groups = [c.kwargs["include_groups"] for c in mock_sp.artist_albums.call_args_list]
    assert groups == ["single", "album", "appears_on"]
