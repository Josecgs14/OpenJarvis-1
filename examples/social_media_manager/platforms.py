"""Platform adapters — publish posts (text + photo) and fetch engagement.

Each adapter exposes the same tiny surface so the manager stays
platform-agnostic:

    publish(text, image_path=None) -> PostResult
    fetch_metrics(remote_id)       -> dict | None
    char_limit                     -> int

Built-in adapters:

* ``TwitterPlatform`` — X/Twitter API v2 (media upload via v1.1),
  reusing the OAuth 1.0a signer from ``openjarvis.channels.twitter_channel``.
* ``MastodonPlatform`` — via Mastodon.py (supports media attachments).
* ``DryRunPlatform`` — prints what would be posted; used by ``--dry-run``
  and demo mode so the agent is testable without any credentials.
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class PostResult:
    ok: bool
    remote_id: str = ""
    url: str = ""
    error: str = ""


class PlatformError(RuntimeError):
    """Raised when a platform can't be constructed (missing creds/deps)."""


class TwitterPlatform:
    """X/Twitter adapter. Posts tweets with optional photo attachment.

    Required env vars (same as ``TwitterChannel``):
      TWITTER_API_KEY, TWITTER_API_SECRET,
      TWITTER_ACCESS_TOKEN, TWITTER_ACCESS_SECRET
    Optional (read-side metrics): TWITTER_BEARER_TOKEN
    """

    name = "twitter"
    char_limit = 280

    _UPLOAD_URL = "https://upload.twitter.com/1.1/media/upload.json"
    _TWEET_URL = "https://api.twitter.com/2/tweets"

    def __init__(self) -> None:
        key = os.environ.get("TWITTER_API_KEY", "")
        secret = os.environ.get("TWITTER_API_SECRET", "")
        token = os.environ.get("TWITTER_ACCESS_TOKEN", "")
        token_secret = os.environ.get("TWITTER_ACCESS_SECRET", "")
        if not all((key, secret, token, token_secret)):
            raise PlatformError(
                "Twitter credentials missing — set TWITTER_API_KEY,"
                " TWITTER_API_SECRET, TWITTER_ACCESS_TOKEN,"
                " TWITTER_ACCESS_SECRET"
            )
        from openjarvis.channels.twitter_channel import _OAuth1Auth

        self._auth = _OAuth1Auth(key, secret, token, token_secret)
        self._bearer = os.environ.get("TWITTER_BEARER_TOKEN", "")

    def _upload_media(self, image_path: str) -> str:
        import httpx

        path = Path(image_path)
        mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
        with path.open("rb") as f:
            resp = httpx.post(
                self._UPLOAD_URL,
                files={"media": (path.name, f, mime)},
                auth=self._auth,
                timeout=60.0,
            )
        resp.raise_for_status()
        return str(resp.json()["media_id_string"])

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        import httpx

        try:
            payload: Dict = {"text": text}
            if image_path:
                payload["media"] = {"media_ids": [self._upload_media(image_path)]}
            resp = httpx.post(
                self._TWEET_URL, json=payload, auth=self._auth, timeout=30.0
            )
            if resp.status_code not in (200, 201):
                return PostResult(
                    ok=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )
            tweet_id = resp.json()["data"]["id"]
            return PostResult(
                ok=True,
                remote_id=tweet_id,
                url=f"https://x.com/i/status/{tweet_id}",
            )
        except Exception as exc:  # network errors, bad media, etc.
            return PostResult(ok=False, error=str(exc))

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        if not self._bearer:
            return None
        import httpx

        try:
            resp = httpx.get(
                f"https://api.twitter.com/2/tweets/{remote_id}",
                params={"tweet.fields": "public_metrics"},
                headers={"Authorization": f"Bearer {self._bearer}"},
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            pm = resp.json()["data"]["public_metrics"]
            return {
                "likes": pm.get("like_count", 0),
                "shares": pm.get("retweet_count", 0) + pm.get("quote_count", 0),
                "replies": pm.get("reply_count", 0),
                "impressions": pm.get("impression_count", 0),
            }
        except Exception:
            return None


class MastodonPlatform:
    """Mastodon adapter via Mastodon.py (``pip install Mastodon.py``).

    Required env vars: MASTODON_API_BASE_URL, MASTODON_ACCESS_TOKEN
    """

    name = "mastodon"
    char_limit = 500

    def __init__(self) -> None:
        base_url = os.environ.get("MASTODON_API_BASE_URL", "")
        token = os.environ.get("MASTODON_ACCESS_TOKEN", "")
        if not base_url or not token:
            raise PlatformError(
                "Mastodon credentials missing — set MASTODON_API_BASE_URL"
                " and MASTODON_ACCESS_TOKEN"
            )
        try:
            from mastodon import Mastodon
        except ImportError as exc:
            raise PlatformError(
                "Mastodon.py not installed — pip install Mastodon.py"
            ) from exc
        self._client = Mastodon(access_token=token, api_base_url=base_url)

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        try:
            media_ids = None
            if image_path:
                media = self._client.media_post(image_path)
                media_ids = [media["id"]]
            status = self._client.status_post(text, media_ids=media_ids)
            return PostResult(
                ok=True,
                remote_id=str(status["id"]),
                url=status.get("url", ""),
            )
        except Exception as exc:
            return PostResult(ok=False, error=str(exc))

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        try:
            status = self._client.status(remote_id)
            return {
                "likes": status.get("favourites_count", 0),
                "shares": status.get("reblogs_count", 0),
                "replies": status.get("replies_count", 0),
                "impressions": 0,  # not exposed by the Mastodon API
            }
        except Exception:
            return None


class DryRunPlatform:
    """Prints the post instead of publishing. No credentials needed.

    ``fetch_metrics`` returns deterministic pseudo-metrics derived from
    the remote id so demo reports have non-zero numbers to evaluate.
    """

    char_limit = 280

    def __init__(self, name: str = "dry-run") -> None:
        self.name = name
        self._counter = 0

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        self._counter += 1
        print("  ┌── DRY-RUN: would publish ──")
        print(f"  │  platform: {self.name}")
        if image_path:
            print(f"  │  photo: {image_path}")
        print(f"  │  text ({len(text)} chars): {text}")
        print("  └────────────────────────────")
        return PostResult(ok=True, remote_id=f"dryrun-{self._counter}")

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        seed = int(hashlib.sha256(remote_id.encode()).hexdigest()[:8], 16)
        return {
            "likes": seed % 50,
            "shares": seed % 13,
            "replies": seed % 7,
            "impressions": 200 + seed % 1800,
        }


_PLATFORMS = {
    "twitter": TwitterPlatform,
    "x": TwitterPlatform,
    "mastodon": MastodonPlatform,
}


def get_platform(name: str, *, dry_run: bool = False):
    """Build the adapter for *name*; wrap as dry-run when asked.

    In dry-run mode no credentials are required — the returned adapter
    only prints.
    """
    key = name.lower().strip()
    if dry_run:
        plat = DryRunPlatform(name=key)
        cls = _PLATFORMS.get(key)
        if cls is not None:
            plat.char_limit = cls.char_limit
        return plat
    cls = _PLATFORMS.get(key)
    if cls is None:
        raise PlatformError(
            f"Unknown platform {name!r}. Available: {sorted(set(_PLATFORMS))}"
        )
    return cls()
