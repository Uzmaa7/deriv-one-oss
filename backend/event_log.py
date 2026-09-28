"""
Lightweight fire-and-forget event logger.

Usage (backend endpoints):
    from event_log import log_event
    log_event(owner_sub, email, "lab.app_created", {"mode": "gws", "skill_count": 3})

Also exposes POST /api/events for frontend-fired events.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.responses import Response
from pydantic import BaseModel, Field

from database import SessionLocal, UserEvent

logger = logging.getLogger(__name__)
router = APIRouter()


def log_event(owner_sub: str, email: str, event: str, properties: dict[str, Any] | None = None) -> None:
    """Write one UserEvent row. Never raises — failures are logged and swallowed."""
    db = SessionLocal()
    try:
        db.add(UserEvent(
            owner_sub=owner_sub,
            email=email or "",
            event=event,
            properties=json.dumps(properties or {}),
        ))
        db.commit()
    except Exception:
        logger.warning("event_log: failed to record event=%s owner=%s", event, owner_sub[:12], exc_info=True)
    finally:
        db.close()


# ── Frontend event sink ───────────────────────────────────────────────────────

class FrontendEvent(BaseModel):
    event:      str             = Field(..., min_length=1, max_length=80)
    properties: dict[str, Any]  = Field(default_factory=dict)


@router.post("/api/events", status_code=204)
async def record_frontend_event(body: FrontendEvent, request: Request):
    try:
        from cf_access import build_auth_context
        ctx = build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Not authenticated") from exc
    log_event(ctx.sub, ctx.email, body.event, body.properties)
    return Response(status_code=204)
