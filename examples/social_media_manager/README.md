# Social Media Manager Agent

An AI agent that runs your social media accounts end to end, on your own
machine:

- **Posts messages and photos** to X/Twitter, Instagram, Facebook Pages, and
  Mastodon (photo captions are written by the AI).
- **Rides trends** — pulls Google Trends and Hacker News, scores each topic
  for relevance to *your* niche with the local model.
- **Plans a content calendar** — mixes trend-riding and evergreen posts and
  schedules them at your preferred hours.
- **Evaluates performance** — fetches likes/shares/replies/impressions and
  writes a weekly report with concrete recommendations.
- **Tracks everything** — calendar, publish history, metrics snapshots, and
  the trend log live in one SQLite database (`~/.openjarvis/social_media_manager.db`).

## Files

| File | Purpose |
|---|---|
| `social_media_manager.py` | CLI entry point (`trends`, `plan`, `post`, `run`, `report`, `status`) |
| `content.py` | Brand profile + LLM content generation and evaluation |
| `trends.py` | Trend fetching (Google Trends RSS, Hacker News) and niche ranking |
| `platforms.py` | Twitter/X, Instagram, Facebook, and Mastodon adapters (text + photo) and dry-run mode |
| `store.py` | SQLite state: calendar, history, metrics, trend log |
| `profile.example.toml` | Brand profile template — copy to `profile.toml` |
| `test_social_media_manager.py` | Offline tests (no network/engine needed) |

## Quick start (no credentials needed)

Everything works in `--dry-run` mode first — posts are printed, not published:

```bash
cd examples/social_media_manager
cp profile.example.toml profile.toml   # edit: your name, niche, tone, language

# What's trending for my niche?
uv run python social_media_manager.py trends

# Plan a week of content (uses queued photos automatically)
uv run python social_media_manager.py --dry-run plan --days 7

# Publish whatever is due right now
uv run python social_media_manager.py --dry-run run

# One-off post
uv run python social_media_manager.py --dry-run post --topic "our new release"
uv run python social_media_manager.py --dry-run post --photo ~/pic.jpg

# Evaluate the last week + see everything the agent tracks
uv run python social_media_manager.py --dry-run report --days 7
uv run python social_media_manager.py status
```

All commands accept `--model` / `--engine` (defaults to your OpenJarvis
config, e.g. Ollama).

## Going live

Drop `--dry-run` and provide credentials. The easiest way is a `.env` file
next to the agent — copy `.env.example` to `.env`, paste your tokens, and the
agent loads it automatically on every run (it's gitignored, never committed):

```bash
cp .env.example .env   # then edit .env with your tokens
```

Or export the variables in your shell instead:

```bash
# X / Twitter (app with read+write permissions)
export TWITTER_API_KEY=...        TWITTER_API_SECRET=...
export TWITTER_ACCESS_TOKEN=...   TWITTER_ACCESS_SECRET=...
export TWITTER_BEARER_TOKEN=...   # read-side, used for metrics

# Facebook Page (Meta Graph API — Page access token with pages_manage_posts;
# add read_insights to also get impression metrics)
export FACEBOOK_PAGE_ID=...
export FACEBOOK_PAGE_ACCESS_TOKEN=...

# Instagram (professional account linked to a Facebook Page; token with
# instagram_content_publish)
export INSTAGRAM_USER_ID=...
export INSTAGRAM_ACCESS_TOKEN=...

# Mastodon (needs: pip install Mastodon.py)
export MASTODON_API_BASE_URL=https://mastodon.social
export MASTODON_ACCESS_TOKEN=...
```

**Instagram caveats** (Meta API rules, not ours): every post must include a
photo, and the image must be a *publicly accessible URL* — Meta's servers
fetch it. So for Instagram pass `--photo https://...` (or schedule posts whose
`image_path` is a URL); local files are rejected with a clear error. Facebook
accepts both local files and URLs, and personal profiles can't be automated —
only Pages you manage.

### Photos

Point `photo_queue` in `profile.toml` at a folder. Any image you drop there
gets picked up by `plan`, captioned by the AI at publish time, posted, and
moved to `posted/` so it never goes out twice. Add an optional sidecar note
(`beach.jpg` → `beach.txt`) describing the shot to guide the caption.

### Automation

Register the loop with the OpenJarvis scheduler (or use plain cron — the
command prints equivalent crontab lines):

```bash
uv run python social_media_manager.py register-schedules
jarvis scheduler start
```

This sets up: publish due posts every 30 min · plan the week on Mondays ·
weekly evaluation report on Sundays.

## Tests

```bash
uv run pytest examples/social_media_manager/ -v
```

## En español — resumen rápido

Este agente lleva el control de tus redes sociales (X/Twitter, Instagram,
Facebook y Mastodon): detecta tendencias y las puntúa según tu nicho, planifica
un calendario de contenido, redacta y publica mensajes y fotos (les escribe el
pie de foto), mide likes/compartidos/respuestas,
te entrega un informe semanal con recomendaciones, y registra todo en una base
de datos local. Configura tu marca en `profile.toml` (idioma `es` para que
publique en español), prueba primero con `--dry-run`, y automatízalo con
`register-schedules`.
