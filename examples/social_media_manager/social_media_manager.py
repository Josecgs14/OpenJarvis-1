#!/usr/bin/env python3
"""OpenJarvis Social Media Manager — your AI community manager.

An autonomous agent that runs an account end to end: discovers trends,
plans a content calendar, writes and publishes posts (text and photos),
pulls engagement metrics, evaluates what worked, and keeps a full audit
trail of everything in SQLite.

Usage:
    python social_media_manager.py trends             # what's hot for my niche
    python social_media_manager.py plan --days 7      # build the week's calendar
    python social_media_manager.py post --topic "..." # compose + publish now
    python social_media_manager.py run                # publish what's due (cron)
    python social_media_manager.py report --days 7    # engagement evaluation
    python social_media_manager.py status             # everything the agent tracks

Add ``--dry-run`` to any publishing command to print posts instead of
publishing — no credentials needed.
"""

from __future__ import annotations

import shutil
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import click

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import content as content_mod  # noqa: E402
import platforms as platforms_mod  # noqa: E402
import trends as trends_mod  # noqa: E402
from content import Profile  # noqa: E402
from store import (  # noqa: E402
    DEFAULT_DB_PATH,
    STATUS_SCHEDULED,
    Post,
    Store,
)


@dataclass
class Ctx:
    """Shared CLI state built once in the group callback."""

    profile: Profile
    store: Store
    model: Optional[str]
    engine_key: Optional[str]
    dry_run: bool
    _jarvis: Any = None

    @property
    def jarvis(self):
        """Lazily build the Jarvis SDK handle (needs a running engine)."""
        if self._jarvis is None:
            try:
                from openjarvis import Jarvis
            except ImportError:
                raise click.ClickException(
                    "openjarvis is not installed — run: uv sync --extra dev"
                )
            try:
                self._jarvis = Jarvis(model=self.model, engine_key=self.engine_key)
            except Exception as exc:
                raise click.ClickException(
                    f"could not initialize Jarvis — {exc}\n"
                    "Make sure your engine is running (e.g. `ollama serve`)."
                )
        return self._jarvis

    def close(self) -> None:
        if self._jarvis is not None:
            self._jarvis.close()
        self.store.close()

    def platform(self, name: str):
        return platforms_mod.get_platform(name, dry_run=self.dry_run)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@click.group()
@click.option(
    "--profile",
    "profile_path",
    default=str(_HERE / "profile.toml"),
    show_default=True,
    help="Path to the brand profile TOML.",
)
@click.option(
    "--db",
    "db_path",
    default=str(DEFAULT_DB_PATH),
    show_default=True,
    help="Path to the SQLite state database.",
)
@click.option("--model", default=None, help="Model to use (e.g. qwen3:8b).")
@click.option(
    "--engine",
    "engine_key",
    default=None,
    help="Engine backend (ollama, cloud, vllm, ...).",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print posts instead of publishing. No credentials needed.",
)
@click.pass_context
def cli(
    ctx,
    profile_path: str,
    db_path: str,
    model: Optional[str],
    engine_key: Optional[str],
    dry_run: bool,
) -> None:
    """AI agent that plans, posts, measures, and tracks your social media."""
    path = Path(profile_path)
    if path.exists():
        profile = Profile.load(path)
    else:
        profile = Profile()
        click.echo(
            f"[info] no profile at {path} — using defaults. Copy "
            "profile.example.toml to profile.toml and edit it.",
        )
    ctx.obj = Ctx(
        profile=profile,
        store=Store(db_path),
        model=model,
        engine_key=engine_key,
        dry_run=dry_run,
    )
    ctx.call_on_close(ctx.obj.close)


# ---------------------------------------------------------------------------
# trends
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--limit", default=10, show_default=True, help="How many ranked trends to show."
)
@click.pass_obj
def trends(ctx: Ctx, limit: int) -> None:
    """Fetch current trends and rank them for your niche."""
    p = ctx.profile
    click.echo(f"Fetching trends for niche: {p.niche} (geo: {p.geo})...")
    candidates = trends_mod.gather_candidates(geo=p.geo)
    if not candidates:
        raise click.ClickException("no trend sources reachable")
    click.echo(f"Ranking {len(candidates)} candidates with the model...")
    ranked = trends_mod.rank_trends(
        ctx.jarvis,
        p.niche,
        candidates,
        language=p.language,
        model=ctx.model,
    )
    ctx.store.log_trends("mixed", [(topic, score) for _, topic, score in ranked])
    click.echo()
    for source, topic, score in ranked[:limit]:
        click.echo(f"  [{score:4.1f}/10] {topic}   ({source})")


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------


