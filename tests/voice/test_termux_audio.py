"""Tests for Termux (Android) microphone capture and TTS playback."""

from __future__ import annotations

import logging
import subprocess
import tempfile
from unittest.mock import patch

from openjarvis.voice.termux_audio import (
    TermuxTTSBackend,
    _recording_dir,
    is_termux,
    termux_api_available,
    termux_audio_source,
)


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


class TestRecordingDir:
    def test_non_termux_uses_system_tmpdir(self, monkeypatch):
        monkeypatch.delenv("TERMUX_VERSION", raising=False)
        monkeypatch.setenv("PREFIX", "/usr")

        assert _recording_dir() == tempfile.gettempdir()

    def test_termux_uses_shared_storage(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
        with patch("openjarvis.voice.termux_audio.os.makedirs") as mock_makedirs:
            assert _recording_dir() == "/sdcard/.jarvis-listen-tmp"
        mock_makedirs.assert_called_once_with(
            "/sdcard/.jarvis-listen-tmp", exist_ok=True
        )

    def test_termux_falls_back_if_shared_storage_unusable(self, monkeypatch):
        monkeypatch.setenv("TERMUX_VERSION", "0.118.0")
        with patch(
            "openjarvis.voice.termux_audio.os.makedirs",
            side_effect=OSError("no /sdcard"),
        ):
            assert _recording_dir() == tempfile.gettempdir()


class TestTermuxAudioSource:
    def test_yields_recorded_clip(self):
        # termux-microphone-record always produces an MP4/AAC container,
        # regardless of the requested filename/extension.
        clip_bytes = b"\x00\x00\x00\x18ftypmp42" + b"...rest of the clip..."

        def _fake_run(cmd, **kwargs):
            if "-f" in cmd:
                path = cmd[cmd.index("-f") + 1]
                with open(path, "wb") as f:
                    f.write(clip_bytes)
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        run_patch = patch(
            "openjarvis.voice.termux_audio.subprocess.run", side_effect=_fake_run
        )
        sleep_patch = patch("openjarvis.voice.termux_audio.time.sleep")
        with run_patch, sleep_patch:
            gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
            chunk = next(gen)
            gen.close()

        assert chunk == clip_bytes

    def test_warns_when_recording_command_fails(self, caplog):
        failed = subprocess.CompletedProcess(
            [], 1, stdout="", stderr="Permission denial"
        )
        with patch(
            "openjarvis.voice.termux_audio.subprocess.run", return_value=failed
        ), patch("openjarvis.voice.termux_audio.time.sleep"):
            with caplog.at_level(
                logging.WARNING, logger="openjarvis.voice.termux_audio"
            ):
                gen = termux_audio_source(chunk_seconds=1.0, sample_rate=16000)
                chunk = next(gen)
                gen.close()

        assert chunk == b""
        assert any("Permission denial" in r.message for r in caplog.records)

    def test_warns_on_empty_recording(self, caplog):
        ok = subprocess.CompletedProcess([], 0, stdout="", stderr="")
        with patch(
            "openjarvis.voice.termux_audio.subprocess.run", return_value=ok
        ), patch("openjarvis.voice.termux_audio.time.sleep"):
            with caplog.at_level(
                logging.WARNING, logger="openjarvis.voice.termux_audio"
            ):
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
