"""Agent-Level Access Control API.

Endpoints
─────────
POST /api/agents/{agent_id}/access/request          User requests access to an agent
GET  /api/agents/{agent_id}/access/status           Current user's access status for an agent

Owner endpoints (agent owner, collaborator, or admin)
───────────────────────────────────────────────────────
GET  /api/agents/{agent_id}/access/grants           List access grants for an agent
POST /api/agents/{agent_id}/access/grant            Owner bulk direct grant (no request required)
POST /api/agents/{agent_id}/access/{grant_id}/approve   Approve a pending request
POST /api/agents/{agent_id}/access/{grant_id}/deny      Deny with reason
POST /api/agents/{agent_id}/access/{grant_id}/revoke    Revoke an active grant

Admin endpoints (is_admin required)
────────────────────────────────────
GET  /api/admin/agents/access/grants                All active agent grants org-wide
GET  /api/admin/agents/{agent_id}/access/requests   Pending + historical requests for an agent
POST /api/admin/agents/{agent_id}/access/grant      Admin direct grant (no request required)
POST /api/admin/agents/{agent_id}/access/{grant_id}/approve   Approve a pending request
POST /api/admin/agents/{agent_id}/access/{grant_id}/deny      Deny with reason
POST /api/admin/agents/{agent_id}/access/{grant_id}/revoke    Revoke an active grant
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from access_models import (
    AgentAccessGrant,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
    STATUS_REVOKED,
    agent_access_grant_to_dict,
)
from agents import Agent, AgentCollaborator
from cf_access import build_auth_context
from database import SessionLocal
from org_directory import get_employee_by_email

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


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


def _get_agent_or_404(agent_id: str, db: Session) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found.")
    return agent


def _active_grant_for_user(agent_id: str, user_email: str, db: Session) -> Optional[AgentAccessGrant]:
    """Return the active (approved, non-expired) grant for this user+agent, or None."""
    now = _utcnow()
    grant = (
        db.query(AgentAccessGrant)
        .filter(
            AgentAccessGrant.agent_id == agent_id,
            AgentAccessGrant.user_email == user_email,
            AgentAccessGrant.status == STATUS_APPROVED,
        )
        .filter(
            (AgentAccessGrant.expires_at == None) |  # noqa: E711
            (AgentAccessGrant.expires_at > now)
        )
        .first()
    )
    return grant


# ── Request bodies ────────────────────────────────────────────────────────────

class AccessRequestBody(BaseModel):
    justification: Optional[str] = Field(default=None, max_length=1000)


_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")
_MAX_BULK_GRANT_EMAILS = 50


def _normalize_agent_emails(
    user_emails: Optional[list[str]] = None,
    user_email: Optional[str] = None,
) -> list[str]:
    """Merge user_email / user_emails into a deduped, validated list (max 50)."""
    raw: list[str] = []
    if user_emails:
        for item in user_emails:
            if not isinstance(item, str):
                continue
            raw.extend(part.strip() for part in item.split(",") if part.strip())
    if user_email:
        raw.extend(part.strip() for part in user_email.split(",") if part.strip())

    seen: set[str] = set()
    normalized: list[str] = []
    for e in raw:
        e = e.lower()
        if e in seen:
            continue
        if len(e) > 320 or not _EMAIL_RE.match(e):
            raise ValueError(f"Invalid email address format: {e}")
        seen.add(e)
        normalized.append(e)

    if not normalized:
        raise ValueError("At least one email address is required.")
    if len(normalized) > _MAX_BULK_GRANT_EMAILS:
        raise ValueError(f"At most {_MAX_BULK_GRANT_EMAILS} emails per request.")
    return normalized


class GrantBody(BaseModel):
    """Direct agent grant. Accepts ``user_email``, ``user_emails``, or
    comma-separated values in either field. All recipients share the same
    justification and expiry."""

    user_email: Optional[str] = Field(default=None, max_length=4000)
    user_emails: Optional[list[str]] = None
    justification: Optional[str] = Field(default=None, max_length=1000)
    expires_in_days: Optional[int] = Field(default=None, ge=1, le=3650)

    def resolved_emails(self) -> list[str]:
        return _normalize_agent_emails(user_emails=self.user_emails, user_email=self.user_email)


AdminGrantBody = GrantBody  # backwards-compatible alias


class DenyBody(BaseModel):
    deny_reason: Optional[str] = Field(default=None, max_length=500)


class RevokeBody(BaseModel):
    revoke_reason: Optional[str] = Field(default=None, max_length=500)


def _is_agent_owner(ctx, agent: Agent) -> bool:
    from agent_access_helpers import is_agent_owner

    return is_agent_owner(agent, ctx.sub, ctx.email)


def _ensure_agent_owner_or_admin(ctx, agent: Agent) -> None:
    """Agent owner, a collaborator, or a platform admin can manage user access."""
    if _is_agent_owner(ctx, agent) or ctx.is_admin:
        return
    db = SessionLocal()
    try:
        collab = (
            db.query(AgentCollaborator)
            .filter(
                AgentCollaborator.agent_id == agent.id,
                AgentCollaborator.user_email == ctx.email.lower(),
            )
            .first()
        )
    finally:
        db.close()
    if not collab:
        raise HTTPException(
            status_code=403,
            detail="Only the agent owner, a collaborator, or an admin can manage user access.",
        )


async def _bulk_grant_agent_access(
    *,
    agent: Agent,
    agent_id: str,
    target_emails: list[str],
    granter_email: str,
    granter_name: str,
    justification: str | None,
    expires_at: datetime | None,
    db: Session,
    role_id: str | None = None,
    role_name: str | None = None,
) -> dict:
    """Grant access to one or many users. Returns partial-success response dict."""
    now = _utcnow()
    granted: list[dict] = []
    errors: list[dict] = []

    for user_email in target_emails:
        from agent_access_helpers import creator_email_matches

        if creator_email_matches(agent.creator_email, user_email):
            errors.append({
                "email": user_email,
                "error": "That user is the creator of this agent — they already have access.",
            })
            continue

        if _active_grant_for_user(agent_id, user_email, db):
            errors.append({
                "email": user_email,
                "error": "That user already has active access to this agent.",
            })
            continue

        user_name = user_email
        try:
            emp = await get_employee_by_email(user_email)
            if emp:
                user_name = emp.name or user_email
        except Exception:
            logger.debug("IAM lookup failed for %s during agent grant", user_email)

        grant = AgentAccessGrant(
            agent_id=agent_id,
            user_email=user_email,
            user_name=user_name,
            status=STATUS_APPROVED,
            justification=justification,
            granted_by_email=granter_email,
            granted_by_name=granter_name,
            decided_at=now,
            expires_at=expires_at,
            role_id=role_id,
            role_name=role_name,
        )
        db.add(grant)
        db.query(AgentAccessGrant).filter(
            AgentAccessGrant.agent_id == agent_id,
            AgentAccessGrant.user_email == user_email,
            AgentAccessGrant.status == STATUS_PENDING,
        ).update({
            "status": STATUS_APPROVED,
            "decided_at": now,
            "granted_by_email": granter_email,
            "granted_by_name": granter_name,
            "updated_at": now,
        })
        db.flush()
        granted.append(agent_access_grant_to_dict(grant))
        logger.info(
            "Agent access granted: agent=%s user=%s by=%s grant=%s",
            agent_id, user_email, granter_email, grant.id,
        )

    if granted:
        db.commit()
    else:
        db.rollback()

    if not granted:
        first = errors[0]["error"] if errors else "Grant failed."
        if len(target_emails) == 1:
            status = 400 if "creator" in first.lower() else 409
            raise HTTPException(status_code=status, detail=first)
        raise HTTPException(
            status_code=422,
            detail={"message": "All grants failed.", "errors": errors},
        )

    response: dict = {
        "granted": len(granted),
        "failed": len(errors),
        "grants": granted,
        "errors": errors,
    }
    if len(granted) == 1 and not errors:
        response.update(granted[0])
    return response


# ── User endpoints ────────────────────────────────────────────────────────────

@router.post("/api/agents/{agent_id}/access/request", status_code=201)
async def request_agent_access(
    agent_id: str,
    body: AccessRequestBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Submit an access request for an agent."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)

    # Creator always has implicit access — no need to request
    from agent_access_helpers import creator_email_matches

    if creator_email_matches(agent.creator_email, ctx.email):
        raise HTTPException(status_code=400, detail="You are the creator of this agent — you already have access.")

    # Reject duplicate active grant
    if _active_grant_for_user(agent_id, ctx.email, db):
        raise HTTPException(status_code=409, detail="You already have active access to this agent.")

    # Reject duplicate pending request
    pending = (
        db.query(AgentAccessGrant)
        .filter(
            AgentAccessGrant.agent_id == agent_id,
            AgentAccessGrant.user_email == ctx.email,
            AgentAccessGrant.status == STATUS_PENDING,
        )
        .first()
    )
    if pending:
        raise HTTPException(status_code=409, detail="You already have a pending access request for this agent.")

    # IAM lookup for display name
    user_name = ctx.email
    try:
        emp = await get_employee_by_email(ctx.email)
        if emp:
            user_name = emp.name or ctx.email
    except Exception:
        logger.debug("IAM lookup failed for %s during agent access request", ctx.email)

    grant = AgentAccessGrant(
        agent_id=agent_id,
        user_email=ctx.email,
        user_name=user_name,
        status=STATUS_PENDING,
        justification=(body.justification or "").strip() or None,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)
    logger.info("Agent access requested: agent=%s user=%s grant=%s", agent_id, ctx.email, grant.id)
    return agent_access_grant_to_dict(grant)


@router.get("/api/agents/{agent_id}/access/status")
def get_agent_access_status(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Return the current user's access status for a given agent."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)

    from agent_access_helpers import is_agent_owner

    if is_agent_owner(agent, ctx.sub, ctx.email):
        return {"access": "owner", "grant": None}

    active = _active_grant_for_user(agent_id, ctx.email, db)
    if active:
        return {"access": "approved", "grant": agent_access_grant_to_dict(active)}

    pending = (
        db.query(AgentAccessGrant)
        .filter(
            AgentAccessGrant.agent_id == agent_id,
            AgentAccessGrant.user_email == ctx.email,
            AgentAccessGrant.status == STATUS_PENDING,
        )
        .first()
    )
    if pending:
        return {"access": "pending", "grant": agent_access_grant_to_dict(pending)}

    return {"access": "none", "grant": None}


# ── Owner endpoints ───────────────────────────────────────────────────────────

@router.get("/api/agents/{agent_id}/access/grants")
def owner_list_agent_grants(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List all access grants for an agent (owner or admin only)."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    rows = (
        db.query(AgentAccessGrant)
        .filter(AgentAccessGrant.agent_id == agent_id)
        .order_by(AgentAccessGrant.created_at.desc())
        .all()
    )
    return {"grants": [agent_access_grant_to_dict(g) for g in rows]}


@router.post("/api/agents/{agent_id}/access/grant", status_code=201)
async def owner_grant_agent_access(
    agent_id: str,
    body: GrantBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Owner bulk direct grant — immediately approve access without a request.

    Accepts one or many emails (``user_email``, ``user_emails``, or
    comma-separated). All recipients share the same justification and expiry.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    try:
        target_emails = body.resolved_emails()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    granter_name = ctx.email
    try:
        emp = await get_employee_by_email(ctx.email)
        if emp:
            granter_name = emp.name or ctx.email
    except Exception:
        pass

    now = _utcnow()
    expires_at = None
    if body.expires_in_days:
        expires_at = now + timedelta(days=body.expires_in_days)

    justification = (body.justification or "").strip() or None
    return await _bulk_grant_agent_access(
        agent=agent,
        agent_id=agent_id,
        target_emails=target_emails,
        granter_email=ctx.email,
        granter_name=granter_name,
        justification=justification,
        expires_at=expires_at,
        db=db,
    )


@router.post("/api/agents/{agent_id}/access/{grant_id}/approve")
def owner_approve_agent_request(
    agent_id: str,
    grant_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Approve a pending agent access request (owner or admin only)."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    return _approve_agent_grant(agent_id, grant_id, ctx.email, db)


@router.post("/api/agents/{agent_id}/access/{grant_id}/deny")
def owner_deny_agent_request(
    agent_id: str,
    grant_id: str,
    body: DenyBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Deny a pending agent access request (owner or admin only)."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    return _deny_agent_grant(agent_id, grant_id, ctx.email, body.deny_reason, db)


@router.post("/api/agents/{agent_id}/access/{grant_id}/revoke")
def owner_revoke_agent_grant(
    agent_id: str,
    grant_id: str,
    body: RevokeBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke an active agent access grant (owner or admin only)."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    return _revoke_agent_grant(agent_id, grant_id, ctx.email, body.revoke_reason, db)


def _approve_agent_grant(agent_id: str, grant_id: str, decider_email: str, db: Session) -> dict:
    grant = (
        db.query(AgentAccessGrant)
        .filter(AgentAccessGrant.id == grant_id, AgentAccessGrant.agent_id == agent_id)
        .first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if grant.status != STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"Grant is already in status '{grant.status}'.")

    now = _utcnow()
    grant.status = STATUS_APPROVED
    grant.granted_by_email = decider_email
    grant.decided_at = now
    grant.updated_at = now
    db.commit()
    logger.info(
        "Agent access approved: grant=%s agent=%s user=%s by=%s",
        grant_id, agent_id, grant.user_email, decider_email,
    )
    return agent_access_grant_to_dict(grant)


def _deny_agent_grant(
    agent_id: str,
    grant_id: str,
    decider_email: str,
    deny_reason: str | None,
    db: Session,
) -> dict:
    grant = (
        db.query(AgentAccessGrant)
        .filter(AgentAccessGrant.id == grant_id, AgentAccessGrant.agent_id == agent_id)
        .first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if grant.status != STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"Grant is already in status '{grant.status}'.")

    now = _utcnow()
    grant.status = STATUS_DENIED
    grant.granted_by_email = decider_email
    grant.decided_at = now
    grant.deny_reason = (deny_reason or "").strip() or None
    grant.updated_at = now
    db.commit()
    logger.info(
        "Agent access denied: grant=%s agent=%s user=%s by=%s",
        grant_id, agent_id, grant.user_email, decider_email,
    )
    return agent_access_grant_to_dict(grant)


def _revoke_agent_grant(
    agent_id: str,
    grant_id: str,
    revoker_email: str,
    revoke_reason: str | None,
    db: Session,
) -> dict:
    grant = (
        db.query(AgentAccessGrant)
        .filter(AgentAccessGrant.id == grant_id, AgentAccessGrant.agent_id == agent_id)
        .first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if grant.status != STATUS_APPROVED:
        raise HTTPException(status_code=409, detail=f"Grant is not active (status='{grant.status}').")

    now = _utcnow()
    grant.status = STATUS_REVOKED
    grant.revoked_at = now
    grant.revoked_by = revoker_email
    grant.revoke_reason = (revoke_reason or "").strip() or None
    grant.updated_at = now
    db.commit()
    logger.info(
        "Agent access revoked: grant=%s agent=%s user=%s by=%s",
        grant_id, agent_id, grant.user_email, revoker_email,
    )
    return agent_access_grant_to_dict(grant)


# ── Admin endpoints ───────────────────────────────────────────────────────────

@router.get("/api/admin/agents/access/grants")
def admin_list_all_agent_grants(
    request: Request,
    db: Session = Depends(_db),
):
    """Return all agent access grants org-wide (all statuses — pending, approved, denied, revoked).

    The frontend filters by status; returning all rows ensures pending requests
    are visible in the admin panel immediately after submission.
    """
    _admin_auth(request)
    rows = (
        db.query(AgentAccessGrant)
        .order_by(AgentAccessGrant.created_at.desc())
        .all()
    )
    return {"grants": [agent_access_grant_to_dict(g) for g in rows]}


@router.get("/api/admin/agents/{agent_id}/access/requests")
def admin_list_agent_requests(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Return all pending + historical access requests for a given agent."""
    _admin_auth(request)
    _get_agent_or_404(agent_id, db)
    rows = (
        db.query(AgentAccessGrant)
        .filter(AgentAccessGrant.agent_id == agent_id)
        .order_by(AgentAccessGrant.created_at.desc())
        .all()
    )
    return {"requests": [agent_access_grant_to_dict(g) for g in rows]}


