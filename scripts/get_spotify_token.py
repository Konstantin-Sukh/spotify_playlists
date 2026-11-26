"""One-time local utility to obtain a Spotify OAuth2 refresh token."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv
from spotipy.cache_handler import MemoryCacheHandler
from spotipy.oauth2 import SpotifyOAuth

SCOPES = "playlist-modify-public playlist-modify-private user-library-read"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8888/callback"


def main() -> None:
    """Run local OAuth2 flow and print SPOTIFY_REFRESH_TOKEN."""
    load_dotenv()

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "").strip()
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI).strip()

    if not client_id or not client_secret:
        print(
            "Ошибка: задайте SPOTIFY_CLIENT_ID и SPOTIFY_CLIENT_SECRET "
            "в .env или переменных окружения.",
            file=sys.stderr,
        )
        sys.exit(1)

    auth_manager = SpotifyOAuth(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_handler=MemoryCacheHandler(),
        open_browser=True,
    )

    print("Открываю браузер для авторизации в Spotify...")
    print(f"Redirect URI: {redirect_uri}")
    token_info = auth_manager.get_access_token(as_dict=True)
    refresh_token = token_info.get("refresh_token")

    if not refresh_token:
        print("Не удалось получить refresh_token от Spotify.", file=sys.stderr)
        sys.exit(1)

    print("\nАвторизация прошла успешно!")
    print("Добавьте эту строку в .env и в GitHub Secrets:\n")
    print(f"SPOTIFY_REFRESH_TOKEN={refresh_token}")


if __name__ == "__main__":
    main()
