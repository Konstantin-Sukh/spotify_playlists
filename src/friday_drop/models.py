"""Domain models and schemas for Friday Drop."""

from pydantic import BaseModel, Field


class ParsedArtists(BaseModel):
    """Structured output schema returned by Gemini for artist extraction."""

    artists: list[str] = Field(
        default_factory=list,
        description="Clean, deduplicated list of music artist names extracted from comments.",
    )


class TrackCandidate(BaseModel):
    """Track metadata collected locally from Spotify for filtering and playlist creation."""

    uri: str
    name: str
    artist_name: str
    album_name: str
    release_date: str


class PlaylistSummary(BaseModel):
    """Summary of a completed Friday Drop pipeline run."""

    playlist_url: str | None = None
    playlist_name: str | None = None
    track_count: int = 0
    matched_artists: list[str] = Field(default_factory=list)
    no_releases_artists: list[str] = Field(default_factory=list)
    not_found_artists: list[str] = Field(default_factory=list)
    skipped_not_liked_count: int = 0