@cli.command()
@click.option(
    "--days", default=7, show_default=True, help="How many days of content to plan."
)
@click.option(
    "--platform",
    "platform_name",
    default=None,
    help="Platform to plan for (default: first in profile).",
)
@click.pass_obj
def plan(ctx: Ctx, days: int, platform_name: Optional[str]) -> None:
    """Build a content calendar: trends + evergreen ideas + queued photos."""
    p = ctx.profile
    platform_name = platform_name or (p.platforms[0] if p.platforms else "twitter")

    click.echo("1/3 Gathering and ranking trends...")
    candidates = trends_mod.gather_candidates(geo=p.geo)
    ranked = (
        trends_mod.rank_trends(
            ctx.jarvis,
            p.niche,
            candidates,
            language=p.language,
            model=ctx.model,
        )
        if candidates
        else []
    )
    if ranked:
        ctx.store.log_trends("mixed", [(topic, score) for _, topic, score in ranked])

    click.echo("2/3 Planning topics with the model...")
    ideas = content_mod.plan_topics(ctx.jarvis, p, ranked, days=days, model=ctx.model)

    click.echo("3/3 Scheduling posts...")
    photos = p.queued_photos()
    slots = _calendar_slots(p, days)
    scheduled = 0
    for idea, slot in zip(ideas, slots):
        image_path = str(photos.pop(0)) if photos else None
        post = Post(
            platform=platform_name,
            status=STATUS_SCHEDULED,
            topic=idea["topic"],
            image_path=image_path,
            scheduled_at=_iso(slot),
        )
        ctx.store.add_post(post)
        scheduled += 1
        photo_tag = "  [+photo]" if image_path else ""
        click.echo(
            f"  {slot.astimezone().strftime('%a %d %H:%M')}  "
            f"({idea['kind']}) {idea['topic'][:70]}{photo_tag}"
        )
    click.echo(
        f"\nCalendar ready: {scheduled} posts scheduled on {platform_name}. "
        "Text is written at publish time so it stays fresh.\n"
        "Run `... run` (or cron it) to publish when due."
    )


def _calendar_slots(p: Profile, days: int) -> list:
    """Future posting datetimes from the profile's hours, soonest first."""
    hours = sorted(p.posting_hours) or [9]
    now = datetime.now().astimezone()
    slots = []
    for day in range(days + 1):
        date = now + timedelta(days=day)
        for hour in hours[: max(1, p.posts_per_day)]:
            slot = date.replace(hour=hour, minute=0, second=0, microsecond=0)
            if slot > now:
                slots.append(slot)
    return slots[: days * max(1, p.posts_per_day)]


# ---------------------------------------------------------------------------
# post
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--topic", default=None, help="What to post about (AI writes it).")
@click.option("--text", default=None, help="Exact text to post (skips the AI).")
@click.option(
    "--photo",
    type=click.Path(exists=True),
    default=None,
    help="Photo to attach (AI writes the caption if no text given).",
)
@click.option(
    "--platform",
    "platform_name",
    default=None,
    help="Platform (default: first in profile).",
)
@click.pass_obj
def post(
    ctx: Ctx,
    topic: Optional[str],
    text: Optional[str],
    photo: Optional[str],
    platform_name: Optional[str],
) -> None:
    """Compose (if needed) and publish a single post right now."""
    p = ctx.profile
    if not any((topic, text, photo)):
        raise click.ClickException("give me --topic, --text, or --photo")
    platform_name = platform_name or (p.platforms[0] if p.platforms else "twitter")
    plat = ctx.platform(platform_name)

    if text is None:
        if photo and not topic:
            click.echo("Writing a caption for the photo...")
            text = content_mod.caption_photo(
                ctx.jarvis,
                p,
                Path(photo),
                char_limit=plat.char_limit,
                model=ctx.model,
            )
        else:
            click.echo(f"Writing a post about: {topic}")
            text = content_mod.generate_post(
                ctx.jarvis,
                p,
                topic=topic,
                char_limit=plat.char_limit,
                model=ctx.model,
            )
    record = ctx.store.add_post(
        Post(
            platform=platform_name,
            status=STATUS_SCHEDULED,
            text=text,
            image_path=photo,
            topic=topic,
            scheduled_at=_iso(_now_utc()),
        )
    )
    _publish(ctx, record, plat)


