"""Always-on microphone listener: wake phrase -> command -> spoken reply.

Captures audio from the system microphone, segments it into utterances
using simple energy-based voice activity detection (VAD), transcribes each
utterance with a speech-to-text backend, and — when the transcript starts
with a configured wake phrase (e.g. "Hola Claude") — dispatches the
remaining text to a callback. If a text-to-speech backend is configured,
the callback's return value is synthesized and played back through the
speakers.
"""

from __future__ import annotations

import array
import logging
import queue
import wave
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable, Iterator, List, Optional

from openjarvis.voice.wake_word import DEFAULT_WAKE_PHRASES, detect_wake_word

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16000
CHUNK_SECONDS = 0.1


@dataclass
class ListenerConfig:
    """Tuning parameters for :class:`WakeWordListener`."""

    wake_phrases: List[str] = field(default_factory=lambda: list(DEFAULT_WAKE_PHRASES))
    silence_threshold: float = 0.02  # RMS amplitude (0-1) below which audio is silence
    silence_duration: float = 1.0  # seconds of trailing silence that ends an utterance
    max_utterance_seconds: float = 15.0
    sample_rate: int = SAMPLE_RATE


def pcm16_to_wav(pcm: bytes, sample_rate: int) -> bytes:
    """Wrap raw mono 16-bit PCM samples in a WAV container."""
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


def rms_level(pcm: bytes) -> float:
    """Return the normalized RMS amplitude (0.0-1.0) of 16-bit PCM samples."""
    if not pcm:
        return 0.0
    samples = array.array("h", pcm)
    if not samples:
        return 0.0
    mean_sq = sum(s * s for s in samples) / len(samples)
    return (mean_sq**0.5) / 32768.0


class WakeWordListener:
    """Listens for a wake phrase on the microphone and dispatches commands."""

    def __init__(
        self,
        *,
        speech_backend,
        on_command: Callable[[str], Optional[str]],
        tts_backend: object = None,
        config: Optional[ListenerConfig] = None,
        audio_source: Optional[Iterator[bytes]] = None,
        play_audio: Optional[Callable[[bytes, str], None]] = None,
    ) -> None:
        self._speech_backend = speech_backend
        self._on_command = on_command
        self._tts_backend = tts_backend
        self._config = config or ListenerConfig()
        self._audio_source = audio_source
        self._play_audio = play_audio
        self._stop = False

    def stop(self) -> None:
        """Signal the run loop to exit after the current chunk."""
        self._stop = True

    def _default_audio_source(self) -> Iterator[bytes]:
        """Yield mono 16-bit PCM chunks from the default microphone."""
        try:
            import sounddevice as sd
        except ImportError as exc:
            raise ImportError(
                "sounddevice is required for `jarvis listen`. Install with: "
                'pip install "openjarvis[speech-mic]"'
            ) from exc

        cfg = self._config
        chunk_frames = max(1, int(cfg.sample_rate * CHUNK_SECONDS))
        q: "queue.Queue[bytes]" = queue.Queue()

        def _callback(indata, frames, time_info, status):
            if status:
                logger.debug("Audio input status: %s", status)
            q.put(bytes(indata))

        with sd.RawInputStream(
            samplerate=cfg.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=chunk_frames,
            callback=_callback,
        ):
            while not self._stop:
                yield q.get()

    def _default_play_audio(self, audio: bytes, fmt: str) -> None:
        """Play synthesized WAV audio through the default speaker."""
        if fmt.lower() != "wav":
            logger.debug("Cannot play back non-WAV TTS audio (format=%s)", fmt)
            return
        try:
            import numpy as np
            import sounddevice as sd
        except ImportError:
            logger.debug("sounddevice/numpy not installed; skipping playback")
            return

        with wave.open(BytesIO(audio), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            samples = np.frombuffer(frames, dtype="<i2")
            sd.play(samples, samplerate=wf.getframerate())
            sd.wait()

    def run(self) -> None:
        """Block, listening for the wake phrase and dispatching commands."""
        audio_source = self._audio_source
        if audio_source is None:
            audio_source = self._default_audio_source()
        play_audio = self._play_audio or self._default_play_audio

        cfg = self._config
        silence_chunks_limit = max(1, int(cfg.silence_duration / CHUNK_SECONDS))
        max_chunks = max(1, int(cfg.max_utterance_seconds / CHUNK_SECONDS))

        buffer = bytearray()
        speaking = False
        silence_chunks = 0
        total_chunks = 0

        for chunk in audio_source:
            if self._stop:
                break

            is_speech = rms_level(chunk) >= cfg.silence_threshold

            if not speaking:
                if is_speech:
                    speaking = True
                    buffer = bytearray(chunk)
                    silence_chunks = 0
                    total_chunks = 1
                continue

            buffer.extend(chunk)
            total_chunks += 1
            silence_chunks = 0 if is_speech else silence_chunks + 1

            if silence_chunks >= silence_chunks_limit or total_chunks >= max_chunks:
                utterance = bytes(buffer)
                buffer = bytearray()
                speaking = False
                silence_chunks = 0
                total_chunks = 0
                self._handle_utterance(utterance, play_audio)

    def _handle_utterance(self, pcm: bytes, play_audio: Callable[[bytes, str], None]) -> None:
        wav = pcm16_to_wav(pcm, self._config.sample_rate)
        try:
            result = self._speech_backend.transcribe(wav, format="wav")
        except Exception:
            logger.exception("Transcription failed")
            return

        text = (result.text or "").strip()
        if not text:
            return

        command = detect_wake_word(text, self._config.wake_phrases)
        if command is None:
            logger.debug("No wake phrase in transcript: %r", text)
            return

        if not command:
            logger.info("Wake phrase detected, waiting for a command")
            return

        logger.info("Command: %s", command)
        try:
            reply = self._on_command(command)
        except Exception:
            logger.exception("Command handler failed")
            return

        if reply and self._tts_backend is not None:
            try:
                tts_result = self._tts_backend.synthesize(reply, output_format="wav")
                play_audio(tts_result.audio, tts_result.format)
            except Exception:
                logger.exception("Voice reply synthesis/playback failed")


__all__ = ["ListenerConfig", "WakeWordListener", "pcm16_to_wav", "rms_level"]
