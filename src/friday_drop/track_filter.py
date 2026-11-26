"""Local Python track filter for removing unwanted versions and duplicates."""

from __future__ import annotations

import re

from friday_drop.models import TrackCandidate

UNWANTED_VERSION_PATTERN = re.compile(
    r"\b(?:sped\s*up|speed\s*up|slowed(?:\s*(?:\+|&|and)?\s*reverb)?|"
    r"reverb|instrumental|a\s*cappella|acapella)\b",
    re.IGNORECASE,
)

_FEAT_PATTERN = re.compile(r"\s*[\(\[](?:feat\.?|ft\.?)\s+[^\)\]]+[\)\]]", re.IGNORECASE)
_NON_ALNUM_PATTERN = re.compile(r"[^\w\s]", re.UNICODE)
_WHITESPACE_PATTERN = re.compile(r"\s+")


def is_unwanted_version(track_name: str) -> bool:
    """Return True if the track title indicates an unwanted edit/version."""
    return bool(UNWANTED_VERSION_PATTERN.search(track_name))


def normalize_title(title: str) -> str:
    """Normalize a track title for duplicate detection."""
    cleaned = _FEAT_PATTERN.sub("", title.lower())
    cleaned = _NON_ALNUM_PATTERN.sub(" ", cleaned)
    return _WHITESPACE_PATTERN.sub(" ", cleaned).strip()


def filter_tracks(tracks: list[TrackCandidate]) -> list[TrackCandidate]:
    """Filter out unwanted track versions and duplicates while preserving order."""
    filtered: list[TrackCandidate] = []
    seen_uris: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()

    for track in tracks:
        if is_unwanted_version(track.name):
            continue
        if track.uri in seen_uris:
            continue

        dedup_key = (normalize_title(track.name), track.artist_name.strip().lower())
        if dedup_key in seen_keys:
            continue

        seen_uris.add(track.uri)
        seen_keys.add(dedup_key)
        filtered.append(track)

    return filtered
