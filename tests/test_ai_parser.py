"""Tests for Gemini AI artist parser."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from google.genai import errors

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


def test_parse_comments_switches_to_fallback_model_on_503() -> None:
    mock_client = MagicMock()
    err_503 = errors.ServerError(503, {"error": {"code": 503, "message": "Unavailable"}})
    success_resp = SimpleNamespace(parsed=ParsedArtists(artists=["Muse"]), text=None)
    mock_client.models.generate_content.side_effect = [err_503, success_resp]

    parser = ArtistAIParser(
        api_key="test-key",
        model="gemini-3.8-flash",
        fallback_models=["gemini-3.5-flash-lite"],
        client=mock_client,
    )

    with patch("friday_drop.ai_parser.time.sleep") as mock_sleep:
        result = parser.parse_comments(["Muse"])

    assert result == ["Muse"]
    assert mock_client.models.generate_content.call_count == 2
    called_models = [c.kwargs["model"] for c in mock_client.models.generate_content.call_args_list]
    assert called_models == ["gemini-3.8-flash", "gemini-3.5-flash-lite"]
    mock_sleep.assert_not_called()


def test_parse_comments_retries_full_chain_five_times_on_503() -> None:
    mock_client = MagicMock()
    err_503 = errors.ServerError(503, {"error": {"code": 503, "message": "Unavailable"}})
    mock_client.models.generate_content.side_effect = err_503

    parser = ArtistAIParser(
        api_key="test-key",
        model="gemini-3.8-flash",
        fallback_models=["gemini-3.5-flash-lite"],
        client=mock_client,
    )

    with (
        patch("friday_drop.ai_parser.time.sleep") as mock_sleep,
        pytest.raises(errors.ServerError),
    ):
        parser.parse_comments(["Muse"])

    # 6 rounds * 2 models = 12 calls; 5 sleeps of 15s between rounds
    assert mock_client.models.generate_content.call_count == 12
    assert mock_sleep.call_count == 5
    mock_sleep.assert_called_with(15.0)


def test_parse_comments_does_not_retry_on_400() -> None:
    mock_client = MagicMock()
    err_400 = errors.ClientError(400, {"error": {"code": 400, "message": "Bad Request"}})
    mock_client.models.generate_content.side_effect = err_400

    parser = ArtistAIParser(
        api_key="test-key",
        model="gemini-3.8-flash",
        fallback_models=["gemini-3.5-flash-lite"],
        client=mock_client,
    )

    with (
        patch("friday_drop.ai_parser.time.sleep") as mock_sleep,
        pytest.raises(errors.ClientError),
    ):
        parser.parse_comments(["Muse"])

    assert mock_client.models.generate_content.call_count == 1
    mock_sleep.assert_not_called()


def test_parse_comments_chunks_multiple_large_lists_and_deduplicates() -> None:
    mock_client = MagicMock()
    mock_client.models.generate_content.side_effect = [
        SimpleNamespace(parsed=ParsedArtists(artists=["Husky", "Muse"]), text=None),
        SimpleNamespace(parsed=ParsedArtists(artists=["husky", "  MUSE ", "SALUKI"]), text=None),
    ]
    parser = ArtistAIParser(api_key="test-key", client=mock_client)

    list_one = "\n".join(f"Artist {i} - Song {i}" for i in range(250))  # > 4000 chars
    list_two = "\n".join(f"Other {i} - Track {i}" for i in range(250))  # > 4000 chars, combined > 8000
    result = parser.parse_comments([list_one, list_two])

    assert result == ["Husky", "Muse", "SALUKI"]
    assert mock_client.models.generate_content.call_count == 2
