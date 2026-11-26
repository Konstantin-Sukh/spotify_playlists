"""Application configuration loaded via pydantic-settings."""

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
    playlist_public: bool = False
    gemini_model: str = "gemini-3.8-flash"
