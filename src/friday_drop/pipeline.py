"""Pipeline orchestrator and CLI entry point."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from friday_drop.ai_parser import ArtistAIParser
from friday_drop.config import Settings
from friday_drop.github_client import GitHubClient
from friday_drop.models import PlaylistSummary, TrackCandidate
from friday_drop.spotify_client import SpotifyClient
from friday_drop.track_filter import filter_tracks

logger = logging.getLogger(__name__)


def run_pipeline(
    settings: Settings,
    github_client: GitHubClient | None = None,
    ai_parser: ArtistAIParser | None = None,
    spotify_client: SpotifyClient | None = None,
    now_utc: datetime | None = None,
) -> PlaylistSummary | None:
    """Execute the Friday Drop workflow end-to-end."""
    gh = github_client or GitHubClient(
        token=settings.github_token,
        repository=settings.github_repository,
    )
    issue = gh.find_open_issue(label=settings.issue_label)
    if issue is None:
        logger.info("Открытый Issue с лейблом '%s' не найден. Завершаем работу.", settings.issue_label)
        return None

    comments = gh.get_issue_comments(issue)
    if not comments:
        logger.info("Комментариев в Issue #%s нет. Закрываем Issue.", issue.number)
        gh.close_issue_no_artists(issue)
        return None

    parser = ai_parser or ArtistAIParser(
        api_key=settings.gemini_api_key,
        model=settings.gemini_model,
        fallback_models=settings.gemini_fallback_models,
    )
    artists = parser.parse_comments(comments)
    if not artists:
        logger.info("Артисты в комментариях Issue #%s не найдены. Закрываем Issue.", issue.number)
        gh.close_issue_no_artists(issue)
        return None

    sp = spotify_client or SpotifyClient(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        refresh_token=settings.spotify_refresh_token,
        redirect_uri=settings.spotify_redirect_uri,
    )

    current_utc = now_utc or datetime.now(timezone.utc)
    candidates: list[TrackCandidate] = []
    matched_artists: list[str] = []
    no_releases_artists: list[str] = []
    not_found_artists: list[str] = []

    for artist_query in artists:
        found = sp.search_artist(artist_query)
        if found is None:
            not_found_artists.append(artist_query)
            continue

        artist_id, canonical_name = found
        artist_tracks = sp.get_recent_tracks(
            artist_id=artist_id,
            artist_name=canonical_name,
            lookback_days=settings.lookback_days,
            now_utc=current_utc,
        )
        if artist_tracks:
            matched_artists.append(canonical_name)
            candidates.extend(artist_tracks)
        else:
            no_releases_artists.append(canonical_name)

    filtered_tracks = filter_tracks(candidates)
    playlist_name = f"Friday Drop — {current_utc.strftime('%d.%m.%Y')}"
    playlist_url: str | None = None

    if filtered_tracks:
        track_uris = [track.uri for track in filtered_tracks]
        playlist_url = sp.create_playlist(
            name=playlist_name,
            track_uris=track_uris,
            public=settings.playlist_public,
        )

    summary = PlaylistSummary(
        playlist_url=playlist_url,
        playlist_name=playlist_name if playlist_url else None,
        track_count=len(filtered_tracks),
        matched_artists=matched_artists,
        no_releases_artists=no_releases_artists,
        not_found_artists=not_found_artists,
    )
    report = format_report(summary, lookback_days=settings.lookback_days)
    gh.close_issue_with_report(issue, report)
    return summary


def format_report(summary: PlaylistSummary, lookback_days: int = 6) -> str:
    """Format a human-readable Markdown report for the GitHub Issue."""
    lines: list[str] = []

    if summary.playlist_url:
        lines.append(f"## Плейлист готов: [{summary.playlist_name}]({summary.playlist_url})")
        lines.append(f"- **Добавлено треков:** {summary.track_count}")
    else:
        lines.append("## Новых релизов не найдено")
        lines.append(
            f"За последние {lookback_days} дней у выбранных артистов не вышло новых треков "
            "(после фильтрации версий), поэтому плейлист не создавался."
        )

    if summary.matched_artists:
        lines.append(f"- **Артисты в плейлисте:** {', '.join(summary.matched_artists)}")
    if summary.no_releases_artists:
        lines.append(
            f"- **Без свежих релизов ({lookback_days} дн.):** "
            f"{', '.join(summary.no_releases_artists)}"
        )
    if summary.not_found_artists:
        lines.append(f"- **Не найдены в Spotify:** {', '.join(summary.not_found_artists)}")

    return "\n".join(lines)


def main() -> None:
    """CLI entry point for Friday Drop."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    settings = Settings()
    run_pipeline(settings)


if __name__ == "__main__":
    main()
