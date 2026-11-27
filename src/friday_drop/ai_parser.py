"""Gemini AI parser for extracting clean artist names from raw issue comments."""

from __future__ import annotations

import logging
import time

from google import genai
from google.genai import errors, types

from friday_drop.models import ParsedArtists

logger = logging.getLogger(__name__)

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

SYSTEM_INSTRUCTION = (
    "Ты помощник для извлечения списка музыкальных исполнителей из комментариев пользователя. "
    "Тебе передается ТОЛЬКО сырой текст комментариев из GitHub Issue (обычно это большой список "
    "релизов формата «Артист - Название релиза» с треками/синглами и альбомами). "
    "Твоя задача — извлечь чистый список имен артистов ТОЛЬКО из синглов (отдельных треков):\n"
    "1. Пропускай релизы, которые явно помечены как альбом, LP, EP или микстейп "
    "(или находятся в разделе альбомов). Если тип релиза не указан — считай его синглом.\n"
    "2. Если на сингле указано несколько артистов (через запятую, &, x, feat., ft., и), "
    "извлекай каждого артиста отдельно.\n"
    "3. Убирай названия самих треков, пометки, даты, эмодзи, маркеры списков и дубликаты.\n"
    "4. Исправляй очевидные опечатки и приводи транслитерацию к оригинальному написанию "
    "(например, «дмтбой» -> «dmtboy»), сохраняя кириллицу для русскоязычных псевдонимов.\n"
    "Если в тексте нет ни одного подходящего артиста, верни пустой список."
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

        raw_text = "\n---\n".join(non_empty)
        response = self._generate_with_retries(raw_text)

        parsed = response.parsed
        if isinstance(parsed, ParsedArtists):
            raw_artists = parsed.artists
        elif response.text:
            raw_artists = ParsedArtists.model_validate_json(response.text).artists
        else:
            raw_artists = []

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


def _deduplicate_artists(artists: list[str]) -> list[str]:
    """Strip whitespace and deduplicate artist names case-insensitively."""
    result: list[str] = []
    seen: set[str] = set()
    for artist in artists:
        cleaned = artist.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key not in seen:
            seen.add(key)
            result.append(cleaned)
    return result
