"""Microphone capture and spoken replies for Termux (Android).

Termux cannot use PortAudio/``sounddevice`` for continuous microphone
streaming, so ``jarvis listen`` falls back to the Termux:API command-line
tools when run inside Termux (detected via :func:`is_termux`):

- Capture: :func:`termux_audio_source` records short AAC/M4A clips with
  ``termux-microphone-record`` and yields their raw bytes, acting as an
  ``audio_source`` for :class:`~openjarvis.voice.listener.WakeWordListener`
  in non-VAD mode (each clip is transcribed directly, e.g. via OpenAI
  Whisper which accepts ``m4a``). Android's ``MediaRecorder`` — which backs
  ``termux-microphone-record`` — cannot produce raw PCM/WAV, only
  compressed formats, so streaming energy-based VAD isn't possible here.
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
    """Yield short AAC-encoded clips recorded via ``termux-microphone-record``.

    Each iteration records a ``chunk_seconds``-long clip to a temporary file
    using the phone's microphone (via Termux:API) and yields its raw bytes
    (an MP4/M4A container with AAC audio — ``termux-microphone-record``
    cannot produce raw PCM/WAV). Each clip is a self-contained utterance
    suitable for direct transcription with ``format="m4a"``. Runs until the
    caller stops iterating.
    """
    while True:
        fd, path = tempfile.mkstemp(suffix=".m4a", prefix="jarvis-listen-")
        os.close(fd)
        try:
            # `-l` must be a whole number of seconds — a float string (e.g.
            # "4.0") causes termux-microphone-record to accept the broadcast
            # (exit 0) but produce an empty recording. `-e wav` is kept
            # because it's the encoder value confirmed to actually record on
            # real devices (the output is still AAC/MP4 despite the name).
            record = subprocess.run(
                [
                    "termux-microphone-record",
                    "-f",
                    path,
                    "-l",
                    str(max(1, round(chunk_seconds))),
                    "-e",
                    "wav",
                    "-r",
                    str(sample_rate),
                    "-c",
                    "1",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=chunk_seconds + 10,
            )
            if record.returncode != 0:
                logger.warning(
                    "termux-microphone-record failed (exit %d): %s",
                    record.returncode,
                    (record.stderr or record.stdout).strip(),
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
                with open(path, "rb") as f:
                    data = f.read()
            except FileNotFoundError:
                data = b""
            if not data:
                logger.warning(
                    "No audio captured this chunk (empty recording). Check "
                    "that Termux:API has microphone permission: Android "
                    "Settings > Apps > Termux:API > Permissions > Microphone."
                )
            yield data
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
