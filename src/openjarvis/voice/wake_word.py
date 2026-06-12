"""Wake-phrase detection for the local always-on voice listener."""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

# Spanish and English variants of "Hola Claude" — checked in order.
DEFAULT_WAKE_PHRASES: List[str] = [
    "hola claude",
    "hey claude",
    "ok claude",
    "oye claude",
]

_WORD_RE = re.compile(r"[\w']+", re.UNICODE)


def _normalize_word(word: str) -> str:
    """Lowercase a word and strip accents/diacritics for fuzzy matching."""
    decomposed = unicodedata.normalize("NFKD", word)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_accents.lower()


def detect_wake_word(
    text: str, phrases: Optional[List[str]] = None
) -> Optional[str]:
    """Return the command following a wake phrase, or ``None`` if absent.

    Matching is case- and accent-insensitive ("Hola Claude" == "hola
    claude" == "Hola, Claude!") and only triggers when a wake phrase
    appears at the *start* of the transcript, so a mid-sentence mention of
    the assistant's name doesn't accidentally activate it.

    Returns an empty string when the transcript consists of *only* the
    wake phrase — the caller should then prompt for a follow-up command.
    Returns ``None`` when no configured wake phrase matches.
    """
    phrases = phrases or DEFAULT_WAKE_PHRASES
    words = _WORD_RE.findall(text)
    if not words:
        return None
    norm_words = [_normalize_word(w) for w in words]

    for phrase in phrases:
        phrase_words = [_normalize_word(w) for w in _WORD_RE.findall(phrase)]
        if not phrase_words:
            continue
        n = len(phrase_words)
        if norm_words[:n] == phrase_words:
            remainder = " ".join(words[n:]).strip(" ,.!?")
            return remainder

    return None


__all__ = ["DEFAULT_WAKE_PHRASES", "detect_wake_word"]
