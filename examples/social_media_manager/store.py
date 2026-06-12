"""SQLite state store for the social media manager.

Tracks everything the agent does: the content calendar (scheduled posts),
publish history, engagement metrics snapshots, and the trend log. One
file on disk (default ``~/.openjarvis/social_media_manager.db``) is the
single source of truth, so the agent survives restarts and cron runs.
"""

from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

DEFAULT_DB_PATH = Path.home() / ".openjarvis" / "social_media_manager.db"

# Post lifecycle: draft -> scheduled -> published | failed
STATUS_DRAFT = "draft"
STATUS_SCHEDULED = "scheduled"
STATUS_PUBLISHED = "published"
STATUS_FAILED = "failed"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id           TEXT PRIMARY KEY,
    platform     TEXT NOT NULL,
    status       TEXT NOT NULL,
    text         TEXT,
    image_path   TEXT,
    topic        TEXT,
    scheduled_at TEXT,
    published_at TEXT,
    remote_id    TEXT,
    remote_url   TEXT,
    error        TEXT,
    created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS metrics (
    post_id     TEXT NOT NULL REFERENCES posts(id),
    fetched_at  TEXT NOT NULL,
    likes       INTEGER NOT NULL DEFAULT 0,
    shares      INTEGER NOT NULL DEFAULT 0,
    replies     INTEGER NOT NULL DEFAULT 0,
    impressions INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS trends (
    id         TEXT PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    source     TEXT NOT NULL,
    topic      TEXT NOT NULL,
    relevance  REAL NOT NULL DEFAULT 0,
    used       INTEGER NOT NULL DEFAULT 0
);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Post:
    """One entry in the content calendar / publish history."""

    platform: str
    status: str = STATUS_DRAFT
    text: Optional[str] = None
    image_path: Optional[str] = None
    topic: Optional[str] = None
    scheduled_at: Optional[str] = None
    published_at: Optional[str] = None
    remote_id: Optional[str] = None
    remote_url: Optional[str] = None
    error: Optional[str] = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: str = field(default_factory=_now_iso)

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "Post":
        return cls(**{k: row[k] for k in row.keys()})


class Store:
    """Persistence layer. All timestamps are ISO-8601 UTC strings."""

    def __init__(self, db_path: Path | str = DEFAULT_DB_PATH) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Store":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Posts / calendar
    # ------------------------------------------------------------------

    def add_post(self, post: Post) -> Post:
        self._conn.execute(
            "INSERT INTO posts (id, platform, status, text, image_path, topic,"
            " scheduled_at, published_at, remote_id, remote_url, error, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                post.id,
                post.platform,
                post.status,
                post.text,
                post.image_path,
                post.topic,
                post.scheduled_at,
                post.published_at,
                post.remote_id,
                post.remote_url,
                post.error,
                post.created_at,
            ),
        )
        self._conn.commit()
        return post

    def get_post(self, post_id: str) -> Optional[Post]:
        row = self._conn.execute(
            "SELECT * FROM posts WHERE id = ?", (post_id,)
        ).fetchone()
        return Post.from_row(row) if row else None

    def due_posts(self, now: Optional[str] = None) -> List[Post]:
        """Scheduled posts whose time has come (oldest first)."""
        now = now or _now_iso()
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status = ? AND scheduled_at <= ?"
            " ORDER BY scheduled_at",
            (STATUS_SCHEDULED, now),
        ).fetchall()
        return [Post.from_row(r) for r in rows]

    def upcoming_posts(self, limit: int = 20) -> List[Post]:
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status = ? ORDER BY scheduled_at LIMIT ?",
            (STATUS_SCHEDULED, limit),
        ).fetchall()
        return [Post.from_row(r) for r in rows]

    def recent_posts(self, limit: int = 20) -> List[Post]:
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status IN (?, ?)"
            " ORDER BY COALESCE(published_at, created_at) DESC LIMIT ?",
            (STATUS_PUBLISHED, STATUS_FAILED, limit),
        ).fetchall()
        return [Post.from_row(r) for r in rows]

    def published_since(self, since_iso: str) -> List[Post]:
        rows = self._conn.execute(
            "SELECT * FROM posts WHERE status = ? AND published_at >= ?"
            " ORDER BY published_at",
            (STATUS_PUBLISHED, since_iso),
        ).fetchall()
        return [Post.from_row(r) for r in rows]

    def update_text(self, post_id: str, text: str) -> None:
        self._conn.execute("UPDATE posts SET text = ? WHERE id = ?", (text, post_id))
        self._conn.commit()

    def mark_published(
        self, post_id: str, remote_id: str, remote_url: Optional[str] = None
    ) -> None:
        self._conn.execute(
            "UPDATE posts SET status = ?, published_at = ?, remote_id = ?,"
            " remote_url = ?, error = NULL WHERE id = ?",
            (STATUS_PUBLISHED, _now_iso(), remote_id, remote_url, post_id),
        )
        self._conn.commit()

    def mark_failed(self, post_id: str, error: str) -> None:
        self._conn.execute(
            "UPDATE posts SET status = ?, error = ? WHERE id = ?",
            (STATUS_FAILED, error[:500], post_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Metrics
    # ------------------------------------------------------------------

    def record_metrics(
        self,
        post_id: str,
        *,
        likes: int = 0,
        shares: int = 0,
        replies: int = 0,
        impressions: int = 0,
    ) -> None:
        self._conn.execute(
            "INSERT INTO metrics (post_id, fetched_at, likes, shares,"
            " replies, impressions) VALUES (?, ?, ?, ?, ?, ?)",
            (post_id, _now_iso(), likes, shares, replies, impressions),
        )
        self._conn.commit()

    def latest_metrics(self, post_id: str) -> Optional[Dict[str, int]]:
        row = self._conn.execute(
            "SELECT likes, shares, replies, impressions FROM metrics"
            " WHERE post_id = ? ORDER BY fetched_at DESC, rowid DESC LIMIT 1",
            (post_id,),
        ).fetchone()
        return dict(row) if row else None

    def totals_since(self, since_iso: str) -> Dict[str, int]:
        """Aggregate latest-snapshot metrics for posts published since a date."""
        totals = {"posts": 0, "likes": 0, "shares": 0, "replies": 0, "impressions": 0}
        for post in self.published_since(since_iso):
            totals["posts"] += 1
            m = self.latest_metrics(post.id)
            if m:
                for key in ("likes", "shares", "replies", "impressions"):
                    totals[key] += m[key]
        return totals

    # ------------------------------------------------------------------
    # Trends
    # ------------------------------------------------------------------

    def log_trends(self, source: str, topics: List[tuple[str, float]]) -> None:
        """Record ranked (topic, relevance) pairs from one fetch."""
        now = _now_iso()
        self._conn.executemany(
            "INSERT INTO trends (id, fetched_at, source, topic, relevance)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (uuid.uuid4().hex[:12], now, source, topic, relevance)
                for topic, relevance in topics
            ],
        )
        self._conn.commit()

    def recent_trends(self, limit: int = 15) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM trends ORDER BY fetched_at DESC, relevance DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
