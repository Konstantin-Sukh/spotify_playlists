"""Gemini AI parser for extracting clean artist names from raw issue comments."""

from __future__ import annotations

import logging
import re
import time

from google import genai
from google.genai import errors, types

from friday_drop.models import ParsedArtists

logger = logging.getLogger(__name__)
_WHITESPACE_RE = re.compile(r"\s+")

FALLBACK_STATUS_CODES = {404, 429, 500, 502, 503, 504}
DEFAULT_FALLBACK_MODELS = (
    "gemini-3.1-flash-lite",
    "gemini-flash-lite-latest",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
)
DEFAULT_MAX_RETRIES = 5
DEFAULT_RETRY_DELAY_SECONDS = 15.0
DEFAULT_REQUEST_TIMEOUT_MS = 15_000
MAX_CHARS_PER_CHUNK = 8000

SYSTEM_INSTRUCTION = (
    "Ты помощник для извлечения списка музыкальных исполнителей из комментариев пользователя. "
    "Тебе передается ТОЛЬКО сырой текст комментариев из GitHub Issue (обычно это список "
    "релизов формата «Артист - Название релиза» с треками, синглами и альбомами). "
    "Твоя задача — извлечь чистый список имен ВСЕХ артистов из текста "
    "(не исключай релизы с пометкой «альбом»/«EP» — тип релиза дополнительно проверится позже):\n"
    "1. Если на релизе указано несколько артистов (через запятую, &, x, feat., ft., и), "
    "извлекай каждого артиста отдельно.\n"
    "2. Убирай названия самих треков и альбомов, пометки «(Альбом)», «(EP)», даты, эмодзи, "
    "маркеры списков и дубликаты.\n"
    "3. Исправляй очевидные опечатки. Для русскоязычных и двуязычных артистов, которые в Spotify "
    "могут быть записаны латиницей или переводом, включай В ОБА варианта написания "
    "(например: «Хаски» -> добавь и «Хаски», и «Husky»; «Фараон» -> «Фараон» и «PHARAOH»; "
    "«Скриптонит» -> «Скриптонит» и «Scriptonite»; «дмтбой» -> «dmtboy»).\n"
    "Если в тексте нет ни одного артиста, верни пустой список."
)


class ArtistAIParser:
    """Extracts structured artist names from raw GitHub issue comments using Gemini."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-3.5-flash-lite",
        fallback_models: list[str] | tuple[str, ...] = DEFAULT_FALLBACK_MODELS,
        client: genai.Client | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_delay_seconds: float = DEFAULT_RETRY_DELAY_SECONDS,
    ) -> None:
        self._client = client or genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=DEFAULT_REQUEST_TIMEOUT_MS),
        )
        self._models = _unique_models([model, *fallback_models])
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds

    def parse_comments(self, comments: list[str]) -> list[str]:
        """Parse raw comment texts into a deduplicated list of artist names."""
        non_empty = [comment.strip() for comment in comments if comment and comment.strip()]
        if not non_empty:
            return []

        raw_artists: list[str] = []
        for chunk in _chunk_comments(non_empty, max_chars=MAX_CHARS_PER_CHUNK):
            response = self._generate_with_retries(chunk)
            parsed = response.parsed
            if isinstance(parsed, ParsedArtists):
                raw_artists.extend(parsed.artists)
            elif response.text:
                raw_artists.extend(ParsedArtists.model_validate_json(response.text).artists)

        return _deduplicate_artists(raw_artists)

    def _generate_with_retries(self, raw_text: str) -> types.GenerateContentResponse:
        """Try models in order, retrying the chain on transient API errors."""
        config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            response_mime_type="application/json",
            response_schema=ParsedArtists,
            temperature=0.0,
        )

        last_error: errors.APIError | None = None

        for round_idx in range(self._max_retries + 1):
            for model_name in self._models:
                try:
                    return self._client.models.generate_content(
                        model=model_name,
                        contents=raw_text,
                        config=config,
                    )
                except errors.APIError as exc:
                    status_code = getattr(exc, "code", None)
                    if status_code not in FALLBACK_STATUS_CODES:
                        raise
                    last_error = exc
                    logger.warning(
                        "Модель %s вернула %s, переключаюсь на следующую модель...",
                        model_name,
                        status_code,
                    )

            if round_idx < self._max_retries:
                logger.warning(
                    "Все модели недоступны (попытка %d/%d). Повтор через %.0f сек...",
                    round_idx + 1,
                    self._max_retries,
                    self._retry_delay_seconds,
                )
                time.sleep(self._retry_delay_seconds)

        assert last_error is not None
        raise last_error


def _unique_models(models: list[str]) -> list[str]:
    """Preserve order while removing empty or duplicate model names."""
    result: list[str] = []
    seen: set[str] = set()
    for name in models:
        cleaned = name.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result


def _chunk_comments(comments: list[str], max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Group comments (or split oversized ones by line) into bounded chunks."""
    chunks: list[str] = []
    current_parts: list[str] = []
    current_len = 0

    for comment in comments:
        pieces = _split_oversized_text(comment, max_chars) if len(comment) > max_chars else [comment]
        for piece in pieces:
            sep_len = 5 if current_parts else 0  # len("\n---\n") == 5
            if current_parts and current_len + sep_len + len(piece) > max_chars:
                chunks.append("\n---\n".join(current_parts))
                current_parts = [piece]
                current_len = len(piece)
            else:
                current_parts.append(piece)
                current_len += sep_len + len(piece)

    if current_parts:
        chunks.append("\n---\n".join(current_parts))
    return chunks


def _split_oversized_text(text: str, max_chars: int) -> list[str]:
    """Split a large comment along line boundaries into chunks <= max_chars."""
    parts: list[str] = []
    lines: list[str] = []
    length = 0

    for line in text.splitlines():
        add_len = len(line) + (1 if lines else 0)
        if lines and length + add_len > max_chars:
            parts.append("\n".join(lines))
            lines = [line]
            length = len(line)
        else:
            lines.append(line)
            length += add_len

    if lines:
        parts.append("\n".join(lines))
    return parts


def _deduplicate_artists(artists: list[str]) -> list[str]:
    """Strip whitespace and deduplicate artist names case-insensitively."""
    result: list[str] = []
    seen: set[str] = set()
    for artist in artists:
        cleaned = _WHITESPACE_RE.sub(" ", artist.strip())
        if not cleaned:
            continue
        key = cleaned.casefold()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