# ---------------------------------------------------------------------------
# run (the cron entry point)
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_obj
def run(ctx: Ctx) -> None:
    """Publish every scheduled post whose time has come. Cron this."""
    due = ctx.store.due_posts()
    if not due:
        click.echo("Nothing due. Calendar is up to date.")
        return
    click.echo(f"{len(due)} post(s) due.")
    plats: Dict[str, Any] = {}
    for record in due:
        if record.platform not in plats:
            try:
                plats[record.platform] = ctx.platform(record.platform)
            except platforms_mod.PlatformError as exc:
                ctx.store.mark_failed(record.id, str(exc))
                click.echo(f"  [fail] {record.id}: {exc}")
                continue
        plat = plats[record.platform]
        if not record.text:
            if record.image_path:
                record.text = content_mod.caption_photo(
                    ctx.jarvis,
                    ctx.profile,
                    Path(record.image_path),
                    char_limit=plat.char_limit,
                    model=ctx.model,
                )
            else:
                record.text = content_mod.generate_post(
                    ctx.jarvis,
                    ctx.profile,
                    topic=record.topic or ctx.profile.niche,
                    char_limit=plat.char_limit,
                    model=ctx.model,
                )
            ctx.store.update_text(record.id, record.text)
        _publish(ctx, record, plat)


def _publish(ctx: Ctx, record: Post, plat) -> None:
    """Publish one record, persist the outcome, archive the photo."""
    if record.image_path and not Path(record.image_path).exists():
        ctx.store.mark_failed(record.id, f"photo missing: {record.image_path}")
        click.echo(f"  [fail] {record.id}: photo missing {record.image_path}")
        return
    result = plat.publish(record.text or "", record.image_path)
    if result.ok:
        ctx.store.mark_published(record.id, result.remote_id, result.url)
        click.echo(
            f"  [ok] {record.platform} {record.id} -> {result.url or result.remote_id}"
        )
        _archive_photo(ctx, record)
    else:
        ctx.store.mark_failed(record.id, result.error)
        click.echo(f"  [fail] {record.id}: {result.error}")


def _archive_photo(ctx: Ctx, record: Post) -> None:
    """Move a published queue photo to ``posted/`` so it never reposts."""
    if ctx.dry_run or not record.image_path or not ctx.profile.photo_queue:
        return
    photo = Path(record.image_path)
    queue = Path(ctx.profile.photo_queue).expanduser()
    try:
        if photo.parent.resolve() != queue.resolve():
            return
        archive = queue / "posted"
        archive.mkdir(exist_ok=True)
        shutil.move(str(photo), str(archive / photo.name))
        sidecar = photo.with_suffix(".txt")
        if sidecar.exists():
            shutil.move(str(sidecar), str(archive / sidecar.name))
    except OSError as exc:
        click.echo(f"  [warn] could not archive {photo.name}: {exc}")


# ---------------------------------------------------------------------------
# report (evaluation)
# ---------------------------------------------------------------------------


@cli.command()
@click.option("--days", default=7, show_default=True, help="Evaluation window in days.")
@click.pass_obj
def report(ctx: Ctx, days: int) -> None:
    """Refresh engagement metrics and write a performance evaluation."""
    since = _iso(_now_utc() - timedelta(days=days))
    posts = ctx.store.published_since(since)
    if not posts:
        click.echo(f"No published posts in the last {days} days — nothing to evaluate.")
        return

    click.echo(f"Refreshing metrics for {len(posts)} post(s)...")
    plats: Dict[str, Any] = {}
    history = []
    for record in posts:
        if record.platform not in plats:
            try:
                plats[record.platform] = ctx.platform(record.platform)
            except platforms_mod.PlatformError:
                plats[record.platform] = None
        plat = plats[record.platform]
        if plat is not None and record.remote_id:
            metrics = plat.fetch_metrics(record.remote_id)
            if metrics:
                ctx.store.record_metrics(record.id, **metrics)
        history.append(
            {
                "topic": record.topic,
                "text": (record.text or "")[:120],
                "platform": record.platform,
                "published_at": record.published_at,
                "had_photo": bool(record.image_path),
                "metrics": ctx.store.latest_metrics(record.id) or {},
            }
        )

    totals = ctx.store.totals_since(since)
    click.echo(
        f"\nTotals (last {days}d): {totals['posts']} posts, "
        f"{totals['likes']} likes, {totals['shares']} shares, "
        f"{totals['replies']} replies, {totals['impressions']} impressions\n"
    )
    click.echo("Asking the model for the evaluation...\n")
    click.echo(
        content_mod.evaluate_performance(
            ctx.jarvis, ctx.profile, history, totals, model=ctx.model
        )
    )


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


