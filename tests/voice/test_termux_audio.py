"""Tests for Termux (Android) microphone capture and TTS playback."""

from __future__ import annotations

import array
import logging
import subprocess
import wave
from io import BytesIO
from unittest.mock import patch

from openjarvis.voice.termux_audio import (
    TermuxTTSBackend,
    is_termux,
    termux_api_available,
    termux_audio_source,
)


def _wav_bytes(amplitude: int, n: int = 16000, sample_rate: int = 16000) -> bytes:
    pcm = array.array("h", [amplitude] * n).tobytes()
    buf = BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm)
    return buf.getvalue()


class TestIsTermux:
    def test_true_when_termux_version_set(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
        monkeypatch.delenv("PREFIX", raising=False)
        assert is_termux() is True

    def test_true_when_prefix_is_termux(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/data/data/com.termux/files/usr")
        assert is_termux() is True

    def test_false_on_normal_linux(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/usr")
        assert is_termux() is False


class TestTermuxApiAvailable:
    def test_true_when_binary_found(self):
        bin_path = "/data/data/com.termux/files/usr/bin/termux-microphone-record"
        with patch("shutil.which", return_value=bin_path):
            assert termux_api_available() is True

    def test_false_when_binary_missing(self):
        with patch("shutil.which", return_value=None):
            assert termux_api_available() is False


class TestTermuxAudioSource:
    def test_yields_recorded_pcm(self):
        wav_payload = _wav_bytes(1000, n=8000)

        def _fake_run(cmd, **kwargs):
            if "-f" in cmd:
                path = cmd[cmd.index("-f") + 1]
                with open(path, "wb") as f:
                    f.write(wav_payload)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        run_patch = patch(
            "openjarvis.voice.termux_audio.subprocess.run", side_effect=_fake_run
        )
        sleep_patch = patch("openjarvis.voice.termux_audio.time.sleep")
        with run_patch, sleep_patch:
            gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
            chunk = next(gen)
            gen.close()

        with wave.open(BytesIO(wav_payload), "rb") as wf:
            expected_pcm = wf.readframes(wf.getnframes())

        assert chunk == expected_pcm

    def test_missing_recording_yields_empty_bytes(self):
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch(
            "openjarvis.voice.termux_audio.subprocess.run", return_value=ok
        ), patch("openjarvis.voice.termux_audio.time.sleep"):
            gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
            chunk = next(gen)
            gen.close()

        assert chunk == b""

    def test_warns_when_recording_command_fails(self, caplog):
        failed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Permission denial"
        )
        with patch(
            "openjarvis.voice.termux_audio.subprocess.run", return_value=failed
        ), patch("openjarvis.voice.termux_audio.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="openjarvis.voice.termux_audio"):
                gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
                chunk = next(gen)
                gen.close()

        assert chunk == b""
        assert any("Permission denial" in r.message for r in caplog.records)

    def test_warns_on_empty_recording(self, caplog):
        wav_payload = _wav_bytes(0, n=0)

        def _fake_run(cmd, **kwargs):
            if "-f" in cmd:
                path = cmd[cmd.index("-f") + 1]
                with open(path, "wb") as f:
                    f.write(wav_payload)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        with patch(
            "openjarvis.voice.termux_audio.subprocess.run", side_effect=_fake_run
        ), patch("openjarvis.voice.termux_audio.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="openjarvis.voice.termux_audio"):
                gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
                chunk = next(gen)
                gen.close()

        assert chunk == b""
        assert any("microphone permission" in r.message for r in caplog.records)


class TestTermuxTTSBackend:
    def test_synthesize_calls_termux_tts_speak(self):
        backend = TermuxTTSBackend()
        with patch("openjarvis.voice.termux_audio.subprocess.run") as mock_run:
            result = backend.synthesize("Son las tres")

        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        assert cmd[:1] == ["termux-tts-speak"]
        assert "Son las tres" in cmd

        assert result.audio == b""
        assert result.format == "termux"

    def test_health_reflects_binary_presence(self):
        backend = TermuxTTSBackend()
        with patch("shutil.which", return_value="/usr/bin/termux-tts-speak"):
            assert backend.health() is True
        with patch("shutil.which", return_value=None):
            assert backend.health() is False
