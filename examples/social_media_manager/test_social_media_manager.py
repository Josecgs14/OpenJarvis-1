"""Offline tests for the social media manager example.

Everything here runs without a network connection, credentials, or an
inference engine — the LLM is replaced with a canned-response stub.

Run:  uv run pytest examples/social_media_manager/ -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import content as content_mod
import trends as trends_mod
from content import Profile
from platforms import DryRunPlatform, get_platform
from store import STATUS_SCHEDULED, Post, Store


class StubJarvis:
    """Returns a fixed response and records the prompts it was asked."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []

    def ask(self, prompt: str, **kwargs) -> str:
        self.prompts.append(prompt)
        return self.response


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def test_store_post_lifecycle(tmp_path):
    with Store(tmp_path / "t.db") as store:
        post = store.add_post(
            Post(
                platform="twitter",
                status=STATUS_SCHEDULED,
                topic="ai",
                scheduled_at="2020-01-01T00:00:00+00:00",
            )
        )
        assert store.due_posts(now="2020-01-02T00:00:00+00:00")[0].id == post.id
        # not yet due from the past
        assert store.due_posts(now="2019-12-31T00:00:00+00:00") == []

        store.mark_published(post.id, "tw-123", "https://x.com/i/status/tw-123")
        refreshed = store.get_post(post.id)
        assert refreshed.status == "published"
        assert refreshed.remote_id == "tw-123"
        assert store.due_posts(now="2099-01-01T00:00:00+00:00") == []


def test_store_failure_and_metrics(tmp_path):
    with Store(tmp_path / "t.db") as store:
        good = store.add_post(
            Post(
                platform="twitter",
                status=STATUS_SCHEDULED,
                scheduled_at="2020-01-01T00:00:00+00:00",
            )
        )
        bad = store.add_post(
            Post(
                platform="twitter",
                status=STATUS_SCHEDULED,
                scheduled_at="2020-01-01T00:00:00+00:00",
            )
        )
        store.mark_published(good.id, "id-1")
        store.mark_failed(bad.id, "boom")

        store.record_metrics(good.id, likes=3, shares=1, replies=2, impressions=90)
        store.record_metrics(good.id, likes=10, shares=2, replies=2, impressions=300)
        # latest snapshot wins
        assert store.latest_metrics(good.id)["likes"] == 10

        totals = store.totals_since("2019-01-01T00:00:00+00:00")
        assert totals == {
            "posts": 1,
            "likes": 10,
            "shares": 2,
            "replies": 2,
            "impressions": 300,
        }
        assert store.get_post(bad.id).error == "boom"


def test_store_trend_log(tmp_path):
    with Store(tmp_path / "t.db") as store:
        store.log_trends("hackernews", [("rust 2.0", 8.0), ("celeb gossip", 1.0)])
        logged = store.recent_trends()
        assert {t["topic"] for t in logged} == {"rust 2.0", "celeb gossip"}


# ---------------------------------------------------------------------------
# trends ranking
# ---------------------------------------------------------------------------


def test_rank_trends_parses_scores():
    jarvis = StubJarvis('[{"n": 1, "score": 9}, {"n": 2, "score": 2}]')
    ranked = trends_mod.rank_trends(
        jarvis,
        "ai",
        [("hn", "new LLM released"), ("google", "football final")],
    )
    assert ranked[0] == ("hn", "new LLM released", 9.0)
    assert ranked[1][2] == 2.0


def test_rank_trends_survives_garbage_output():
    jarvis = StubJarvis("sorry, I cannot do that")
    ranked = trends_mod.rank_trends(jarvis, "ai", [("hn", "topic")])
    assert ranked == [("hn", "topic", 0.0)]


def test_rank_trends_clamps_and_ignores_bad_entries():
    jarvis = StubJarvis('[{"n": 1, "score": 99}, {"n": 7, "score": 5}, {"x": 1}]')
    ranked = trends_mod.rank_trends(jarvis, "ai", [("hn", "a"), ("hn", "b")])
    assert ranked[0][2] == 10.0  # clamped
    assert ranked[1][2] == 0.0  # n=7 out of range, entry without n ignored


# ---------------------------------------------------------------------------
# content generation
# ---------------------------------------------------------------------------


def test_generate_post_strips_wrappers_and_enforces_limit():
    long_text = '"' + "palabra " * 60 + '"'
    jarvis = StubJarvis(long_text)
    text = content_mod.generate_post(
        jarvis,
        Profile(),
        topic="ai",
        char_limit=100,
    )
    assert len(text) <= 100
    assert not text.startswith('"')
    assert text.endswith("…")


