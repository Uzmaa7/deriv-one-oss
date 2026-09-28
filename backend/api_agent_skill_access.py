"""Agent-scoped skill access grants — owners/collaborators grant skills their agent uses.

Endpoints (agent owner, collaborator, or platform admin)
──────────────────────────────────────────────────────────
GET  /api/agents/{agent_id}/skills/grantable   Skills on this agent the owner may grant
GET  /api/agents/{agent_id}/skills/grants      Active/pending grants for agent skills
POST /api/agents/{agent_id}/skills/grant       Bulk grant skills to users (all or subset)
POST /api/agents/{agent_id}/skills/grants/{grant_id}/revoke   Revoke an active grant
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from access_models import (
    CrossDepartmentGrant,
    STATUS_APPROVED,
    STATUS_REVOKED,
    grant_to_dict,
)
from agents import Agent, loads as agent_loads
from api_access import _normalize_email_list, bulk_grant_skill_access
from api_agent_access import (
    _auth,
    _db,
    _ensure_agent_owner_or_admin,
    _get_agent_or_404,
)
from skill_router import resolve_user_skill_access
from skills_acl import get_skill_acl

logger = logging.getLogger(__name__)
router = APIRouter()

_SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")


class AgentSkillGrantBody(BaseModel):
    """Grant one or more agent skills to one or many users."""

    user_email: Optional[str] = Field(default=None, max_length=4000)
    user_emails: Optional[list[str]] = None
    skill_slugs: Optional[list[str]] = None
    grant_type: str = "time_limited"
    expires_in_days: Optional[int] = Field(default=90, ge=1, le=365)
    justification: str = Field(..., min_length=10, max_length=2000)
    restrictions: dict = {}

    @validator("grant_type")
    def grant_type_valid(cls, v: str) -> str:
        if v not in ("permanent", "time_limited", "restricted"):
            raise ValueError("grant_type must be permanent, time_limited, or restricted.")
        return v

    @validator("skill_slugs", each_item=True)
    def skill_slug_valid(cls, v: str) -> str:
        if not _SKILL_SLUG_RE.match(v):
            raise ValueError(f"Invalid skill slug: {v}")
        return v

    def resolved_emails(self) -> list[str]:
        return _normalize_email_list(emails=self.user_emails, email=self.user_email)


class SkillRevokeBody(BaseModel):
    reason: str = Field(default="Revoked by agent owner", max_length=500)


def _agent_skill_ids(agent: Agent) -> list[str]:
    return agent_loads(agent.skill_ids)


def _resolve_grantable_slugs(
    agent: Agent,
    granter_accessible: frozenset[str],
    is_admin: bool,
) -> list[dict]:
    """Return metadata for each agent skill the owner may grant to others."""
    skills: list[dict] = []
    for slug in _agent_skill_ids(agent):
        acl = get_skill_acl(slug)
        if acl is None:
            skills.append({
                "slug": slug,
                "name": slug,
                "grantable": False,
                "reason": "Skill not found in ACL manifest.",
            })
            continue

        if slug not in granter_accessible and not is_admin:
            skills.append({
                "slug": slug,
                "name": acl.description or slug,
                "risk_level": acl.risk_level,
                "grantable": False,
                "reason": "You do not have access to grant this skill.",
            })
            continue

        if acl.cross_dept_policy == "blocked":
            skills.append({
                "slug": slug,
                "name": acl.description or slug,
                "risk_level": acl.risk_level,
                "grantable": False,
                "reason": "This skill does not allow cross-department access.",
            })
            continue

        if acl.risk_level == "critical" and not is_admin:
            skills.append({
                "slug": slug,
                "name": acl.description or slug,
                "risk_level": acl.risk_level,
                "grantable": False,
                "reason": "Critical-risk skills require platform admin approval.",
            })
            continue

        skills.append({
            "slug": slug,
            "name": acl.description or slug,
            "risk_level": acl.risk_level,
            "grantable": True,
            "reason": None,
        })
    return skills


@router.get("/api/agents/{agent_id}/skills/grantable")
async def list_grantable_agent_skills(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List skills attached to this agent that the owner may grant to other users."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    _, _, accessible = await resolve_user_skill_access(ctx.sub, ctx.email)
    skills = _resolve_grantable_slugs(agent, accessible, ctx.is_admin)
    return {"skills": skills, "agent_id": agent_id}


@router.get("/api/agents/{agent_id}/skills/grants")
def list_agent_skill_grants(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List active and pending cross-dept grants for skills this agent uses."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    slugs = _agent_skill_ids(agent)
    if not slugs:
        return {"grants": []}

    rows = (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.skill_slug.in_(slugs),
            CrossDepartmentGrant.status.in_(["pending", "approved"]),
        )
        .order_by(CrossDepartmentGrant.created_at.desc())
        .all()
    )
    return {"grants": [grant_to_dict(g) for g in rows]}


@router.post("/api/agents/{agent_id}/skills/grant", status_code=201)
async def owner_grant_agent_skills(
    agent_id: str,
    body: AgentSkillGrantBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Grant one or more of this agent's skills to one or many users.

    ``skill_slugs`` is optional — when omitted, all grantable skills on the agent
    are included. Accepts comma-separated emails via ``user_email`` or ``user_emails``.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    agent_slugs = _agent_skill_ids(agent)
    if not agent_slugs:
        raise HTTPException(status_code=400, detail="This agent has no skills configured.")

    try:
        target_emails = body.resolved_emails()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _, _, accessible = await resolve_user_skill_access(ctx.sub, ctx.email)
    grantable_meta = _resolve_grantable_slugs(agent, accessible, ctx.is_admin)
    grantable_slugs = {s["slug"] for s in grantable_meta if s["grantable"]}

    if body.skill_slugs:
        unknown = [s for s in body.skill_slugs if s not in agent_slugs]
        if unknown:
            raise HTTPException(
                status_code=422,
                detail=f"Skill(s) not attached to this agent: {', '.join(unknown)}",
            )
        not_grantable = [s for s in body.skill_slugs if s not in grantable_slugs]
        if not_grantable:
            raise HTTPException(
                status_code=403,
                detail=f"You cannot grant skill(s): {', '.join(not_grantable)}",
            )
        skill_slugs = list(dict.fromkeys(body.skill_slugs))
    else:
        skill_slugs = sorted(grantable_slugs)

    if not skill_slugs:
        raise HTTPException(
            status_code=400,
            detail="No grantable skills on this agent.",
        )

    expires_in_days = body.expires_in_days
    if body.grant_type == "permanent":
        expires_in_days = None

    return await bulk_grant_skill_access(
        skill_slugs=skill_slugs,
        target_emails=target_emails,
        granter_sub=ctx.sub,
        granter_email=ctx.email,
        grant_type=body.grant_type,
        expires_in_days=expires_in_days,
        justification=body.justification.strip(),
        restrictions=body.restrictions,
        db=db,
        allow_critical=ctx.is_admin,
    )


@router.post("/api/agents/{agent_id}/skills/grants/{grant_id}/revoke")
def owner_revoke_agent_skill_grant(
    agent_id: str,
    grant_id: str,
    body: SkillRevokeBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke an active skill grant for a skill this agent uses."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    agent_slugs = set(_agent_skill_ids(agent))
    grant = db.query(CrossDepartmentGrant).filter(CrossDepartmentGrant.id == grant_id).first()
    if not grant:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if grant.skill_slug not in agent_slugs:
        raise HTTPException(status_code=403, detail="This grant is not for a skill used by this agent.")
    if grant.status != STATUS_APPROVED:
        raise HTTPException(status_code=409, detail=f"Grant is not active (status='{grant.status}').")

    grant.status = STATUS_REVOKED
    grant.revoked_at = datetime.now(timezone.utc)
    grant.revoked_by = ctx.email
    grant.revoke_reason = (body.reason or "").strip() or None
    db.commit()

    logger.info(
        "Agent owner revoked skill grant: agent=%s grant=%s skill=%s user=%s by=%s",
        agent_id, grant_id, grant.skill_slug, grant.user_email, ctx.email,
    )
    return {"grant_id": grant_id, "status": STATUS_REVOKED}
