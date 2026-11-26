"""Gemini AI parser for extracting clean artist names from raw issue comments."""

from __future__ import annotations

from google import genai
from google.genai import types

from friday_drop.models import ParsedArtists

SYSTEM_INSTRUCTION = (
    "Ты помощник для извлечения списка музыкальных исполнителей из комментариев пользователя. "
    "Тебе передается ТОЛЬКО сырой текст комментариев из GitHub Issue. "
    "Извлеки чистый список имен артистов: исправь очевидные опечатки в именах, "
    "удали лишние слова, приветствия, эмодзи, маркеры списков и дубликаты. "
    "Если в тексте нет ни одного артиста, верни пустой список."
)


class ArtistAIParser:
    """Extracts structured artist names from raw GitHub issue comments using Gemini."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        client: genai.Client | None = None,
    ) -> None:
        self._client = client or genai.Client(api_key=api_key)
        self._model = model

    def parse_comments(self, comments: list[str]) -> list[str]:
        """Parse raw comment texts into a deduplicated list of artist names."""
        non_empty = [comment.strip() for comment in comments if comment and comment.strip()]
        if not non_empty:
            return []

        raw_text = "\n---\n".join(non_empty)
        response = self._client.models.generate_content(
            model=self._model,
            contents=raw_text,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=ParsedArtists,
                temperature=0.0,
            ),
        )

        parsed = response.parsed
        if isinstance(parsed, ParsedArtists):
            raw_artists = parsed.artists
        elif response.text:
            raw_artists = ParsedArtists.model_validate_json(response.text).artists
        else:
            raw_artists = []

        return _deduplicate_artists(raw_artists)


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
