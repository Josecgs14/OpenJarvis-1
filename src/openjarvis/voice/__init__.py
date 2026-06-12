"""Always-on local wake-word voice assistant (``jarvis listen``)."""

from __future__ import annotations

from openjarvis.voice.listener import ListenerConfig, WakeWordListener
from openjarvis.voice.wake_word import DEFAULT_WAKE_PHRASES, detect_wake_word

__all__ = [
    "DEFAULT_WAKE_PHRASES",
    "ListenerConfig",
    "WakeWordListener",
    "detect_wake_word",
]
