"""Tests for Gemini AI artist parser."""

from types import SimpleNamespace
from unittest.mock import MagicMock

from friday_drop.ai_parser import ArtistAIParser
from friday_drop.models import ParsedArtists


def test_parse_comments_returns_empty_without_api_call_when_no_text() -> None:
    mock_client = MagicMock()
    parser = ArtistAIParser(api_key="test-key", client=mock_client)

    assert parser.parse_comments([]) == []
    assert parser.parse_comments(["   ", "\n"]) == []
    mock_client.models.generate_content.assert_not_called()


def test_parse_comments_uses_structured_output_and_deduplicates() -> None:
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = SimpleNamespace(
        parsed=ParsedArtists(artists=[" Daft Punk ", "daft punk", "The Weeknd", ""]),
        text=None,
    )
    parser = ArtistAIParser(api_key="test-key", client=mock_client)

    result = parser.parse_comments(["Добавьте Daft Punk", "и еще the weeknd плз"])

    assert result == ["Daft Punk", "The Weeknd"]
    mock_client.models.generate_content.assert_called_once()


def test_parse_comments_falls_back_to_json_text() -> None:
    mock_client = MagicMock()
    mock_client.models.generate_content.return_value = SimpleNamespace(
        parsed=None,
        text='{"artists": ["Fred again..", "Disclosure"]}',
    )
    parser = ArtistAIParser(api_key="test-key", client=mock_client)

    result = parser.parse_comments(["Fred again и Disclosure"])

    assert result == ["Fred again..", "Disclosure"]
