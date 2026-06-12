"""Content generation and evaluation — the LLM-facing half of the manager.

Everything here takes a ``jarvis`` handle (the OpenJarvis SDK facade) plus
the user's brand *profile* and returns plain Python values; no I/O other
than the model calls, so it's easy to test with a stub.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Brand profile
# ---------------------------------------------------------------------------


@dataclass
class Profile:
    """The account's identity — who we post as, about what, and how."""

    name: str = "My Brand"
    niche: str = "technology"
    tone: str = "friendly, helpful, a little playful"
    language: str = "en"
    geo: str = "US"
    platforms: List[str] = field(default_factory=lambda: ["twitter"])
    hashtags: List[str] = field(default_factory=list)
    posts_per_day: int = 2
    posting_hours: List[int] = field(default_factory=lambda: [9, 18])
    photo_queue: str = ""

    @classmethod
    def load(cls, path: Path | str) -> "Profile":
        """Load a profile from TOML (see ``profile.example.toml``)."""
        try:
            import tomllib
        except ImportError:  # Python 3.10
            import tomli as tomllib  # type: ignore[no-redef]
        with Path(path).open("rb") as f:
            data = tomllib.load(f)
        section = data.get("profile", data)
        known = {f_.name for f_ in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in section.items() if k in known})

    def queued_photos(self) -> List[Path]:
        """Photos waiting in the queue directory, oldest first."""
        if not self.photo_queue:
            return []
        queue = Path(self.photo_queue).expanduser()
        if not queue.is_dir():
            return []
        exts = {".jpg", ".jpeg", ".png", ".gif", ".webp"}
        photos = [p for p in sorted(queue.iterdir()) if p.suffix.lower() in exts]
        photos.sort(key=lambda p: p.stat().st_mtime)
        return photos


def _photo_notes(photo: Path) -> str:
    """Read the optional sidecar note (``photo.jpg`` -> ``photo.txt``).

    A sidecar lets the user tell the captioner what's in the shot
    without needing a vision model.
    """
    sidecar = photo.with_suffix(".txt")
    if sidecar.exists():
        try:
            return sidecar.read_text(encoding="utf-8").strip()
        except OSError:
            return ""
    return ""


# ---------------------------------------------------------------------------
# Post generation
# ---------------------------------------------------------------------------

_VOICE = (
    "Writing rules:\n"
    "- Write in {language}.\n"
    "- Tone: {tone}.\n"
    "- HARD LIMIT: {char_limit} characters total, hashtags included.\n"
    "- At most 2 hashtags{hashtag_hint}.\n"
    "- No invented facts, stats, URLs, or quotes. If you don't know, "
    "don't claim.\n"
    "- Output ONLY the post text. No quotes around it, no explanation.\n"
)


def _voice(profile: Profile, char_limit: int) -> str:
    hint = (
        f", preferring these when relevant: {', '.join(profile.hashtags)}"
        if profile.hashtags
        else ""
    )
    return _VOICE.format(
        language=profile.language,
        tone=profile.tone,
        char_limit=char_limit,
        hashtag_hint=hint,
    )


def _clean_post(text: str, char_limit: int) -> str:
    """Strip wrappers the model may add and enforce the platform limit."""
    text = (text or "").strip()
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1].strip()
    text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    if len(text) > char_limit:
        cut = text[: char_limit - 1]
        # Trim back to the last whole word so we never cut mid-word
        cut = cut.rsplit(" ", 1)[0] if " " in cut else cut
        text = cut + "…"
    return text


def generate_post(
    jarvis,
    profile: Profile,
    *,
    topic: str,
    char_limit: int = 280,
    trend_context: str = "",
    model: Optional[str] = None,
) -> str:
    """Draft one post about *topic* in the account's voice."""
    context_block = (
        f"\nWhy now (trending context): {trend_context}\n" if trend_context else ""
    )
    prompt = (
        f'You manage the social account of "{profile.name}" '
        f"(niche: {profile.niche}).\n"
        f"Write ONE post about: {topic}\n"
        f"{context_block}\n" + _voice(profile, char_limit)
    )
    response = jarvis.ask(
        prompt, model=model, temperature=0.7, max_tokens=512, context=False
    )
    return _clean_post(response, char_limit)


