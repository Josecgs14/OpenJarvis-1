"""Auto-discover available speech-to-text and text-to-speech backends."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from openjarvis.core.config import JarvisConfig
    from openjarvis.speech._stubs import SpeechBackend
    from openjarvis.speech.tts import TTSBackend

# Priority order: local first, then cloud
DISCOVERY_ORDER = [
    "faster-whisper",
    "openai",
    "deepgram",
]

# Priority order for TTS: local first, then cloud
TTS_DISCOVERY_ORDER = [
    "kokoro",
    "openai_tts",
    "cartesia",
]


def _create_backend(
    key: str,
    config: "JarvisConfig",
) -> Optional["SpeechBackend"]:
    """Try to instantiate a speech backend by registry key."""
    from openjarvis.core.registry import SpeechRegistry

    if not SpeechRegistry.contains(key):
        return None

    try:
        backend_cls = SpeechRegistry.get(key)

        if key == "faster-whisper":
            return backend_cls(
                model_size=config.speech.model,
                device=config.speech.device,
                compute_type=config.speech.compute_type,
            )
        elif key == "openai":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            return backend_cls(api_key=api_key)
        elif key == "deepgram":
            api_key = os.environ.get("DEEPGRAM_API_KEY", "")
            if not api_key:
                return None
            return backend_cls(api_key=api_key)
        else:
            return backend_cls()
    except Exception:
        return None


def get_speech_backend(config: "JarvisConfig") -> Optional["SpeechBackend"]:
    """Resolve the speech-to-text backend from config.

    If ``config.speech.backend`` is ``"auto"``, tries backends in
    priority order and returns the first healthy one.
    """
    # Trigger registration of built-in backends
    import openjarvis.speech  # noqa: F401

    backend_key = config.speech.backend

    if backend_key != "auto":
        return _create_backend(backend_key, config)

    # Auto-discovery: try each in priority order, skipping backends whose
    # dependencies aren't actually installed (e.g. faster-whisper registers
    # itself even without the optional package, but health() reports False).
    for key in DISCOVERY_ORDER:
        backend = _create_backend(key, config)
        if backend is not None and backend.health():
            return backend

    return None


def _create_tts_backend(key: str, config: "JarvisConfig") -> Optional["TTSBackend"]:
    """Try to instantiate a TTS backend by registry key."""
    from openjarvis.core.registry import TTSRegistry

    if not TTSRegistry.contains(key):
        return None

    try:
        backend_cls = TTSRegistry.get(key)

        if key == "kokoro":
            backend = backend_cls()
        elif key == "openai_tts":
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if not api_key:
                return None
            backend = backend_cls(api_key=api_key)
        elif key == "cartesia":
            api_key = os.environ.get("CARTESIA_API_KEY", "")
            if not api_key:
                return None
            backend = backend_cls(api_key=api_key)
        else:
            backend = backend_cls()

        return backend if backend.health() else None
    except Exception:
        return None


def get_tts_backend(config: "JarvisConfig") -> Optional["TTSBackend"]:
    """Resolve the text-to-speech backend from config.

    If ``config.speech.tts_backend`` is ``"auto"``, tries backends in
    priority order (local first, then cloud) and returns the first
    healthy one.
    """
    # Trigger registration of built-in backends
    import openjarvis.speech  # noqa: F401

    backend_key = config.speech.tts_backend

    if backend_key != "auto":
        return _create_tts_backend(backend_key, config)

    for key in TTS_DISCOVERY_ORDER:
        backend = _create_tts_backend(key, config)
        if backend is not None:
            return backend

    return None