@cli.command()
@click.pass_obj
def status(ctx: Ctx) -> None:
    """Show everything the agent is tracking: calendar, history, trends."""
    p = ctx.profile
    click.echo(
        f"Profile: {p.name}  |  niche: {p.niche}  |  "
        f"platforms: {', '.join(p.platforms)}"
    )
    photos = p.queued_photos()
    click.echo(
        f"Photo queue: {len(photos)} waiting"
        + (f" (next: {photos[0].name})" if photos else "")
    )

    click.echo("\nUpcoming calendar:")
    upcoming = ctx.store.upcoming_posts()
    if not upcoming:
        click.echo("  (empty — run `plan` to fill it)")
    for record in upcoming:
        photo_tag = " [+photo]" if record.image_path else ""
        click.echo(
            f"  {record.scheduled_at}  {record.platform}  "
            f"{(record.topic or record.text or '')[:60]}{photo_tag}"
        )

    click.echo("\nRecent posts:")
    recent = ctx.store.recent_posts(10)
    if not recent:
        click.echo("  (none yet)")
    for record in recent:
        metrics = ctx.store.latest_metrics(record.id)
        metrics_tag = (
            f"  ♥{metrics['likes']} ↻{metrics['shares']} 💬{metrics['replies']}"
            if metrics
            else ""
        )
        flag = "✓" if record.status == "published" else "✗"
        click.echo(
            f"  {flag} {record.published_at or record.created_at}  "
            f"{record.platform}  "
            f"{(record.text or record.topic or '')[:55]}{metrics_tag}"
        )
        if record.error:
            click.echo(f"      error: {record.error[:80]}")

    trends_seen = ctx.store.recent_trends(8)
    if trends_seen:
        click.echo("\nLatest ranked trends:")
        for t in trends_seen:
            click.echo(f"  [{t['relevance']:4.1f}] {t['topic'][:70]}")

    week_ago = _iso(_now_utc() - timedelta(days=7))
    totals = ctx.store.totals_since(week_ago)
    click.echo(
        f"\nLast 7 days: {totals['posts']} posts · {totals['likes']} likes · "
        f"{totals['shares']} shares · {totals['replies']} replies · "
        f"{totals['impressions']} impressions"
    )


# ---------------------------------------------------------------------------
# register-schedules
# ---------------------------------------------------------------------------


@cli.command("register-schedules")
@click.pass_obj
def register_schedules(ctx: Ctx) -> None:
    """Register the automation loop with the OpenJarvis scheduler."""
    from openjarvis.scheduler import TaskScheduler
    from openjarvis.scheduler.store import SchedulerStore

    script = Path(__file__).resolve()
    scheduler = TaskScheduler(SchedulerStore())
    jobs = [
        (f"Run: python {script} run", "*/30 * * * *", "publish due posts every 30 min"),
        (
            f"Run: python {script} plan --days 7",
            "0 8 * * 1",
            "plan the week every Monday 8:00",
        ),
        (
            f"Run: python {script} report --days 7",
            "0 9 * * 0",
            "weekly evaluation every Sunday 9:00",
        ),
    ]
    for prompt, cron, label in jobs:
        task = scheduler.create_task(
            prompt=prompt,
            schedule_type="cron",
            schedule_value=cron,
            agent="orchestrator",
            tools="shell_exec",
        )
        click.echo(f"  registered [{task.id}] {label} ({cron})")
    click.echo(
        "\nStart the daemon with: jarvis scheduler start\n"
        "Prefer plain cron? Equivalent crontab lines:\n"
        f"  */30 * * * * python {script} run\n"
        f"  0 8 * * 1    python {script} plan --days 7\n"
        f"  0 9 * * 0    python {script} report --days 7"
    )


if __name__ == "__main__":
    cli()