def test_generate_post_includes_brand_and_topic():
    jarvis = StubJarvis("hola mundo")
    profile = Profile(name="Mi Marca", niche="café de especialidad", language="es")
    content_mod.generate_post(jarvis, profile, topic="métodos de filtrado")
    prompt = jarvis.prompts[0]
    assert "Mi Marca" in prompt
    assert "café de especialidad" in prompt
    assert "métodos de filtrado" in prompt
    assert "es" in prompt


def test_caption_photo_uses_sidecar_notes(tmp_path):
    photo = tmp_path / "sunset_beach.jpg"
    photo.write_bytes(b"\xff\xd8fake")
    photo.with_suffix(".txt").write_text("atardecer en la playa con amigos")
    jarvis = StubJarvis("buen atardecer")
    content_mod.caption_photo(jarvis, Profile(), photo)
    assert "atardecer en la playa con amigos" in jarvis.prompts[0]


def test_plan_topics_fallback_without_model_json():
    jarvis = StubJarvis("not json at all")
    profile = Profile(posts_per_day=1)
    ideas = content_mod.plan_topics(
        jarvis,
        profile,
        [("hn", "big ai news", 9.0)],
        days=2,
    )
    assert len(ideas) == 2
    assert ideas[0]["kind"] == "trend"
    assert "big ai news" in ideas[0]["topic"]


def test_plan_topics_parses_model_json():
    jarvis = StubJarvis(
        '[{"topic": "demo of our app", "kind": "evergreen"},'
        ' {"topic": "hot take on X", "kind": "trend"}]'
    )
    ideas = content_mod.plan_topics(jarvis, Profile(posts_per_day=1), [], days=2)
    assert [i["topic"] for i in ideas] == ["demo of our app", "hot take on X"]


def test_profile_load_and_photo_queue(tmp_path):
    queue = tmp_path / "photos"
    queue.mkdir()
    (queue / "b.png").write_bytes(b"png")
    (queue / "a.jpg").write_bytes(b"jpg")
    (queue / "notes.txt").write_text("not a photo")
    cfg = tmp_path / "profile.toml"
    cfg.write_text(
        "[profile]\n"
        'name = "Test"\nniche = "n"\nlanguage = "es"\n'
        f'photo_queue = "{queue}"\n'
        "unknown_key = 1\n"  # unknown keys must be ignored
    )
    profile = Profile.load(cfg)
    assert profile.name == "Test"
    assert profile.language == "es"
    names = {p.name for p in profile.queued_photos()}
    assert names == {"a.jpg", "b.png"}


# ---------------------------------------------------------------------------
# platforms
# ---------------------------------------------------------------------------


def test_dry_run_platform_publish_and_metrics():
    plat = DryRunPlatform()
    result = plat.publish("hola", image_path=None)
    assert result.ok and result.remote_id == "dryrun-1"
    metrics = plat.fetch_metrics(result.remote_id)
    assert set(metrics) == {"likes", "shares", "replies", "impressions"}
    # deterministic for the same id
    assert metrics == plat.fetch_metrics(result.remote_id)


def test_get_platform_dry_run_inherits_char_limit():
    plat = get_platform("mastodon", dry_run=True)
    assert isinstance(plat, DryRunPlatform)
    assert plat.char_limit == 500
    assert get_platform("instagram", dry_run=True).char_limit == 2200


def test_meta_platforms_require_credentials(monkeypatch):
    import platforms as platforms_mod

    for var in (
        "FACEBOOK_PAGE_ID",
        "FACEBOOK_PAGE_ACCESS_TOKEN",
        "INSTAGRAM_USER_ID",
        "INSTAGRAM_ACCESS_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    import pytest

    with pytest.raises(platforms_mod.PlatformError):
        platforms_mod.FacebookPlatform()
    with pytest.raises(platforms_mod.PlatformError):
        platforms_mod.InstagramPlatform()


def test_instagram_rejects_missing_or_local_photo(monkeypatch):
    import platforms as platforms_mod

    monkeypatch.setenv("INSTAGRAM_USER_ID", "123")
    monkeypatch.setenv("INSTAGRAM_ACCESS_TOKEN", "tok")
    plat = platforms_mod.InstagramPlatform()

    # No photo: Instagram can't post text-only — fails before any HTTP call
    result = plat.publish("hola")
    assert not result.ok
    assert "requires a photo" in result.error

    # Local file: the Graph API only fetches public URLs
    result = plat.publish("hola", image_path="/tmp/foto.jpg")
    assert not result.ok
    assert "publicly accessible" in result.error


def test_is_url():
    import platforms as platforms_mod

    assert platforms_mod.is_url("https://example.com/a.jpg")
    assert platforms_mod.is_url("http://example.com/a.jpg")
    assert not platforms_mod.is_url("/home/user/a.jpg")
    assert not platforms_mod.is_url("a.jpg")
