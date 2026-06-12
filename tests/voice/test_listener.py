"""Tests for the always-on wake-word audio listener."""

from __future__ import annotations

import array
import wave
from io import BytesIO
from unittest.mock import MagicMock

from openjarvis.speech._stubs import TranscriptionResult
from openjarvis.speech.tts import TTSResult
from openjarvis.voice.listener import (
    ListenerConfig,
    WakeWordListener,
    pcm16_to_wav,
    rms_level,
)


def _chunk(amplitude: int, n: int = 10) -> bytes:
    """Build a chunk of *n* 16-bit PCM samples, all set to *amplitude*."""
    return array.array("h", [amplitude] * n).tobytes()


class TestPcm16ToWav:
    def test_roundtrip(self):
        pcm = _chunk(1000, n=16000)
        wav = pcm16_to_wav(pcm, sample_rate=16000)

        with wave.open(BytesIO(wav), "rb") as wf:
            assert wf.getnchannels() == 1
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == 16000
            assert wf.readframes(wf.getnframes()) == pcm


class TestRmsLevel:
    def test_silence_is_zero(self):
        assert rms_level(_chunk(0)) == 0.0

    def test_empty_is_zero(self):
        assert rms_level(b"") == 0.0

    def test_loud_audio_above_default_threshold(self):
        assert rms_level(_chunk(20000)) > 0.02


class TestWakeWordListenerRun:
    """Tests that drive WakeWordListener.run() with an injected audio source."""

    def _config(self) -> ListenerConfig:
        # 2 chunks of trailing silence (0.1s each) end an utterance.
        return ListenerConfig(silence_duration=0.2, sample_rate=16000)

    def test_dispatches_command_and_speaks_reply(self):
        audio_chunks = [
            _chunk(0),  # silence — ignored, not speaking yet
            _chunk(20000),  # speech — utterance starts
            _chunk(20000),  # speech — continues
            _chunk(0),  # silence 1
            _chunk(0),  # silence 2 — ends the utterance
        ]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(
            text="Hola Claude dime la hora"
        )

        tts_backend = MagicMock()
        tts_backend.synthesize.return_value = TTSResult(
            audio=b"wav-bytes", format="wav"
        )

        on_command = MagicMock(return_value="Son las tres")
        play_audio = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            tts_backend=tts_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
            play_audio=play_audio,
        )

        listener.run()

        speech_backend.transcribe.assert_called_once()
        on_command.assert_called_once_with("dime la hora")
        tts_backend.synthesize.assert_called_once_with(
            "Son las tres", output_format="wav"
        )
        play_audio.assert_called_once_with(b"wav-bytes", "wav")

    def test_no_wake_phrase_skips_command(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(text="just noise")

        on_command = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
        )

        listener.run()

        on_command.assert_not_called()

    def test_wake_phrase_only_waits_for_followup(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(text="Hola Claude")

        on_command = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
        )

        listener.run()

        on_command.assert_not_called()

    def test_empty_transcription_is_ignored(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(text="")

        on_command = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
        )

        listener.run()

        on_command.assert_not_called()

    def test_transcription_exception_does_not_crash(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.side_effect = Exception("boom")

        on_command = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
        )

        listener.run()  # should not raise

        on_command.assert_not_called()

    def test_no_tts_backend_skips_playback(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(
            text="Hola Claude qué hora es"
        )

        on_command = MagicMock(return_value="Son las tres")
        play_audio = MagicMock()

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
            play_audio=play_audio,
        )

        listener.run()

        on_command.assert_called_once_with("qué hora es")
        play_audio.assert_not_called()

    def test_on_command_exception_does_not_crash(self):
        audio_chunks = [_chunk(20000), _chunk(0), _chunk(0)]

        speech_backend = MagicMock()
        speech_backend.transcribe.return_value = TranscriptionResult(
            text="Hola Claude rompete"
        )

        on_command = MagicMock(side_effect=Exception("boom"))

        listener = WakeWordListener(
            speech_backend=speech_backend,
            on_command=on_command,
            config=self._config(),
            audio_source=iter(audio_chunks),
        )

        listener.run()  # should not raise

        on_command.assert_called_once()
