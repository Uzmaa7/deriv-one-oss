"""Slack App Registry API.

Endpoints
─────────
GET  /api/slack-apps          All workspace bots; auto-syncs when cache > 24 h old.
                              Each row includes ``registered: bool`` cross-referencing
                              agents already registered on the platform.

Admin endpoints (is_admin required)
────────────────────────────────────
POST /api/slack-apps/sync     Force a fresh sync from Slack users.list.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from agents import Agent
from cf_access import build_auth_context
from database import SessionLocal
from slack_app_registry import SlackAppRecord, _sync_slack_apps, needs_sync

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth(request: Request):
    try:
        return build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _admin_auth(request: Request):
    ctx = _auth(request)
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return ctx


def _record_to_dict(record: SlackAppRecord, registered_app_ids: set[str]) -> dict:
    return {
        "user_id":      record.user_id,
        "bot_id":       record.bot_id,
        "app_id":       record.app_id,
        "name":         record.name,
        "display_name": record.display_name,
        "real_name":    record.real_name,
        "is_app_user":  record.is_app_user,
        "updated_ts":   record.updated_ts,
        "image_192":    record.image_192,
        "synced_at":    record.synced_at.isoformat() if record.synced_at else None,
        "registered":   record.app_id in registered_app_ids if record.app_id else False,
    }


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/api/slack-apps")
def list_slack_apps(
    request: Request,
    db: Session = Depends(_db),
):
    """Return all workspace bots from the local cache.

    Auto-triggers a sync when the cache is empty or older than 24 hours.
    Each record includes a ``registered`` boolean indicating whether a platform
    agent has been registered for this Slack app.
    """
    _auth(request)

    if needs_sync(db):
        try:
            _sync_slack_apps(db)
        except RuntimeError as exc:
            logger.warning("Auto-sync skipped: %s", exc)

    registered_app_ids: set[str] = {
        row.slack_app_id
        for row in db.query(Agent.slack_app_id)
        .filter(Agent.slack_app_id.isnot(None))
        .all()
    }

    records = (
        db.query(SlackAppRecord)
        .order_by(SlackAppRecord.real_name, SlackAppRecord.name)
        .all()
    )
    return [_record_to_dict(r, registered_app_ids) for r in records]


@router.post("/api/slack-apps/sync")
def sync_slack_apps(
    request: Request,
    db: Session = Depends(_db),
):
    """Force a fresh sync from Slack ``users.list``. Admin only."""
    _admin_auth(request)
    try:
        count = _sync_slack_apps(db)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"synced": count}
