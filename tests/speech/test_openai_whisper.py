"""Tests for OpenAI Whisper API speech backend."""

from unittest.mock import MagicMock, patch

import pytest

from openjarvis.core.registry import SpeechRegistry
from openjarvis.speech._stubs import TranscriptionResult
from openjarvis.speech.openai_whisper import OpenAIWhisperBackend


@pytest.fixture(autouse=True)
def _register_openai_whisper():
    """Re-register after any registry clear."""
    if not SpeechRegistry.contains("openai"):
        SpeechRegistry.register_value("openai", OpenAIWhisperBackend)


def test_openai_whisper_registers():
    assert SpeechRegistry.contains("openai")


def test_openai_whisper_transcribe():
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Hello from OpenAI"
    mock_response.language = "en"
    mock_response.duration = 2.0
    mock_client.audio.transcriptions.create.return_value = mock_response

    with patch("openjarvis.speech.openai_whisper.OpenAI", return_value=mock_client):
        from openjarvis.speech.openai_whisper import OpenAIWhisperBackend

        backend = OpenAIWhisperBackend(api_key="test-key")
        result = backend.transcribe(b"fake audio", format="wav")

        assert isinstance(result, TranscriptionResult)
        assert result.text == "Hello from OpenAI"
        assert result.language == "en"


def test_openai_whisper_health():
    with patch("openjarvis.speech.openai_whisper.OpenAI"):
        from openjarvis.speech.openai_whisper import OpenAIWhisperBackend

        backend = OpenAIWhisperBackend(api_key="test-key")
        assert backend.health() is True


def test_openai_whisper_health_no_key():
    with patch("openjarvis.speech.openai_whisper.OpenAI"):
        from openjarvis.speech.openai_whisper import OpenAIWhisperBackend

        backend = OpenAIWhisperBackend.__new__(OpenAIWhisperBackend)
        backend._client = None
        backend._api_key = ""
        assert backend.health() is False


def test_openai_whisper_transcribe_http_fallback():
    """When the `openai` package isn't installed (e.g. Termux, where its
    Rust-based deps jiter/pydantic-core have no prebuilt wheels), transcribe
    via raw HTTP instead."""
    with patch("openjarvis.speech.openai_whisper.OpenAI", None):
        backend = OpenAIWhisperBackend(api_key="test-key")
        assert backend._client is None
        assert backend.health() is True

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "text": "Hello via HTTP",
            "language": "en",
            "duration": 1.5,
        }

        with patch(
            "openjarvis.speech.openai_whisper.httpx.post",
            return_value=mock_response,
        ) as mock_post:
            result = backend.transcribe(b"fake audio", format="wav")

        assert result.text == "Hello via HTTP"
        assert result.language == "en"
        assert result.duration_seconds == 1.5

        call = mock_post.call_args
        assert call.kwargs["headers"]["Authorization"] == "Bearer test-key"
        assert call.kwargs["data"]["model"] == "whisper-1"