def caption_photo(
    jarvis,
    profile: Profile,
    photo: Path,
    *,
    char_limit: int = 280,
    model: Optional[str] = None,
) -> str:
    """Write a caption for a queued photo.

    Uses the filename plus an optional ``.txt`` sidecar note describing
    the shot, so no vision model is required.
    """
    notes = _photo_notes(photo)
    description = notes or photo.stem.replace("_", " ").replace("-", " ")
    prompt = (
        f'You manage the social account of "{profile.name}" '
        f"(niche: {profile.niche}).\n"
        f"Write ONE caption for a photo we are about to post.\n"
        f"What the photo shows: {description}\n\n" + _voice(profile, char_limit)
    )
    response = jarvis.ask(
        prompt, model=model, temperature=0.7, max_tokens=512, context=False
    )
    return _clean_post(response, char_limit)


# ---------------------------------------------------------------------------
# Calendar planning
# ---------------------------------------------------------------------------

_PLAN_PROMPT = (
    "You are planning the next {days} days of content for the social "
    'account of "{name}" (niche: {niche}; audience language: '
    "{language}).\n\n"
    "Relevant trending topics right now (score 0-10):\n{trends}\n\n"
    "Evergreen angle: anything useful/entertaining for the niche.\n\n"
    "Produce {total} post ideas: a mix of trend-riding posts (only for "
    "trends scored >= 5) and evergreen niche posts. For each idea give "
    "a short, concrete topic (what the post will say/show, not just a "
    "keyword).\n\n"
    "Return ONLY a JSON array like "
    '[{{"topic": "...", "kind": "trend|evergreen"}}, ...] '
    "with exactly {total} entries. No prose, no markdown fences."
)


def plan_topics(
    jarvis,
    profile: Profile,
    ranked_trends: List[tuple],
    *,
    days: int,
    model: Optional[str] = None,
) -> List[Dict[str, str]]:
    """Ask the model for a topic list covering *days* of posting.

    Falls back to simple trend/evergreen topics if the model output
    can't be parsed, so planning never returns empty-handed.
    """
    total = max(1, days * profile.posts_per_day)
    trends_block = (
        "\n".join(
            f"- [{score:.0f}/10] {topic} (via {source})"
            for source, topic, score in ranked_trends[:12]
        )
        or "- (no trend data available)"
    )
    prompt = _PLAN_PROMPT.format(
        days=days,
        name=profile.name,
        niche=profile.niche,
        language=profile.language,
        trends=trends_block,
        total=total,
    )
    try:
        response = jarvis.ask(
            prompt,
            model=model,
            temperature=0.5,
            max_tokens=2048,
            context=False,
        )
        match = re.search(r"\[.*\]", response or "", re.DOTALL)
        ideas = json.loads(match.group(0)) if match else []
    except Exception:
        ideas = []
    cleaned = [
        {"topic": str(i["topic"]).strip(), "kind": str(i.get("kind", "evergreen"))}
        for i in ideas
        if isinstance(i, dict) and i.get("topic")
    ]
    if not cleaned:
        # Fallback: ride the top trends, pad with the niche itself
        for source, topic, score in ranked_trends:
            if score >= 5 and len(cleaned) < total:
                cleaned.append({"topic": f"our take on: {topic}", "kind": "trend"})
        while len(cleaned) < total:
            cleaned.append(
                {"topic": f"a practical tip about {profile.niche}", "kind": "evergreen"}
            )
    return cleaned[:total]


# ---------------------------------------------------------------------------
# Performance evaluation
# ---------------------------------------------------------------------------

_REPORT_PROMPT = (
    "You are the analytics brain of a social media manager for "
    '"{name}" (niche: {niche}).\n\n'
    "Here is the recent posting history with engagement metrics "
    "(JSON):\n{history}\n\n"
    "Aggregate totals: {totals}\n\n"
    "Write a short performance report in {language} with exactly these "
    "sections:\n"
    "1. Resumen / Summary — 2-3 sentences on overall performance.\n"
    "2. Lo que funcionó / What worked — which posts/topics got the most "
    "engagement and a hypothesis why.\n"
    "3. Lo que no / What didn't — weakest content and why.\n"
    "4. Recomendaciones / Recommendations — 3 concrete, actionable "
    "changes for next week (topics, timing, format).\n\n"
    "Base every claim ONLY on the data above. Plain text, no markdown "
    "tables."
)


def evaluate_performance(
    jarvis,
    profile: Profile,
    history: List[Dict[str, Any]],
    totals: Dict[str, int],
    *,
    model: Optional[str] = None,
) -> str:
    """Turn raw history+metrics into an actionable written report."""
    prompt = _REPORT_PROMPT.format(
        name=profile.name,
        niche=profile.niche,
        language=profile.language,
        history=json.dumps(history, ensure_ascii=False, indent=1),
        totals=json.dumps(totals),
    )
    return jarvis.ask(
        prompt, model=model, temperature=0.3, max_tokens=2048, context=False
    )
