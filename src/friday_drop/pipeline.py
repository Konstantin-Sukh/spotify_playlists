"""Pipeline orchestrator and CLI entry point."""

from __future__ import annotations

import logging
import urllib.request
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

    issue_artist_queries: list[str] = []
    if issue is not None:
        comments = gh.get_issue_comments(issue)
        if comments:
            parser = ai_parser or ArtistAIParser(
                api_key=settings.gemini_api_key,
                model=settings.gemini_model,
                fallback_models=settings.gemini_fallback_models,
            )
            issue_artist_queries = parser.parse_comments(comments)

    sp = spotify_client or SpotifyClient(
        client_id=settings.spotify_client_id,
        client_secret=settings.spotify_client_secret,
        refresh_token=settings.spotify_refresh_token,
        redirect_uri=settings.spotify_redirect_uri,
    )

    liked_artists = sp.get_liked_artists(min_tracks=settings.min_liked_tracks)
    logger.info(
        "Найдено %d артистов из «Любимых треков» (минимум %d треков).",
        len(liked_artists),
        settings.min_liked_tracks,
    )

    artists_to_check: list[tuple[str, str]] = list(liked_artists)
    seen_artist_ids: set[str] = {artist_id for artist_id, _ in liked_artists}
    not_found_artists: list[str] = []

    for artist_query in issue_artist_queries:
        found = sp.search_artist(artist_query)
        if found is None:
            not_found_artists.append(artist_query)
            continue

        artist_id, canonical_name = found
        if artist_id not in seen_artist_ids:
            seen_artist_ids.add(artist_id)
            artists_to_check.append((artist_id, canonical_name))

    if not artists_to_check and not not_found_artists:
        logger.info("Нет артистов для проверки (ни в «Любимых треках», ни в Issue).")
        if issue is not None:
            gh.close_issue_no_artists(issue)
        return None

    current_utc = now_utc or datetime.now(timezone.utc)
    candidates: list[TrackCandidate] = []
    matched_artists: list[str] = []
    no_releases_artists: list[str] = []

    for artist_id, canonical_name in artists_to_check:
        artist_tracks = sp.get_recent_tracks(
            artist_id=artist_id,
            artist_name=canonical_name,
            lookback_days=settings.lookback_days,
            now_utc=current_utc,
            max_tracks_per_release=settings.max_tracks_per_release,
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
    if issue is not None:
        gh.close_issue_with_report(issue, report)
    else:
        logger.info("Итоговый отчет (открытый Issue не найден):\n%s", report)

    if settings.ntfy_topic.strip():
        send_ntfy_notification(settings.ntfy_topic.strip(), summary)

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
        if len(summary.no_releases_artists) <= 10:
            lines.append(
                f"- **Без свежих релизов ({lookback_days} дн.):** "
                f"{', '.join(summary.no_releases_artists)}"
            )
        else:
            lines.append(
                f"- **Без свежих релизов ({lookback_days} дн.):** "
                f"{len(summary.no_releases_artists)} артистов"
            )
    if summary.not_found_artists:
        lines.append(f"- **Не найдены в Spotify:** {', '.join(summary.not_found_artists)}")

    return "\n".join(lines)


def send_ntfy_notification(topic: str, summary: PlaylistSummary) -> None:
    """Send a push notification via ntfy.sh with the Friday Drop result."""
    if summary.playlist_url:
        title = "Friday Drop Ready!"
        message = (
            f"Плейлист «{summary.playlist_name}» ({summary.track_count} треков) "
            "уже в вашей медиатеке Spotify!"
        )
        headers = {
            "Title": title,
            "Tags": "headphones,musical_note",
            "Click": summary.playlist_url,
        }
    else:
        title = "Friday Drop: No New Releases"
        message = "За последние 6 дней у выбранных артистов не вышло новых треков."
        headers = {
            "Title": title,
            "Tags": "musical_note",
        }

    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10):
            logger.info("Push-уведомление в ntfy отправлено.")
    except Exception as exc:
        logger.warning("Не удалось отправить уведомление в ntfy: %s", exc)


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
