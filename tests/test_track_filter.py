"""Tests for local track filtering and title normalization."""

from friday_drop.models import TrackCandidate
from friday_drop.track_filter import filter_tracks, is_unwanted_version, normalize_title


def _make_track(uri: str, name: str, artist: str = "Artist A") -> TrackCandidate:
    return TrackCandidate(
        uri=uri,
        name=name,
        artist_name=artist,
        album_name="Test Album",
        release_date="2026-11-20",
    )


def test_is_unwanted_version_detects_blocked_keywords() -> None:
    assert is_unwanted_version("Midnight City (Sped Up)")
    assert is_unwanted_version("Midnight City - Slowed + Reverb")
    assert is_unwanted_version("Midnight City (Slowed)")
    assert is_unwanted_version("Midnight City [Reverb]")
    assert is_unwanted_version("Midnight City (Instrumental)")
    assert is_unwanted_version("Midnight City - Acapella")
    assert is_unwanted_version("Midnight City (A Cappella)")

    assert not is_unwanted_version("Midnight City")
    assert not is_unwanted_version("Midnight City (feat. Guest)")


def test_normalize_title_strips_feat_and_punctuation() -> None:
    assert normalize_title("Hello, World! (feat. Someone)") == "hello world"
    assert normalize_title("  Hello   World [ft. Other] ") == "hello world"


def test_filter_tracks_removes_unwanted_and_duplicates() -> None:
    tracks = [
        _make_track("spotify:track:1", "Neon Lights"),
        _make_track("spotify:track:2", "Neon Lights (Sped Up)"),
        _make_track("spotify:track:3", "Neon Lights (feat. Guest)"),
        _make_track("spotify:track:1", "Neon Lights"),
        _make_track("spotify:track:4", "Solar Flare - Slowed & Reverb"),
        _make_track("spotify:track:5", "Solar Flare (Instrumental)"),
        _make_track("spotify:track:6", "Solar Flare"),
        _make_track("spotify:track:7", "Solar Flare (Acapella)"),
    ]

    result = filter_tracks(tracks)

    assert [t.uri for t in result] == ["spotify:track:1", "spotify:track:6"]
