"""Always-on local wake-word voice assistant (``jarvis listen``)."""

from __future__ import annotations

from openjarvis.voice.listener import ListenerConfig, WakeWordListener
from openjarvis.voice.termux_audio import (
    TermuxTTSBackend,
    is_termux,
    termux_api_available,
    termux_audio_source,
)
from openjarvis.voice.wake_word import DEFAULT_WAKE_PHRASES, detect_wake_word

__all__ = [
    "DEFAULT_WAKE_PHRASES",
    "ListenerConfig",
    "TermuxTTSBackend",
    "WakeWordListener",
    "detect_wake_word",
    "is_termux",
    "termux_api_available",
    "termux_audio_source",
]
