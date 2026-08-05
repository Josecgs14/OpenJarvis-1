"""Tests for wake-phrase detection."""

from __future__ import annotations

from openjarvis.voice.wake_word import DEFAULT_WAKE_PHRASES, detect_wake_word


class TestDetectWakeWord:
    def test_default_phrases_includes_hola_claude(self):
        assert "hola claude" in DEFAULT_WAKE_PHRASES

    def test_exact_wake_phrase_only_returns_empty_command(self):
        assert detect_wake_word("Hola Claude") == ""

    def test_wake_phrase_with_command(self):
        result = detect_wake_word("Hola Claude, abre mi correo")
        assert result == "abre mi correo"

    def test_case_insensitive(self):
        result = detect_wake_word("HOLA CLAUDE qué hora es")
        assert result == "qué hora es"

    def test_accent_insensitive(self):
        # "Holá Cláude" — accented variant should still match "hola claude"
        result = detect_wake_word("Holá Cláude, qué tal")
        assert result == "qué tal"

    def test_alternate_phrase_hey_claude(self):
        result = detect_wake_word("Hey Claude, what's the weather")
        assert result == "what's the weather"

    def test_no_wake_phrase_returns_none(self):
        assert detect_wake_word("just talking to myself") is None

    def test_mid_sentence_mention_does_not_trigger(self):
        # The wake phrase must be at the start of the transcript.
        assert detect_wake_word("I was telling Claude about it") is None

    def test_empty_transcript_returns_none(self):
        assert detect_wake_word("") is None

    def test_custom_phrases(self):
        result = detect_wake_word("Computer, status report", phrases=["computer"])
        assert result == "status report"

    def test_punctuation_does_not_break_matching(self):
        result = detect_wake_word("Hola Claude, ¿qué hora es?")
        assert result == "qué hora es"
