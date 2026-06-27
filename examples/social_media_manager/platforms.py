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
* ``FacebookPlatform`` — Facebook Page posts via the Meta Graph API
  (text and photos; local files or public URLs).
* ``InstagramPlatform`` — Instagram professional accounts via the Meta
  Graph API (photo posts only; images must be public URLs).
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

GRAPH_API = "https://graph.facebook.com/v21.0"


def is_url(path: str) -> bool:
    return path.startswith(("http://", "https://"))


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


class FacebookPlatform:
    """Facebook Page adapter via the Meta Graph API.

    Posts to a Page you manage (personal profiles can't be automated).
    Photos can be local files (uploaded directly) or public URLs.

    Required env vars:
      FACEBOOK_PAGE_ID, FACEBOOK_PAGE_ACCESS_TOKEN
    (a Page access token with pages_manage_posts; add read_insights
    for impression metrics)
    """

    name = "facebook"
    # Generation cap, not the API maximum (63k) — keeps posts readable.
    char_limit = 2200

    def __init__(self) -> None:
        self._page_id = os.environ.get("FACEBOOK_PAGE_ID", "")
        self._token = os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "")
        if not self._page_id or not self._token:
            raise PlatformError(
                "Facebook credentials missing — set FACEBOOK_PAGE_ID"
                " and FACEBOOK_PAGE_ACCESS_TOKEN (a Page access token)"
            )

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        import httpx

        try:
            if image_path:
                data = {"message": text, "access_token": self._token}
                if is_url(image_path):
                    data["url"] = image_path
                    resp = httpx.post(
                        f"{GRAPH_API}/{self._page_id}/photos",
                        data=data,
                        timeout=60.0,
                    )
                else:
                    path = Path(image_path)
                    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
                    with path.open("rb") as f:
                        resp = httpx.post(
                            f"{GRAPH_API}/{self._page_id}/photos",
                            data=data,
                            files={"source": (path.name, f, mime)},
                            timeout=120.0,
                        )
            else:
                resp = httpx.post(
                    f"{GRAPH_API}/{self._page_id}/feed",
                    data={"message": text, "access_token": self._token},
                    timeout=30.0,
                )
            if resp.status_code != 200:
                return PostResult(
                    ok=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            # /photos returns {"id": photo_id, "post_id": page_postid};
            # the feed post id is the one metrics are read from.
            post_id = body.get("post_id") or body.get("id", "")
            return PostResult(
                ok=True,
                remote_id=post_id,
                url=f"https://www.facebook.com/{post_id}",
            )
        except Exception as exc:
            return PostResult(ok=False, error=str(exc))

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        import httpx

        try:
            resp = httpx.get(
                f"{GRAPH_API}/{remote_id}",
                params={
                    "fields": "reactions.summary(true),comments.summary(true),shares",
                    "access_token": self._token,
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            metrics = {
                "likes": body.get("reactions", {})
                .get("summary", {})
                .get("total_count", 0),
                "shares": body.get("shares", {}).get("count", 0),
                "replies": body.get("comments", {})
                .get("summary", {})
                .get("total_count", 0),
                "impressions": 0,
            }
            # Impressions need the read_insights permission; best-effort.
            ins = httpx.get(
                f"{GRAPH_API}/{remote_id}/insights",
                params={
                    "metric": "post_impressions",
                    "access_token": self._token,
                },
                timeout=15.0,
            )
            if ins.status_code == 200:
                for entry in ins.json().get("data", []):
                    values = entry.get("values") or [{}]
                    metrics["impressions"] = int(values[0].get("value") or 0)
            return metrics
        except Exception:
            return None


class InstagramPlatform:
    """Instagram adapter via the Meta Graph API (content publishing).

    Needs an Instagram *professional* (business/creator) account linked
    to a Facebook Page. Two API constraints to know about:

    * every post MUST have a photo (no text-only posts), and
    * the photo must be a publicly accessible URL — Meta fetches it
      server-side. Local files are rejected with a clear error; host
      the image somewhere public (or pass an https:// URL as the photo).

    Required env vars:
      INSTAGRAM_USER_ID (the IG professional account id),
      INSTAGRAM_ACCESS_TOKEN (a token with instagram_content_publish)
    """

    name = "instagram"
    char_limit = 2200

    def __init__(self) -> None:
        self._user_id = os.environ.get("INSTAGRAM_USER_ID", "")
        self._token = os.environ.get("INSTAGRAM_ACCESS_TOKEN", "")
        if not self._user_id or not self._token:
            raise PlatformError(
                "Instagram credentials missing — set INSTAGRAM_USER_ID"
                " and INSTAGRAM_ACCESS_TOKEN"
            )

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        if not image_path:
            return PostResult(
                ok=False,
                error="Instagram requires a photo — schedule this post"
                " with an image or use another platform",
            )
        if not is_url(image_path):
            return PostResult(
                ok=False,
                error="Instagram's API only accepts publicly accessible"
                f" image URLs, got local file {image_path!r} — upload it"
                " somewhere public and use the https:// URL",
            )
        import httpx

        try:
            create = httpx.post(
                f"{GRAPH_API}/{self._user_id}/media",
                data={
                    "image_url": image_path,
                    "caption": text,
                    "access_token": self._token,
                },
                timeout=60.0,
            )
            if create.status_code != 200:
                return PostResult(
                    ok=False,
                    error=f"container HTTP {create.status_code}: {create.text[:200]}",
                )
            creation_id = create.json()["id"]
            publish = httpx.post(
                f"{GRAPH_API}/{self._user_id}/media_publish",
                data={"creation_id": creation_id, "access_token": self._token},
                timeout=60.0,
            )
            if publish.status_code != 200:
                return PostResult(
                    ok=False,
                    error=f"publish HTTP {publish.status_code}: {publish.text[:200]}",
                )
            media_id = publish.json()["id"]
            permalink = ""
            link = httpx.get(
                f"{GRAPH_API}/{media_id}",
                params={"fields": "permalink", "access_token": self._token},
                timeout=15.0,
            )
            if link.status_code == 200:
                permalink = link.json().get("permalink", "")
            return PostResult(ok=True, remote_id=media_id, url=permalink)
        except Exception as exc:
            return PostResult(ok=False, error=str(exc))

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        import httpx

        try:
            resp = httpx.get(
                f"{GRAPH_API}/{remote_id}",
                params={
                    "fields": "like_count,comments_count",
                    "access_token": self._token,
                },
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            body = resp.json()
            metrics = {
                "likes": body.get("like_count", 0),
                "shares": 0,  # not exposed for IG media
                "replies": body.get("comments_count", 0),
                "impressions": 0,
            }
            # views replaced impressions in recent API versions; try both.
            for metric_name in ("views", "impressions"):
                ins = httpx.get(
                    f"{GRAPH_API}/{remote_id}/insights",
                    params={"metric": metric_name, "access_token": self._token},
                    timeout=15.0,
                )
                if ins.status_code == 200 and ins.json().get("data"):
                    values = ins.json()["data"][0].get("values") or [{}]
                    metrics["impressions"] = int(values[0].get("value") or 0)
                    break
            return metrics
        except Exception:
            return None


class BufferPlatform:
    """Publish through Buffer — one integration covers many networks.

    Buffer already holds the OAuth for your connected channels (Instagram,
    Facebook, TikTok, LinkedIn, ...), so the agent never touches the Meta
    Graph API or per-network tokens. You only need a Buffer access token
    and the channel id you want to post to.

    Addressed as ``buffer:<service>`` (e.g. ``buffer:facebook``). The
    channel id is read from ``BUFFER_<SERVICE>_CHANNEL_ID`` and the token
    from ``BUFFER_ACCESS_TOKEN`` (see ``get_platform``).

    Uses Buffer's REST API. By default posts are added to the channel's
    queue (the next free slot in your Buffer schedule); set
    ``BUFFER_SHARE_NOW=1`` to publish immediately instead.

    Notes / Buffer's own rules:
      * Images must be public URLs (Buffer fetches them) — same as the
        direct Instagram path. Local files are rejected.
      * Instagram and TikTok require an image/video; text-only fails.
    """

    _API = "https://api.bufferapp.com/1"

    # Buffer composes for each network; these are generous display caps.
    _LIMITS = {
        "twitter": 280,
        "facebook": 2200,
        "instagram": 2200,
        "tiktok": 2200,
        "linkedin": 3000,
        "mastodon": 500,
    }

    def __init__(self, service: str, channel_id: str, token: str) -> None:
        self.name = f"buffer:{service}"
        self.service = service
        self.char_limit = self._LIMITS.get(service, 2200)
        self._channel_id = channel_id
        self._token = token
        self._share_now = os.environ.get("BUFFER_SHARE_NOW", "").strip() in (
            "1",
            "true",
            "yes",
        )

    def publish(self, text: str, image_path: Optional[str] = None) -> PostResult:
        if image_path and not is_url(image_path):
            return PostResult(
                ok=False,
                error="Buffer needs a public image URL, not a local file"
                f" ({image_path!r}) — host it and pass the https:// URL",
            )
        if self.service in ("instagram", "tiktok") and not image_path:
            return PostResult(
                ok=False,
                error=f"{self.service} requires an image or video — give"
                " this post a photo URL",
            )
        import httpx

        data = {
            "access_token": self._token,
            "profile_ids[]": self._channel_id,
            "text": text,
            "now": "true" if self._share_now else "false",
        }
        if image_path:
            data["media[photo]"] = image_path
            data["media[picture]"] = image_path
        try:
            resp = httpx.post(
                f"{self._API}/updates/create.json", data=data, timeout=30.0
            )
            if resp.status_code != 200:
                return PostResult(
                    ok=False, error=f"HTTP {resp.status_code}: {resp.text[:200]}"
                )
            body = resp.json()
            if not body.get("success"):
                return PostResult(ok=False, error=str(body)[:200])
            updates = body.get("updates") or []
            remote_id = updates[0].get("id", "") if updates else ""
            state = "published" if self._share_now else "queued"
            return PostResult(ok=True, remote_id=remote_id, url=f"buffer:{state}")
        except Exception as exc:
            return PostResult(ok=False, error=str(exc))

    def fetch_metrics(self, remote_id: str) -> Optional[Dict[str, int]]:
        import httpx

        try:
            resp = httpx.get(
                f"{self._API}/updates/{remote_id}.json",
                params={"access_token": self._token},
                timeout=15.0,
            )
            if resp.status_code != 200:
                return None
            stats = resp.json().get("statistics") or {}
            return {
                "likes": int(stats.get("favorites", 0) or stats.get("likes", 0)),
                "shares": int(stats.get("shares", 0) or stats.get("retweets", 0)),
                "replies": int(stats.get("comments", 0) or stats.get("mentions", 0)),
                "impressions": int(stats.get("reach", 0) or stats.get("clicks", 0)),
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
    "facebook": FacebookPlatform,
    "instagram": InstagramPlatform,
}


def _buffer_service(key: str) -> Optional[str]:
    """Return the service name for a ``buffer:<service>`` key, else None."""
    for sep in (":", "_"):
        prefix = f"buffer{sep}"
        if key.startswith(prefix):
            return key[len(prefix) :]
    return None


def get_platform(name: str, *, dry_run: bool = False):
    """Build the adapter for *name*; wrap as dry-run when asked.

    Accepts direct networks (``facebook``, ``instagram``, ...) and Buffer
    routes (``buffer:facebook``, ``buffer:instagram``, ...). In dry-run
    mode no credentials are required — the returned adapter only prints.
    """
    key = name.lower().strip()
    service = _buffer_service(key)

    if dry_run:
        plat = DryRunPlatform(name=key)
        if service is not None:
            plat.char_limit = BufferPlatform._LIMITS.get(service, 2200)
        else:
            cls = _PLATFORMS.get(key)
            if cls is not None:
                plat.char_limit = cls.char_limit
        return plat

    if service is not None:
        token = os.environ.get("BUFFER_ACCESS_TOKEN", "")
        channel_id = os.environ.get(f"BUFFER_{service.upper()}_CHANNEL_ID", "")
        if not token:
            raise PlatformError(
                "Buffer credentials missing — set BUFFER_ACCESS_TOKEN"
                " (generate at publish.buffer.com/settings/api)"
            )
        if not channel_id:
            raise PlatformError(
                f"No channel id for {name!r} — set "
                f"BUFFER_{service.upper()}_CHANNEL_ID (from `jarvis` or "
                "Buffer's channel list)"
            )
        return BufferPlatform(service, channel_id, token)

    cls = _PLATFORMS.get(key)
    if cls is None:
        raise PlatformError(
            f"Unknown platform {name!r}. Available: "
            f"{sorted(set(_PLATFORMS))} or buffer:<service>"
        )
    return cls()
