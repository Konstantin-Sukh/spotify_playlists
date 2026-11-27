"""Pipeline orchestrator and CLI entry point."""

from __future__ import annotations

import json
import logging
import re
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from friday_drop.ai_parser import ArtistAIParser
from friday_drop.config import Settings
from friday_drop.github_client import GitHubClient
from friday_drop.models import PlaylistSummary, TrackCandidate
from friday_drop.spotify_client import SpotifyClient
from friday_drop.track_filter import filter_tracks

logger = logging.getLogger(__name__)
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_artist_name(name: str) -> str:
    """Normalize artist name for case-insensitive matching against Liked Songs."""
    cleaned = _WHITESPACE_RE.sub(" ", name.strip()).casefold()
    return cleaned.replace("ё", "е")


def load_liked_artists(
    sp: SpotifyClient,
    min_tracks: int,
    cache_path: str,
    cache_ttl_hours: int,
    now_utc: datetime,
) -> list[tuple[str, str]]:
    """Load liked artists from local cache if fresh, otherwise fetch and cache."""
    path = Path(cache_path) if cache_path and cache_ttl_hours > 0 else None

    if path is not None and path.exists():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            updated_at = datetime.fromisoformat(payload["updated_at"])
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)

            is_fresh = (now_utc - updated_at) < timedelta(hours=cache_ttl_hours)
            if is_fresh and payload.get("min_tracks") == min_tracks:
                artists = [(str(item[0]), str(item[1])) for item in payload.get("artists", [])]
                logger.info(
                    "Загружено %d артистов из кэша %s (обновлен %s).",
                    len(artists),
                    path,
                    updated_at.strftime("%Y-%m-%d %H:%M UTC"),
                )
                return artists
        except Exception as exc:
            logger.warning("Не удалось прочитать кэш %s (%s), обновляю из Spotify...", path, exc)

    artists = sp.get_liked_artists(min_tracks=min_tracks)

    if path is not None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "updated_at": now_utc.isoformat(),
                "min_tracks": min_tracks,
                "artists": [[artist_id, name] for artist_id, name in artists],
            }
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            logger.info("Кэш артистов сохранен в %s (%d записей).", path, len(artists))
        except Exception as exc:
            logger.warning("Не удалось сохранить кэш %s: %s", path, exc)

    return artists


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
    issue_artist_queries = parser.parse_comments(comments)
    if not issue_artist_queries:
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
    liked_artists = load_liked_artists(
        sp=sp,
        min_tracks=settings.min_liked_tracks,
        cache_path=settings.liked_cache_path,
        cache_ttl_hours=settings.liked_cache_ttl_hours,
        now_utc=current_utc,
    )
    logger.info(
        "Доступно %d артистов из «Любимых треков» (минимум %d трек).",
        len(liked_artists),
        settings.min_liked_tracks,
    )

    liked_by_name: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for artist_id, canonical_name in liked_artists:
        liked_by_name[normalize_artist_name(canonical_name)].append((artist_id, canonical_name))

    artists_to_check: list[tuple[str, str]] = []
    seen_artist_ids: set[str] = set()
    skipped_not_liked_count = 0

    for artist_query in issue_artist_queries:
        matches = liked_by_name.get(normalize_artist_name(artist_query))
        if not matches:
            skipped_not_liked_count += 1
            continue

        for artist_id, canonical_name in matches:
            if artist_id not in seen_artist_ids:
                seen_artist_ids.add(artist_id)
                artists_to_check.append((artist_id, canonical_name))

    logger.info(
        "После пересечения с «Любимыми треками»: %d артистов для проверки, %d пропущено.",
        len(artists_to_check),
        skipped_not_liked_count,
    )

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
        skipped_not_liked_count=skipped_not_liked_count,
    )
    report = format_report(summary, lookback_days=settings.lookback_days)
    gh.close_issue_with_report(issue, report)

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
            f"За последние {lookback_days} дней у подходящих артистов из ваших «Любимых треков» "
            "не вышло новых треков (после фильтрации версий и релизов до 4 треков), "
            "поэтому плейлист не создавался."
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
    if summary.skipped_not_liked_count:
        lines.append(
            f"- **Нет в «Любимых треках» (пропущено):** {summary.skipped_not_liked_count}"
        )

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
        message = "За последние 6 дней у подходящих артистов не вышло новых треков."
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