@router.post("/api/admin/agents/{agent_id}/access/grant", status_code=201)
async def admin_grant_agent_access(
    agent_id: str,
    body: AdminGrantBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Admin direct grant — immediately approve access without a request.

    Accepts one or many emails (``user_email``, ``user_emails``, or
    comma-separated). All recipients share the same justification and expiry.
    """
    ctx = _admin_auth(request)
    agent = _get_agent_or_404(agent_id, db)

    try:
        target_emails = body.resolved_emails()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    admin_name = ctx.email
    try:
        emp = await get_employee_by_email(ctx.email)
        if emp:
            admin_name = emp.name or ctx.email
    except Exception:
        pass

    now = _utcnow()
    expires_at = None
    if body.expires_in_days:
        expires_at = now + timedelta(days=body.expires_in_days)

    justification = (body.justification or "").strip() or None
    return await _bulk_grant_agent_access(
        agent=agent,
        agent_id=agent_id,
        target_emails=target_emails,
        granter_email=ctx.email,
        granter_name=admin_name,
        justification=justification,
        expires_at=expires_at,
        db=db,
    )


@router.post("/api/admin/agents/{agent_id}/access/{grant_id}/approve")
def admin_approve_agent_request(
    agent_id: str,
    grant_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Approve a pending agent access request."""
    ctx = _admin_auth(request)
    return _approve_agent_grant(agent_id, grant_id, ctx.email, db)


@router.post("/api/admin/agents/{agent_id}/access/{grant_id}/deny")
def admin_deny_agent_request(
    agent_id: str,
    grant_id: str,
    body: DenyBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Deny a pending agent access request."""
    ctx = _admin_auth(request)
    return _deny_agent_grant(agent_id, grant_id, ctx.email, body.deny_reason, db)


@router.post("/api/admin/agents/{agent_id}/access/{grant_id}/revoke")
def admin_revoke_agent_grant(
    agent_id: str,
    grant_id: str,
    body: RevokeBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke an active agent access grant."""
    ctx = _admin_auth(request)
    return _revoke_agent_grant(agent_id, grant_id, ctx.email, body.revoke_reason, db)


# ── Slack App Grants ───────────────────────────────────────────────────────────
# Agent owners and admins can authorise Slack bots to invoke an agent with full
# skill access, bypassing the user-level skill filter applied to human senders.

from access_models import AgentSlackAppGrant, agent_slack_app_grant_to_dict  # noqa: E402
from sqlalchemy.exc import IntegrityError  # noqa: E402


def _owner_or_admin(request: Request, agent: Agent, db=None) -> None:
    """Raise 403 unless the requester is the agent owner, a collaborator, or a platform admin."""
    ctx = _auth(request)
    from agent_access_helpers import is_agent_owner

    is_owner = is_agent_owner(agent, ctx.sub, ctx.email)
    if not is_owner and not ctx.is_admin:
        # Fall through to collaborator check
        _db_instance = db
        if _db_instance is None:
            from database import SessionLocal
            _db_instance = SessionLocal()
            close_after = True
        else:
            close_after = False
        try:
            collab = (
                _db_instance.query(AgentCollaborator)
                .filter(
                    AgentCollaborator.agent_id == agent.id,
                    AgentCollaborator.user_email == ctx.email.lower(),
                )
                .first()
            )
        finally:
            if close_after:
                _db_instance.close()
        if not collab:
            raise HTTPException(status_code=403, detail="Agent owner or admin access required.")


class SlackAppGrantBody(BaseModel):
    slack_user_id: str = Field(..., min_length=1, max_length=40)
    app_name: str | None = Field(default=None, max_length=255)


@router.get("/api/agents/{agent_id}/slack-app-grants")
def list_slack_app_grants(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List all Slack apps authorised to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    _owner_or_admin(request, agent, db)
    grants = (
        db.query(AgentSlackAppGrant)
        .filter(AgentSlackAppGrant.agent_id == agent_id)
        .order_by(AgentSlackAppGrant.created_at.desc())
        .all()
    )
    # Enrich with live registry data if available
    try:
        from slack_app_registry import SlackAppRecord
        user_ids = [g.slack_user_id for g in grants]
        records = {
            r.user_id: r
            for r in db.query(SlackAppRecord).filter(SlackAppRecord.user_id.in_(user_ids)).all()
        }
    except Exception:
        records = {}

    result = []
    for g in grants:
        d = agent_slack_app_grant_to_dict(g)
        rec = records.get(g.slack_user_id)
        d["image_192"] = rec.image_192 if rec else None
        d["is_app_user"] = rec.is_app_user if rec else None
        d["app_id"] = rec.app_id if rec else None
        result.append(d)
    return result


@router.post("/api/agents/{agent_id}/slack-app-grants", status_code=201)
def add_slack_app_grant(
    agent_id: str,
    body: SlackAppGrantBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Authorise a Slack app to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    ctx = _auth(request)
    _owner_or_admin(request, agent, db)

    # Resolve display name from registry if not provided
    app_name = (body.app_name or "").strip() or None
    if not app_name:
        try:
            from slack_app_registry import SlackAppRecord
            rec = db.query(SlackAppRecord).filter(
                SlackAppRecord.user_id == body.slack_user_id
            ).first()
            if rec:
                app_name = rec.real_name or rec.display_name or rec.name
        except Exception:
            pass

    grant = AgentSlackAppGrant(
        agent_id=agent_id,
        slack_user_id=body.slack_user_id,
        app_name=app_name,
        granted_by_email=ctx.email,
    )
    db.add(grant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="This Slack app is already authorised for this agent.")
    db.refresh(grant)
    logger.info(
        "Slack app grant added: agent=%s slack_user=%s app_name=%r by=%s",
        agent_id, body.slack_user_id, app_name, ctx.email,
    )
    return agent_slack_app_grant_to_dict(grant)


@router.delete("/api/agents/{agent_id}/slack-app-grants/{slack_user_id}", status_code=200)
def remove_slack_app_grant(
    agent_id: str,
    slack_user_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke a Slack app's authorisation to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    ctx = _auth(request)
    _owner_or_admin(request, agent, db)

    grant = (
        db.query(AgentSlackAppGrant)
        .filter(
            AgentSlackAppGrant.agent_id == agent_id,
            AgentSlackAppGrant.slack_user_id == slack_user_id,
        )
        .first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Slack app grant not found.")
    db.delete(grant)
    db.commit()
    logger.info(
        "Slack app grant removed: agent=%s slack_user=%s by=%s",
        agent_id, slack_user_id, ctx.email,
    )
    return {"deleted": True, "slack_user_id": slack_user_id}


# ── Slack Workflow Grants ─────────────────────────────────────────────────────
# Agent owners can authorise Workflow Builder workflows (by workflow ID) to
# invoke an agent when they @mention it — same full-skill access as Slack apps.

from access_models import (  # noqa: E402
    AgentSlackWorkflowGrant,
    agent_slack_workflow_grant_to_dict,
)
import re  # noqa: E402

_WORKFLOW_ID_RE = re.compile(r"^(?:(?:Wf|WF|A|Wt|WT)[A-Za-z0-9]{6,78}|\d{8,32})$")


class SlackWorkflowGrantBody(BaseModel):
    workflow_id: str = Field(..., min_length=3, max_length=80)
    label: str | None = Field(default=None, max_length=255)


def _normalise_workflow_id(raw: str) -> str:
    """Strip URL path noise and validate a workflow / app ID."""
    value = (raw or "").strip()
    # Allow pasting a full Workflow Builder URL
    if "/" in value:
        parts = [p for p in value.split("/") if p]
        for part in reversed(parts):
            candidate = part.split("?")[0].strip()
            if _WORKFLOW_ID_RE.match(candidate):
                value = candidate
                break
    if value.startswith(("Wf", "WF", "Wt", "WT")):
        # Canonicalise prefix casing: Wf… / Wt…
        value = value[0].upper() + value[1].lower() + value[2:]
    elif value.startswith("a") and len(value) > 1 and value[1:].isalnum():
        value = "A" + value[1:]
    if not _WORKFLOW_ID_RE.match(value):
        raise HTTPException(
            status_code=400,
            detail="Invalid workflow ID. Expected a Workflow Builder ID (Wf…) or app ID (A…).",
        )
    return value


@router.get("/api/agents/{agent_id}/slack-workflow-grants")
def list_slack_workflow_grants(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List Slack workflows authorised to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    _owner_or_admin(request, agent, db)
    grants = (
        db.query(AgentSlackWorkflowGrant)
        .filter(AgentSlackWorkflowGrant.agent_id == agent_id)
        .order_by(AgentSlackWorkflowGrant.created_at.desc())
        .all()
    )
    return [agent_slack_workflow_grant_to_dict(g) for g in grants]


@router.post("/api/agents/{agent_id}/slack-workflow-grants", status_code=201)
def add_slack_workflow_grant(
    agent_id: str,
    body: SlackWorkflowGrantBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Authorise a Slack workflow to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    ctx = _auth(request)
    _owner_or_admin(request, agent, db)

    workflow_id = _normalise_workflow_id(body.workflow_id)
    label = (body.label or "").strip() or None

    resolved_app_id: str | None = None
    if workflow_id.startswith("Wf"):
        try:
            from api_slack_events import _read_secret, _resolve_workflow_app_id

            xoxb = _read_secret(agent.xoxb_secret_ref)
            resolved = _resolve_workflow_app_id(workflow_id, xoxb=xoxb)
            resolved_app_id = resolved or None
        except Exception:
            logger.warning(
                "Could not resolve workflow app_id at grant time agent=%s workflow_id=%s",
                agent_id,
                workflow_id,
                exc_info=True,
            )
    elif workflow_id.startswith("A"):
        resolved_app_id = workflow_id

    grant = AgentSlackWorkflowGrant(
        agent_id=agent_id,
        workflow_id=workflow_id,
        resolved_app_id=resolved_app_id,
        label=label,
        granted_by_email=ctx.email,
    )
    db.add(grant)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This workflow is already authorised for this agent.",
        )
    db.refresh(grant)
    logger.info(
        "Slack workflow grant added: agent=%s workflow_id=%s label=%r by=%s",
        agent_id, workflow_id, label, ctx.email,
    )
    return agent_slack_workflow_grant_to_dict(grant)


@router.delete("/api/agents/{agent_id}/slack-workflow-grants/{workflow_id}", status_code=200)
def remove_slack_workflow_grant(
    agent_id: str,
    workflow_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke a Slack workflow's authorisation to invoke this agent."""
    agent = _get_agent_or_404(agent_id, db)
    ctx = _auth(request)
    _owner_or_admin(request, agent, db)

    try:
        normalised = _normalise_workflow_id(workflow_id)
    except HTTPException:
        normalised = workflow_id.strip()

    grant = (
        db.query(AgentSlackWorkflowGrant)
        .filter(
            AgentSlackWorkflowGrant.agent_id == agent_id,
            AgentSlackWorkflowGrant.workflow_id == normalised,
        )
        .first()
    )
    if not grant:
        raise HTTPException(status_code=404, detail="Slack workflow grant not found.")
    db.delete(grant)
    db.commit()
    logger.info(
        "Slack workflow grant removed: agent=%s workflow_id=%s by=%s",
        agent_id, normalised, ctx.email,
    )
    return {"deleted": True, "workflow_id": normalised}
