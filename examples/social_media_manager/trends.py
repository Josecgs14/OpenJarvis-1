"""Trend discovery — fetch what's trending and rank it for the user's niche.

Two free, key-less sources are polled:

* Google Trends daily RSS (per-country trending searches)
* Hacker News front page (tech-leaning, high signal for dev niches)

The raw candidates are then scored 0-10 for relevance to the user's
niche by the local model, so the calendar only picks up trends the
account should actually ride.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import List, Tuple

GOOGLE_TRENDS_RSS = "https://trends.google.com/trending/rss?geo={geo}"
HN_TOP_URL = "https://hacker-news.firebaseio.com/v0/topstories.json"
HN_ITEM_URL = "https://hacker-news.firebaseio.com/v0/item/{id}.json"


def fetch_google_trends(geo: str = "US", limit: int = 15) -> List[str]:
    """Return today's trending search titles for *geo* (e.g. 'US', 'MX')."""
    import httpx

    resp = httpx.get(
        GOOGLE_TRENDS_RSS.format(geo=geo), timeout=15.0, follow_redirects=True
    )
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    titles = [(item.findtext("title") or "").strip() for item in root.iter("item")]
    return [t for t in titles if t][:limit]


def fetch_hackernews(limit: int = 15) -> List[str]:
    """Return current Hacker News front-page story titles."""
    import httpx

    ids = httpx.get(HN_TOP_URL, timeout=15.0).json()[:limit]
    titles: List[str] = []
    for story_id in ids:
        try:
            item = httpx.get(HN_ITEM_URL.format(id=story_id), timeout=10.0).json()
            title = (item or {}).get("title", "").strip()
            if title:
                titles.append(title)
        except Exception:
            continue
    return titles


def gather_candidates(
    geo: str = "US",
    limit_per_source: int = 12,
) -> List[Tuple[str, str]]:
    """Collect ``(source, topic)`` pairs from all sources, tolerating failures."""
    candidates: List[Tuple[str, str]] = []
    for source, fetch in (
        ("google-trends", lambda: fetch_google_trends(geo, limit_per_source)),
        ("hackernews", lambda: fetch_hackernews(limit_per_source)),
    ):
        try:
            candidates.extend((source, topic) for topic in fetch())
        except Exception as exc:
            print(f"  [warn] could not fetch {source}: {exc}")
    return candidates


_RANK_PROMPT = (
    "You curate content for a social media account.\n"
    "Account niche: {niche}\n"
    "Audience language: {language}\n\n"
    "Below is a numbered list of currently-trending topics. Score each "
    "from 0 to 10 for how promising it is as a post topic for this "
    "account (10 = perfect fit with an obvious angle, 0 = irrelevant "
    "or risky to comment on).\n\n"
    "{topics}\n\n"
    'Return ONLY a JSON array like [{{"n": 1, "score": 7}}, ...] with '
    "one entry per topic. No prose, no markdown fences."
)


def _parse_scores(response: str, count: int) -> dict[int, float]:
    """Extract {topic_number: score} from the model output, defensively."""
    match = re.search(r"\[.*\]", response or "", re.DOTALL)
    if not match:
        return {}
    try:
        entries = json.loads(match.group(0))
    except json.JSONDecodeError:
        return {}
    scores: dict[int, float] = {}
    for entry in entries:
        try:
            n = int(entry["n"])
            score = float(entry["score"])
        except (KeyError, TypeError, ValueError):
            continue
        if 1 <= n <= count:
            scores[n] = max(0.0, min(10.0, score))
    return scores


def rank_trends(
    jarvis,
    niche: str,
    candidates: List[Tuple[str, str]],
    *,
    language: str = "en",
    model: str | None = None,
) -> List[Tuple[str, str, float]]:
    """Score candidates for the niche. Returns (source, topic, score) desc.

    On any model failure every candidate keeps score 0 — callers can
    still post about them, the calendar just won't prioritize.
    """
    if not candidates:
        return []
    numbered = "\n".join(f"{i}. {topic}" for i, (_, topic) in enumerate(candidates, 1))
    prompt = _RANK_PROMPT.format(niche=niche, language=language, topics=numbered)
    try:
        response = jarvis.ask(
            prompt,
            model=model,
            temperature=0.1,
            max_tokens=2048,
            context=False,
        )
        scores = _parse_scores(response, len(candidates))
    except Exception as exc:
        print(f"  [warn] trend ranking failed: {exc}")
        scores = {}
    ranked = [
        (source, topic, scores.get(i, 0.0))
        for i, (source, topic) in enumerate(candidates, 1)
    ]
    ranked.sort(key=lambda item: item[2], reverse=True)
    return ranked
