"""Slack workspace bot/app registry.

Stores a local snapshot of all non-workflow bots in the workspace, synced from
the Slack ``users.list`` API using ``SLACK_WORKSPACE_TOKEN``.  The snapshot is
refreshed automatically when it is more than 24 hours old and on explicit admin
requests via ``POST /api/slack-apps/sync``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from sqlalchemy import Boolean, Column, DateTime, Integer, String, Text
from sqlalchemy.orm import Session

from database import Base, _uuid

logger = logging.getLogger(__name__)

_CACHE_TTL_SECONDS = 86_400  # 24 hours


# ── Model ─────────────────────────────────────────────────────────────────────

class SlackAppRecord(Base):
    __tablename__ = "slack_app_records"

    user_id      = Column(String(40), primary_key=True)
    bot_id       = Column(String(40), nullable=True)
    app_id       = Column(String(40), nullable=True, index=True)
    name         = Column(String(255), nullable=False)
    display_name = Column(String(255), nullable=True)
    real_name    = Column(String(255), nullable=True)
    is_app_user  = Column(Boolean, default=False, nullable=False)
    updated_ts   = Column(Integer, nullable=True)   # unix epoch from Slack
    image_192    = Column(Text, nullable=True)
    synced_at    = Column(DateTime(timezone=True), nullable=False)


# ── Sync helper ───────────────────────────────────────────────────────────────

def _fetch_page(token: str, cursor: str | None = None) -> dict:
    params: dict[str, str] = {"limit": "200"}
    if cursor:
        params["cursor"] = cursor
    url = "https://slack.com/api/users.list?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"Slack users.list HTTP error: {exc.code} {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Slack users.list network error: {exc.reason}") from exc


def _sync_slack_apps(db: Session) -> int:
    """Fetch all workspace bots from Slack and upsert into ``slack_app_records``.

    Returns the number of records upserted.  Raises ``RuntimeError`` if the
    Slack API call fails or ``SLACK_WORKSPACE_TOKEN`` is not configured.
    """
    token = os.environ.get("SLACK_WORKSPACE_TOKEN", "")
    if not token:
        raise RuntimeError(
            "SLACK_WORKSPACE_TOKEN is not set — cannot sync Slack app registry."
        )

    now = datetime.now(timezone.utc)
    bots: list[dict] = []
    cursor: str | None = None

    while True:
        data = _fetch_page(token, cursor)
        if not data.get("ok"):
            raise RuntimeError(f"Slack users.list error: {data.get('error')}")

        for u in data.get("members", []):
            if (
                u.get("is_bot")
                and not u.get("deleted")
                and not u.get("name", "").startswith("wf_bot_")
            ):
                profile = u.get("profile", {})
                bots.append({
                    "user_id":      u["id"],
                    "bot_id":       profile.get("bot_id") or None,
                    "app_id":       profile.get("api_app_id") or None,
                    "name":         u["name"],
                    "display_name": profile.get("display_name") or None,
                    "real_name":    profile.get("real_name") or None,
                    "is_app_user":  bool(u.get("is_app_user", False)),
                    "updated_ts":   u.get("updated") or None,
                    "image_192":    profile.get("image_192") or None,
                    "synced_at":    now,
                })

        cursor = (data.get("response_metadata") or {}).get("next_cursor") or None
        if not cursor:
            break

    for entry in bots:
        existing = db.get(SlackAppRecord, entry["user_id"])
        if existing:
            for k, v in entry.items():
                setattr(existing, k, v)
        else:
            db.add(SlackAppRecord(**entry))

    db.commit()
    logger.info("Slack app registry synced: %d records", len(bots))
    return len(bots)


def needs_sync(db: Session) -> bool:
    """Return True if the cache is empty or older than ``_CACHE_TTL_SECONDS``."""
    from sqlalchemy import func
    latest: datetime | None = db.query(func.max(SlackAppRecord.synced_at)).scalar()
    if latest is None:
        return True
    age = (datetime.now(timezone.utc) - latest).total_seconds()
    return age > _CACHE_TTL_SECONDS
