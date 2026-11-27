"""Application configuration loaded via pydantic-settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings for the Friday Drop agent."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    github_token: str
    github_repository: str
    gemini_api_key: str

    spotify_client_id: str
    spotify_client_secret: str
    spotify_refresh_token: str
    spotify_redirect_uri: str = "http://127.0.0.1:8888/callback"

    issue_label: str = "friday-drop"
    lookback_days: int = 6
    min_liked_tracks: int = 1
    max_tracks_per_release: int = 4
    liked_cache_path: str = ".cache/liked_artists.json"
    liked_cache_ttl_hours: int = 72
    playlist_public: bool = False
    ntfy_topic: str = ""
    gemini_model: str = "gemini-3.5-flash-lite"
    gemini_fallback_models: list[str] = Field(
        default_factory=lambda: [
            "gemini-3.1-flash-lite",
            "gemini-flash-lite-latest",
            "gemini-3.8-flash",
            "gemini-3.7-flash",
        ]
    )
