"""Cross-pod skill ACL cache invalidation via Redis pub/sub.

When any pod publishes, edits, or deletes a skill it calls notify_skills_changed().
Every pod runs a background subscriber that calls pull_from_store() + reload_manifest()
the moment it receives the invalidation message — giving near-instant consistency
across all replicas without waiting for the 60-second periodic sync.

Channel: skills:invalidate
Message: JSON  {"action": "publish"|"edit"|"delete", "slug": "<slug>"}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os

logger = logging.getLogger(__name__)

_CHANNEL = "skills:invalidate"

_REDIS_HOST = os.environ.get("REDIS_HOST", "").strip()
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))
_REDIS_DB = int(os.environ.get("REDIS_DB", "0"))


def _make_client():
    import redis.asyncio as aioredis  # type: ignore
    if not _REDIS_HOST:
        raise RuntimeError("REDIS_HOST not configured")
    return aioredis.Redis(host=_REDIS_HOST, port=_REDIS_PORT, db=_REDIS_DB, decode_responses=True)


# ── Publisher (called by api_skill_publish.py) ─────────────────────────────────

def notify_skills_changed(action: str, slug: str) -> None:
    """Fire-and-forget: publish an invalidation event to all pods.

    Safe to call from sync code (spawns a short-lived thread).
    Failures are logged but never raise — a failed notify just means
    pods will catch up at the next 60-second poll.
    """
    import threading

    def _publish():
        import asyncio as _asyncio
        try:
            _asyncio.run(_publish_async(action, slug))
        except Exception:
            logger.exception("skills_invalidate: failed to publish notify for '%s'", slug)

    t = threading.Thread(target=_publish, daemon=True)
    t.start()


async def _publish_async(action: str, slug: str) -> None:
    client = _make_client()
    try:
        msg = json.dumps({"action": action, "slug": slug})
        await client.publish(_CHANNEL, msg)
        logger.info("skills_invalidate: published %s for '%s'", action, slug)
    finally:
        await client.aclose()


# ── Subscriber (started in main.py lifespan) ───────────────────────────────────

async def run_subscriber() -> None:
    """Long-running async task: subscribe and reload manifest on every message.

    Reconnects automatically on connection loss with exponential back-off.
    """
    backoff = 1
    while True:
        try:
            client = _make_client()
            pubsub = client.pubsub()
            await pubsub.subscribe(_CHANNEL)
            logger.info("skills_invalidate: subscribed to %s on %s:%s", _CHANNEL, _REDIS_HOST, _REDIS_PORT)
            backoff = 1
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                try:
                    data = json.loads(message["data"])
                    action = data.get("action", "?")
                    slug = data.get("slug", "?")
                    logger.info("skills_invalidate: received %s for '%s' — reloading ACL", action, slug)
                    from skills_store import pull_from_store
                    pull_from_store()
                except Exception:
                    logger.exception("skills_invalidate: error handling message")
        except asyncio.CancelledError:
            logger.info("skills_invalidate: subscriber cancelled")
            return
        except Exception:
            logger.exception(
                "skills_invalidate: subscriber error — reconnecting in %ds", backoff
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)
