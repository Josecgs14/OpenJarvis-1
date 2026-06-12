"""Tests for speech configuration."""

from openjarvis.core.config import JarvisConfig, SpeechConfig, VoiceConfig


def test_speech_config_defaults():
    cfg = SpeechConfig()
    assert cfg.backend == "auto"
    assert cfg.model == "base"
    assert cfg.language == ""
    assert cfg.device == "auto"
    assert cfg.compute_type == "float16"


def test_speech_config_tts_defaults():
    cfg = SpeechConfig()
    assert cfg.tts_backend == "auto"
    assert cfg.tts_voice_id == ""


def test_jarvis_config_has_speech():
    cfg = JarvisConfig()
    assert hasattr(cfg, "speech")
    assert isinstance(cfg.speech, SpeechConfig)
    assert cfg.speech.backend == "auto"


def test_jarvis_system_has_speech_backend():
    """JarvisSystem has a speech_backend attribute."""
    from openjarvis.system import JarvisSystem

    assert "speech_backend" in JarvisSystem.__dataclass_fields__


def test_voice_config_defaults():
    cfg = VoiceConfig()
    assert "hola claude" in cfg.wake_phrases
    assert cfg.speak_replies is True
    assert cfg.agent == ""
    assert cfg.silence_threshold == 0.02
    assert cfg.silence_duration == 1.0
    assert cfg.max_utterance_seconds == 15.0
    assert cfg.termux_chunk_seconds == 4.0


def test_jarvis_config_has_voice():
    cfg = JarvisConfig()
    assert hasattr(cfg, "voice")
    assert isinstance(cfg.voice, VoiceConfig)
