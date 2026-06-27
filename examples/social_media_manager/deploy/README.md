# Running the agent 24/7

The agent has two layers of automation:

1. **Buffer publishes on its own (cloud).** When the agent sends a post to
   your Buffer queue, Buffer publishes it at the scheduled time even if your
   machine is off. This layer is always automatic.
2. **The agent generates the content.** This is a program that has to run
   somewhere on a schedule. The options below are different homes for it.

Whichever you pick, fill in `../.env` (tokens) and `../profile.toml` (brand)
first — see the main [README](../README.md).

> **LLM engine:** content generation needs a model. On a server with no GPU,
> use a **cloud engine** (`--engine cloud`) and set the matching API key in
> `.env` — that's what every option below assumes. With a GPU box you can run
> Ollama locally instead and drop the `--engine` flag.

---

## Option A — GitHub Actions (free, no server to manage)

Best if you don't want to pay for or babysit a server. GitHub runs the agent
on its own machines on a schedule.

1. Copy [`github-actions.example.yml`](github-actions.example.yml) into **your
   own repo** at `.github/workflows/social-media-agent.yml`.
2. Add your tokens as repository **Secrets** (Settings → Secrets and variables
   → Actions): `BUFFER_ACCESS_TOKEN`, `BUFFER_FACEBOOK_CHANNEL_ID`,
   `BUFFER_INSTAGRAM_CHANNEL_ID`, and your cloud LLM key.
3. Done. It plans weekly and publishes every 30 minutes, filling your Buffer
   queue. Trigger a test run anytime with the **Run workflow** button.

Trade-off: GitHub's free cron is UTC and can lag a few minutes — fine for
queue-based posting, not for to-the-second timing.

## Option B — Docker on a VPS (full control)

Best if you want a dedicated always-on box (a $5/mo VPS is plenty for a
cloud engine).

```bash
# on the server, from the OpenJarvis repo root:
cp examples/social_media_manager/.env.example examples/social_media_manager/.env
# edit .env (tokens) and create examples/social_media_manager/profile.toml

docker compose -f examples/social_media_manager/deploy/docker-compose.yml up -d --build
```

That's it — the container runs forever (`restart: always`), publishing due
posts every 30 min, planning weekly, reporting weekly. State persists in the
`smm_data` volume. Check logs with:

```bash
docker compose -f examples/social_media_manager/deploy/docker-compose.yml logs -f
```

Tune cadence via env vars in `docker-compose.yml` (`RUN_EVERY_MIN`,
`PLAN_DAYS`, `PLAN_HOUR`, `TZ`, …) — see
[`scheduler_loop.py`](scheduler_loop.py) for all of them.

## Option C — bare VPS with cron (no Docker)

If you'd rather not use Docker, install the package and add cron lines:

```bash
pip install openjarvis click httpx
crontab -e
```

```cron
# publish due posts every 30 min
*/30 * * * * cd /path/to/examples/social_media_manager && python social_media_manager.py --engine cloud run
# plan the week, Mondays 8am
0 8 * * 1   cd /path/to/examples/social_media_manager && python social_media_manager.py --engine cloud plan --days 7
# weekly report, Sundays 9am
0 9 * * 0   cd /path/to/examples/social_media_manager && python social_media_manager.py --engine cloud report --days 7
```

The agent loads `.env` automatically, so cron doesn't need the tokens inline.

---

## Sanity check before going live

Run once by hand in dry-run (prints, never posts) to confirm the wiring:

```bash
python social_media_manager.py --dry-run plan --days 3
python social_media_manager.py --dry-run run
```

Then drop `--dry-run` and let the schedule take over.
