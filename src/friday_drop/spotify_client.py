"""Spotify client for searching artists, fetching recent releases, and creating playlists."""

from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta, timezone

import spotipy
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.exceptions import SpotifyException
from spotipy.oauth2 import SpotifyOAuth

from friday_drop.models import TrackCandidate

SCOPES = "playlist-modify-public playlist-modify-private user-library-read"
PLAYLIST_BATCH_SIZE = 100


class SpotifyClient:
    """Wrapper around spotipy for Friday Drop release collection and playlist creation."""

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        redirect_uri: str = "http://127.0.0.1:8888/callback",
        sp: spotipy.Spotify | None = None,
    ) -> None:
        if sp is not None:
            self._sp = sp
        else:
            cache_handler = MemoryCacheHandler()
            oauth = SpotifyOAuth(
                client_id=client_id,
                client_secret=client_secret,
                redirect_uri=redirect_uri,
                scope=SCOPES,
                cache_handler=cache_handler,
            )
            oauth.refresh_access_token(refresh_token)
            self._sp = spotipy.Spotify(
                auth_manager=oauth,
                requests_timeout=15,
                status_forcelist=(500, 502, 503, 504),
                retries=2,
                status_retries=2,
            )

    def search_artist(self, name: str) -> tuple[str, str] | None:
        """Search Spotify for an artist and return (artist_id, canonical_name) or None."""
        response = self._sp.search(q=f"artist:{name}", type="artist", limit=5)
        items = response.get("artists", {}).get("items", [])
        if not items:
            # Fallback to broader search if strict artist: filter returns nothing
            response = self._sp.search(q=name, type="artist", limit=5)
            items = response.get("artists", {}).get("items", [])

        if not items:
            return None

        target_lower = name.strip().lower()
        for item in items:
            if item["name"].strip().lower() == target_lower:
                return item["id"], item["name"]

        best = items[0]
        return best["id"], best["name"]

    def get_liked_artists(self, min_tracks: int = 1) -> list[tuple[str, str]]:
        """Return (artist_id, name) for artists with at least min_tracks in Liked Songs."""
        if min_tracks <= 0:
            return []

        counts: Counter[str] = Counter()
        names: dict[str, str] = {}
        offset = 0
        limit = 50

        while True:
            try:
                page = self._sp.current_user_saved_tracks(limit=limit, offset=offset)
            except SpotifyException as exc:
                if exc.http_status == 400 and limit > 10:
                    limit = 10
                    continue
                raise

            items = page.get("items", [])
            if not items:
                break

            for item in items:
                track = item.get("track") or {}
                seen_in_track: set[str] = set()
                for artist in track.get("artists", []):
                    artist_id = artist.get("id")
                    artist_name = artist.get("name")
                    if not artist_id or not artist_name or artist_id in seen_in_track:
                        continue
                    seen_in_track.add(artist_id)
                    counts[artist_id] += 1
                    names.setdefault(artist_id, artist_name)

            if page.get("next") is None:
                break
            offset += limit

        return [
            (artist_id, names[artist_id])
            for artist_id, count in counts.most_common()
            if count >= min_tracks
        ]

    def get_recent_tracks(
        self,
        artist_id: str,
        artist_name: str,
        lookback_days: int = 6,
        now_utc: datetime | None = None,
        max_tracks_per_release: int = 4,
    ) -> list[TrackCandidate]:
        """Collect tracks from artist's releases within lookback_days (UTC)."""
        current_utc = now_utc or datetime.now(timezone.utc)
        cutoff_date = current_utc.date() - timedelta(days=lookback_days)

        tracks: list[TrackCandidate] = []
        recent_albums = self._fetch_recent_albums(artist_id, cutoff_date)

        for album in recent_albums:
            total_tracks = album.get("total_tracks")
            if total_tracks is not None and total_tracks > max_tracks_per_release:
                continue

            album_id = album["id"]
            album_name = album["name"]
            release_date = album["release_date"]

            album_tracks = self._fetch_album_tracks(album_id)
            if len(album_tracks) > max_tracks_per_release:
                continue

            for track in album_tracks:
                tracks.append(
                    TrackCandidate(
                        uri=track["uri"],
                        name=track["name"],
                        artist_name=artist_name,
                        album_name=album_name,
                        release_date=release_date,
                    )
                )

        return tracks

    def create_playlist(
        self,
        name: str,
        track_uris: list[str],
        public: bool = False,
        description: str = "Свежие релизы за последние 6 дней, собранные агентом Friday Drop.",
    ) -> str:
        """Create a Spotify playlist and add tracks in batches. Returns the playlist URL."""
        playlist = self._sp.current_user_playlist_create(
            name=name,
            public=public,
            description=description,
        )
        playlist_id = playlist["id"]

        for idx in range(0, len(track_uris), PLAYLIST_BATCH_SIZE):
            batch = track_uris[idx : idx + PLAYLIST_BATCH_SIZE]
            self._sp.playlist_add_items(playlist_id, batch)

        return playlist["external_urls"]["spotify"]

    def _fetch_recent_albums(self, artist_id: str, cutoff_date: date) -> list[dict]:
        """Fetch artist albums/singles released on or after cutoff_date."""
        recent: list[dict] = []
        offset = 0
        limit = 10
        max_pages = 2

        for _ in range(max_pages):
            page = self._sp.artist_albums(
                artist_id,
                include_groups="album,single",
                limit=limit,
                offset=offset,
            )
            items = page.get("items", [])
            if not items:
                break

            for album in items:
                release_date = _parse_release_date(
                    album.get("release_date", ""),
                    album.get("release_date_precision", ""),
                )
                if release_date is not None and release_date >= cutoff_date:
                    recent.append(album)

            if page.get("next") is None:
                break
            offset += limit

        return recent

    def _fetch_album_tracks(self, album_id: str) -> list[dict]:
        """Fetch all tracks for a given album ID."""
        tracks: list[dict] = []
        offset = 0
        limit = 50

        while True:
            page = self._sp.album_tracks(album_id, limit=limit, offset=offset)
            items = page.get("items", [])
            if not items:
                break
            tracks.extend(items)
            if page.get("next") is None:
                break
            offset += limit

        return tracks


def _parse_release_date(raw_date: str, precision: str) -> date | None:
    """Parse Spotify release_date when full day precision is available."""
    if precision != "day" or not raw_date:
        return None
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        return None
