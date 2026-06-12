"""Microphone capture and spoken replies for Termux (Android).

Termux cannot use PortAudio/``sounddevice`` for continuous microphone
streaming, so ``jarvis listen`` falls back to the Termux:API command-line
tools when run inside Termux (detected via :func:`is_termux`):

- Capture: :func:`termux_audio_source` records short WAV clips with
  ``termux-microphone-record`` and yields their raw PCM frames, acting as
  an ``audio_source`` for :class:`~openjarvis.voice.listener.WakeWordListener`.
- Spoken replies: :class:`TermuxTTSBackend` speaks text via
  ``termux-tts-speak`` — Android's built-in text-to-speech engine. No extra
  Python packages or API keys are required.

Install the Termux:API companion app from F-Droid/Play Store and run
``pkg install termux-api`` to make these commands available.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
import time
import wave
from typing import Any, Iterator

from openjarvis.speech.tts import TTSResult

logger = logging.getLogger(__name__)


def is_termux() -> bool:
    """Return True if running inside Termux on Android."""
    if "TERMUX_VERSION" in os.environ:
        return True
    return "com.termux" in os.environ.get("PREFIX", "")


def termux_api_available() -> bool:
    """Return True if Termux:API tools are installed (``pkg install termux-api``)."""
    return shutil.which("termux-microphone-record") is not None


def termux_audio_source(
    chunk_seconds: float = 4.0, sample_rate: int = 16000
) -> Iterator[bytes]:
    """Yield mono 16-bit PCM chunks recorded via ``termux-microphone-record``.

    Each iteration records a ``chunk_seconds``-long WAV clip to a temporary
    file using the phone's microphone (via Termux:API) and yields its raw
    PCM frames. Runs until the caller stops iterating.
    """
    while True:
        fd, path = tempfile.mkstemp(suffix=".wav", prefix="jarvis-listen-")
        os.close(fd)
        try:
            subprocess.run(
                [
                    "termux-microphone-record",
                    "-f",
                    path,
                    "-l",
                    str(chunk_seconds),
                    "-e",
                    "wav",
                    "-r",
                    str(sample_rate),
                    "-c",
                    "1",
                ],
                check=False,
                capture_output=True,
                timeout=chunk_seconds + 10,
            )
            # The recording runs for `chunk_seconds` in the background;
            # wait for it to finish before reading the file back.
            time.sleep(chunk_seconds + 0.5)
            subprocess.run(
                ["termux-microphone-record", "-d"],
                check=False,
                capture_output=True,
                timeout=10,
            )
            try:
                with wave.open(path, "rb") as wf:
                    yield wf.readframes(wf.getnframes())
            except (wave.Error, EOFError, FileNotFoundError):
                logger.debug("No usable audio captured for this chunk")
                yield b""
        finally:
            if os.path.exists(path):
                os.remove(path)


class TermuxTTSBackend:
    """Speaks replies aloud via Android's built-in TTS (``termux-tts-speak``)."""

    def health(self) -> bool:
        return shutil.which("termux-tts-speak") is not None

    def synthesize(self, text: str, output_format: str = "wav", **_: Any) -> TTSResult:
        """Speak *text* via ``termux-tts-speak`` (blocks until done)."""
        subprocess.run(
            ["termux-tts-speak", text], check=False, capture_output=True, timeout=120
        )
        return TTSResult(audio=b"", format="termux")


__all__ = [
    "TermuxTTSBackend",
    "is_termux",
    "termux_api_available",
    "termux_audio_source",
]
