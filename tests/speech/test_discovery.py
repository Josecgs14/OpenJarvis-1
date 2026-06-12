"""Tests for speech backend auto-discovery."""

from unittest.mock import patch

from openjarvis.core.config import JarvisConfig


def test_get_speech_backend_explicit():
    """Explicit backend selection works."""
    from openjarvis.speech._discovery import get_speech_backend

    config = JarvisConfig()
    config.speech.backend = "faster-whisper"

    with patch("openjarvis.speech._discovery._create_backend") as mock_create:
        mock_backend = type(
            "MockBackend",
            (),
            {
                "backend_id": "faster-whisper",
                "health": lambda self: True,
            },
        )()
        mock_create.return_value = mock_backend

        result = get_speech_backend(config)
        assert result is not None
        assert result.backend_id == "faster-whisper"


def test_get_speech_backend_returns_none_if_nothing_available():
    """Returns None when no backend can be created."""
    from openjarvis.speech._discovery import get_speech_backend

    config = JarvisConfig()
    config.speech.backend = "nonexistent"

    result = get_speech_backend(config)
    assert result is None


def test_auto_discovery_priority():
    """Auto mode tries backends in priority order."""
    from openjarvis.speech._discovery import DISCOVERY_ORDER

    assert DISCOVERY_ORDER[0] == "faster-whisper"
    assert "openai" in DISCOVERY_ORDER
    assert "deepgram" in DISCOVERY_ORDER


def test_get_tts_backend_explicit():
    """Explicit TTS backend selection works."""
    from openjarvis.speech._discovery import get_tts_backend

    config = JarvisConfig()
    config.speech.tts_backend = "kokoro"

    with patch("openjarvis.speech._discovery._create_tts_backend") as mock_create:
        mock_backend = type(
            "MockBackend",
            (),
            {
                "backend_id": "kokoro",
                "health": lambda self: True,
            },
        )()
        mock_create.return_value = mock_backend

        result = get_tts_backend(config)
        assert result is not None
        assert result.backend_id == "kokoro"


def test_get_tts_backend_returns_none_if_nothing_available():
    """Returns None when no TTS backend can be created."""
    from openjarvis.speech._discovery import get_tts_backend

    config = JarvisConfig()
    config.speech.tts_backend = "nonexistent"

    result = get_tts_backend(config)
    assert result is None


def test_tts_auto_discovery_priority():
    """TTS auto mode tries backends in priority order."""
    from openjarvis.speech._discovery import TTS_DISCOVERY_ORDER

    assert TTS_DISCOVERY_ORDER[0] == "kokoro"
    assert "openai_tts" in TTS_DISCOVERY_ORDER
    assert "cartesia" in TTS_DISCOVERY_ORDER
