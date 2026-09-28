"""Agent-scoped Roles — named, reusable bundles of skills scoped to one agent.

A role has no membership list of its own; assigning a role to a user creates
real per-skill CrossDepartmentGrant rows tagged with the role's id (via
bulk_grant_skill_access's role_id/role_name params). Two distinct questions,
answered two different ways:

- "Is this person a member of this role at all?" — yes only if they hold at
  least one active grant tagged with this role's id (see
  _role_member_emails). Merely already holding a skill the role happens to
  bundle does NOT make someone a member — creating or editing a role never
  "discovers" members out of people who were never actually assigned it.
- "Is a member full or partial?" — once someone is a member, this reflects
  their *current* overall access: every skill in the bundle (however each
  was obtained — this role, another role sharing the skill, or a direct
  grant) plus active agent access (or being the agent's owner). This is what
  lets a member who already held one bundle skill directly avoid getting
  stuck "partial" forever once the role grants them the rest.

Endpoints
─────────
POST /api/agents/{agent_id}/roles                              Define a role
GET  /api/agents/{agent_id}/roles                               List roles for an agent
GET  /api/agents/{agent_id}/roles/{role_id}                     Get one role + member stats
PUT  /api/agents/{agent_id}/roles/{role_id}                     Edit a role
DELETE /api/agents/{agent_id}/roles/{role_id}                    Delete a role
POST /api/agents/{agent_id}/roles/{role_id}/archive             Delete a role (alias)
GET  /api/agents/{agent_id}/roles/{role_id}/members             List current role members
POST /api/agents/{agent_id}/roles/{role_id}/assign              Assign role to user(s)
POST /api/agents/{agent_id}/roles/{role_id}/members/{email}/revoke   Revoke role from a user
GET  /api/admin/users/{email}/roles                             Every role a user holds, across all agents (admin only)

Every agent-scoped endpoint here — defining/editing/deleting a role,
assigning it, viewing members, and revoking — requires the agent owner, a
collaborator, or a platform admin (via _ensure_agent_owner_or_admin), the
same trust level as the Skill Access and User Access tabs. The cross-agent
lookup at the bottom is platform-admin only (via _admin_auth), since it
spans every agent, not just one the caller owns or collaborates on.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, validator
from sqlalchemy.orm import Session

from access_models import (
    AgentAccessGrant,
    AgentRole,
    CrossDepartmentGrant,
    STATUS_APPROVED,
    STATUS_REVOKED,
    agent_role_to_dict,
)
from api_access import _normalize_email_list, bulk_grant_skill_access
from agents import Agent
from api_agent_access import (
    _active_grant_for_user,
    _admin_auth,
    _auth,
    _bulk_grant_agent_access,
    _db,
    _ensure_agent_owner_or_admin,
    _get_agent_or_404,
)
from api_agent_skill_access import _agent_skill_ids

logger = logging.getLogger(__name__)
router = APIRouter()

_SKILL_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,63}$")


class AgentRoleBody(BaseModel):
    """Define or edit a role's name, description, and skill bundle."""

    name: str = Field(..., min_length=1, max_length=120)
    description: Optional[str] = Field(default=None, max_length=2000)
    skill_slugs: list[str] = Field(default_factory=list)

    @validator("skill_slugs", each_item=True)
    def skill_slug_valid(cls, v: str) -> str:
        if not _SKILL_SLUG_RE.match(v):
            raise ValueError(f"Invalid skill slug: {v}")
        return v


class AgentRoleAssignBody(BaseModel):
    """Assign a role's full skill bundle to one or many users."""

    user_email: Optional[str] = Field(default=None, max_length=4000)
    user_emails: Optional[list[str]] = None
    grant_type: str = "time_limited"
    expires_in_days: Optional[int] = Field(default=90, ge=1, le=365)
    justification: str = Field(..., min_length=10, max_length=2000)
    restrictions: dict = {}

    @validator("grant_type")
    def grant_type_valid(cls, v: str) -> str:
        if v not in ("permanent", "time_limited", "restricted"):
            raise ValueError("grant_type must be permanent, time_limited, or restricted.")
        return v

    def resolved_emails(self) -> list[str]:
        return _normalize_email_list(emails=self.user_emails, email=self.user_email)


class RoleMemberRevokeBody(BaseModel):
    reason: str = Field(default="Role revoked by agent owner", max_length=500)


def _role_skill_slugs(role: AgentRole) -> list[str]:
    try:
        return json.loads(role.skill_slugs) if role.skill_slugs else []
    except (TypeError, ValueError):
        return []


def _get_role_or_404(agent_id: str, role_id: str, db: Session) -> AgentRole:
    role = (
        db.query(AgentRole)
        .filter(AgentRole.id == role_id, AgentRole.agent_id == agent_id)
        .first()
    )
    if not role:
        raise HTTPException(status_code=404, detail="Role not found.")
    return role


def _active_role_named(
    db: Session,
    agent_id: str,
    name: str,
    *,
    exclude_id: Optional[str] = None,
) -> Optional[AgentRole]:
    """Return an *active* (non-archived) role with this name on the agent, if any."""
    query = db.query(AgentRole).filter(
        AgentRole.agent_id == agent_id,
        AgentRole.name == name,
        AgentRole.archived_at.is_(None),
    )
    if exclude_id:
        query = query.filter(AgentRole.id != exclude_id)
    return query.first()


def _grants_for_skills(db: Session, skill_slugs: list[str]) -> list[CrossDepartmentGrant]:
    if not skill_slugs:
        return []
    return (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.skill_slug.in_(skill_slugs),
            CrossDepartmentGrant.status == STATUS_APPROVED,
        )
        .all()
    )


def _has_agent_access(agent, user_email: str, db: Session) -> bool:
    """Whether user_email can actually talk to this agent at all.

    A role is a bundle of skills *plus* the ability to reach the agent in
    Slack — holding every skill directly, with no agent access, is not
    "full" role access no matter how it looks on paper. The owner/creator
    is implicitly covered without needing an AgentAccessGrant row.
    """
    if (agent.creator_email or "").lower() == user_email.lower():
        return True
    return _active_grant_for_user(agent.id, user_email, db) is not None


def _role_member_emails(role_id: str, db: Session) -> set[str]:
    """Emails of everyone who was actually assigned this role at some point —
    i.e. holds at least one active grant tagged with this role's id.

    This is the membership gate: merely holding a skill the role happens to
    bundle, with no grant ever tagged to this role, does not make someone a
    member. Creating (or editing) a role never conjures members out of
    people who coincidentally already have its skills — only an explicit
    assign does that.
    """
    rows = (
        db.query(CrossDepartmentGrant.user_email)
        .filter(
            CrossDepartmentGrant.role_id == role_id,
            CrossDepartmentGrant.status == STATUS_APPROVED,
        )
        .distinct()
        .all()
    )
    return {r[0] for r in rows}


def _role_member_stats(agent, role: AgentRole, db: Session) -> dict:
    """Compute full/partial member counts.

    Membership itself requires an actual role-tagged grant (see
    _role_member_emails) — but once someone is a member, "full" vs
    "partial" reflects their *current* overall access: every skill in the
    bundle (however each was obtained — this role, another role, or a
    direct grant) plus active agent access. That's what lets a member who
    already held one bundle skill directly avoid getting stuck "partial"
    forever once the role also grants them the rest.
    """
    slug_set = set(_role_skill_slugs(role))
    member_emails = _role_member_emails(role.id, db)
    if not slug_set or not member_emails:
        return {"full_count": 0, "partial_count": 0}

    by_user: dict[str, set[str]] = {}
    for g in _grants_for_skills(db, list(slug_set)):
        if g.user_email in member_emails:
            by_user.setdefault(g.user_email, set()).add(g.skill_slug)

    full = 0
    partial = 0
    for user_email in member_emails:
        slugs = by_user.get(user_email, set())
        if slug_set.issubset(slugs) and _has_agent_access(agent, user_email, db):
            full += 1
        else:
            partial += 1
    return {"full_count": full, "partial_count": partial}


def _full_members_for_skills(agent, role_id: str, slug_set: set[str], db: Session) -> list[str]:
    """Emails of *actual members of this role* (see _role_member_emails) who
    currently hold every skill in slug_set AND have agent access — i.e. who
    would show as "full" against that skill set. Scoped to real members so
    editing a role never reaches out to people who were never assigned it."""
    if not slug_set:
        return []
    member_emails = _role_member_emails(role_id, db)
    if not member_emails:
        return []
    by_user: dict[str, set[str]] = {}
    for g in _grants_for_skills(db, list(slug_set)):
        if g.user_email in member_emails:
            by_user.setdefault(g.user_email, set()).add(g.skill_slug)
    return [
        email for email in member_emails
        if slug_set.issubset(by_user.get(email, set())) and _has_agent_access(agent, email, db)
    ]


@router.post("/api/agents/{agent_id}/roles", status_code=201)
def create_agent_role(
    agent_id: str,
    body: AgentRoleBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Define a new role for this agent. Owner, collaborator, or admin."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    agent_slugs = set(_agent_skill_ids(agent))
    unknown = [s for s in body.skill_slugs if s not in agent_slugs]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Skill(s) not attached to this agent: {', '.join(unknown)}",
        )

    existing = _active_role_named(db, agent_id, body.name)
    if existing:
        raise HTTPException(
            status_code=409,
            detail=f"A role named '{body.name}' already exists on this agent.",
        )

    role = AgentRole(
        agent_id=agent_id,
        name=body.name,
        description=body.description,
        skill_slugs=json.dumps(list(dict.fromkeys(body.skill_slugs))),
        created_by_email=ctx.email,
    )
    db.add(role)
    db.commit()
    db.refresh(role)

    logger.info("Agent role created: agent=%s role=%s by=%s", agent_id, role.id, ctx.email)
    return {**agent_role_to_dict(role), **_role_member_stats(agent, role, db)}


@router.get("/api/agents/{agent_id}/roles")
def list_agent_roles(
    agent_id: str,
    request: Request,
    db: Session = Depends(_db),
    include_archived: bool = False,
):
    """List roles defined for this agent, with member stats."""
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)

    query = db.query(AgentRole).filter(AgentRole.agent_id == agent_id)
    if not include_archived:
        query = query.filter(AgentRole.archived_at.is_(None))
    roles = query.order_by(AgentRole.created_at.desc()).all()

    result = []
    for role in roles:
        d = agent_role_to_dict(role)
        d.update(_role_member_stats(agent, role, db))
        result.append(d)
    return {"roles": result, "agent_id": agent_id}


@router.get("/api/agents/{agent_id}/roles/{role_id}")
def get_agent_role(
    agent_id: str,
    role_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    return {**agent_role_to_dict(role), **_role_member_stats(agent, role, db)}


@router.put("/api/agents/{agent_id}/roles/{role_id}")
async def update_agent_role(
    agent_id: str,
    role_id: str,
    body: AgentRoleBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Edit a role's name, description, or skill bundle. Owner, collaborator, or admin.

    A skill added to the bundle is auto-granted to everyone who was already
    a "full" member (held every skill in the *old* bundle, plus agent
    access) — otherwise editing a role's definition would silently drop
    existing holders to "partial" until someone manually reassigns them.
    Members who were already partial before the edit are left alone; only
    people the role fully covered get topped up. Removing a skill from the
    bundle never revokes anything — do that separately.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    agent_slugs = set(_agent_skill_ids(agent))
    unknown = [s for s in body.skill_slugs if s not in agent_slugs]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Skill(s) not attached to this agent: {', '.join(unknown)}",
        )

    if body.name != role.name:
        existing = _active_role_named(db, agent_id, body.name, exclude_id=role.id)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=f"A role named '{body.name}' already exists on this agent.",
            )

    old_slug_set = set(_role_skill_slugs(role))
    ordered_new_slugs = list(dict.fromkeys(body.skill_slugs))
    added_slugs = sorted(set(ordered_new_slugs) - old_slug_set)
    full_members_before = _full_members_for_skills(agent, role.id, old_slug_set, db) if added_slugs else []

    role.name = body.name
    role.description = body.description
    role.skill_slugs = json.dumps(ordered_new_slugs)
    role.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(role)

    auto_granted = {"granted": 0, "failed": 0, "grants": [], "errors": []}
    if added_slugs and full_members_before:
        try:
            auto_granted = await bulk_grant_skill_access(
                skill_slugs=added_slugs,
                target_emails=full_members_before,
                granter_sub=ctx.sub,
                granter_email=ctx.email,
                grant_type="time_limited",
                expires_in_days=90,
                justification=f'Automatically granted: added to role "{role.name}"',
                restrictions={},
                db=db,
                allow_critical=ctx.is_admin,
                role_id=role.id,
                role_name=role.name,
            )
        except HTTPException as exc:
            detail = exc.detail
            errors = detail["errors"] if isinstance(detail, dict) and detail.get("errors") else [
                {"email": ", ".join(full_members_before), "error": str(detail)}
            ]
            auto_granted = {"granted": 0, "failed": len(errors), "grants": [], "errors": errors}

    logger.info(
        "Agent role updated: agent=%s role=%s by=%s added_skills=%s auto_granted=%d/%d",
        agent_id, role.id, ctx.email, added_slugs, auto_granted["granted"], len(full_members_before),
    )
    return {
        **agent_role_to_dict(role),
        **_role_member_stats(agent, role, db),
        "auto_granted": auto_granted,
    }


def _revoke_role_tagged_grants(
    db: Session,
    role_id: str,
    revoked_by: str,
    reason: str,
    *,
    user_email: Optional[str] = None,
) -> tuple[int, int]:
    """Revoke every active skill grant and agent-access grant tagged with
    this role — everything the role itself is responsible for, and nothing
    else. Scoped to one user_email when given, otherwise every current
    holder (used when the role itself is deleted)."""
    now = datetime.now(timezone.utc)

    skill_query = db.query(CrossDepartmentGrant).filter(
        CrossDepartmentGrant.role_id == role_id,
        CrossDepartmentGrant.status == STATUS_APPROVED,
    )
    if user_email:
        skill_query = skill_query.filter(CrossDepartmentGrant.user_email == user_email)
    skill_rows = skill_query.all()
    for g in skill_rows:
        g.status = STATUS_REVOKED
        g.revoked_at = now
        g.revoked_by = revoked_by
        g.revoke_reason = reason

    access_query = db.query(AgentAccessGrant).filter(
        AgentAccessGrant.role_id == role_id,
        AgentAccessGrant.status == STATUS_APPROVED,
    )
    if user_email:
        access_query = access_query.filter(AgentAccessGrant.user_email == user_email)
    access_rows = access_query.all()
    for g in access_rows:
        g.status = STATUS_REVOKED
        g.revoked_at = now
        g.revoked_by = revoked_by
        g.revoke_reason = reason

    return len(skill_rows), len(access_rows)


def _delete_agent_role(agent_id: str, role_id: str, request: Request, db: Session) -> dict:
    """Hard-delete a role. Owner, collaborator, or admin.

    Revokes every active skill grant and agent-access grant this role
    granted, for every current holder — not just the bookkeeping row.
    Access held directly, or via a different role, is untouched.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    skill_count, access_count = _revoke_role_tagged_grants(
        db, role.id, ctx.email, f'Role "{role.name}" was deleted',
    )
    db.delete(role)
    db.commit()
    logger.info(
        "Agent role deleted: agent=%s role=%s by=%s revoked_skill_grants=%d revoked_agent_access=%d",
        agent_id, role_id, ctx.email, skill_count, access_count,
    )
    return {
        "role_id": role_id,
        "deleted": True,
        "revoked_skill_grants": skill_count,
        "revoked_agent_access_grants": access_count,
    }


@router.delete("/api/agents/{agent_id}/roles/{role_id}")
def delete_agent_role(
    agent_id: str,
    role_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Delete a role. Owner, collaborator, or admin. Existing grants are untouched."""
    return _delete_agent_role(agent_id, role_id, request, db)


@router.post("/api/agents/{agent_id}/roles/{role_id}/archive")
def archive_agent_role(
    agent_id: str,
    role_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """Delete a role (legacy archive URL). Owner, collaborator, or admin. Existing grants are untouched."""
    return _delete_agent_role(agent_id, role_id, request, db)


@router.get("/api/agents/{agent_id}/roles/{role_id}/members")
def list_agent_role_members(
    agent_id: str,
    role_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    """List actual members of this role — people who hold at least one grant
    tagged with this role's id (see _role_member_emails), not anyone who
    merely happens to already hold one of its skills unrelated to it.

    For each real member, "grants" and "missing_skill_slugs" reflect their
    full current access to the bundle's skills (any origin, not just this
    role's own grants) — and "full" also requires active agent access —
    since that's what explains whether they're actually covered, not just
    what this one role happened to give them.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    slug_set = set(_role_skill_slugs(role))
    member_emails = _role_member_emails(role.id, db)
    rows = sorted(_grants_for_skills(db, list(slug_set)), key=lambda g: g.created_at, reverse=True)

    by_user: dict[str, dict] = {}
    for g in rows:
        if g.user_email not in member_emails:
            continue
        entry = by_user.setdefault(
            g.user_email, {"email": g.user_email, "name": g.user_name, "grants": []}
        )
        entry["grants"].append(
            {
                "skill_slug": g.skill_slug,
                "grant_id": g.id,
                "expires_at": g.expires_at.isoformat() if g.expires_at else None,
            }
        )

    members = []
    for email in member_emails:
        entry = by_user.get(email, {"email": email, "name": None, "grants": []})
        have = {gr["skill_slug"] for gr in entry["grants"]}
        missing = sorted(slug_set - have)
        has_agent_access = _has_agent_access(agent, email, db)
        members.append(
            {
                **entry,
                "status": "full" if not missing and has_agent_access else "partial",
                "missing_skill_slugs": missing,
                "has_agent_access": has_agent_access,
            }
        )
    members.sort(key=lambda m: m["email"])

    return {"members": members, "role": agent_role_to_dict(role)}


async def _ensure_agent_access_for_role(
    *,
    agent,
    agent_id: str,
    role_id: str,
    role_name: str,
    target_emails: list[str],
    granter_email: str,
    db: Session,
) -> dict:
    """Best-effort: grant base "User Access" to anyone in target_emails who
    doesn't already have it.

    A role only bundles skills — skills are useless if the person can't talk
    to the agent in Slack at all, so assigning a role must also grant base
    agent access, not just the skill grants.
    """
    creator_email = (agent.creator_email or "").lower()
    needing_access = [
        e for e in target_emails
        if e != creator_email and _active_grant_for_user(agent_id, e, db) is None
    ]
    if not needing_access:
        return {"granted": 0, "failed": 0, "grants": [], "errors": []}

    try:
        return await _bulk_grant_agent_access(
            agent=agent,
            agent_id=agent_id,
            target_emails=needing_access,
            granter_email=granter_email,
            granter_name=granter_email,
            justification=f'Granted via role "{role_name}"',
            expires_at=None,
            db=db,
            role_id=role_id,
            role_name=role_name,
        )
    except HTTPException:
        # Every candidate turned out to already have access (or be the
        # creator) by the time _bulk_grant_agent_access re-checked — not a
        # real failure, just a race with our own pre-filter above.
        return {"granted": 0, "failed": 0, "grants": [], "errors": []}


@router.post("/api/agents/{agent_id}/roles/{role_id}/assign", status_code=201)
async def assign_agent_role(
    agent_id: str,
    role_id: str,
    body: AgentRoleAssignBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Assign this role's full skill bundle — plus base agent access — to one or many users.

    Delegates to bulk_grant_skill_access for the skill grants — every skill
    in the role still goes through the same per-skill ACL/risk/dedup rules
    (critical-risk skills still require ctx.is_admin). The one thing this
    endpoint skips, intentionally, is the personal "you must have accessible
    access to grant this skill" check the Skill Access tab applies — that's
    the point of a role: an admin vets the bundle once, and any owner can
    assign it.

    A user who already actively holds some (or all) of the bundle's skills —
    from this role, another role, or a direct grant — is not an error: those
    skills are simply skipped and the rest are granted normally, so
    assigning a role always "succeeds" for someone who's already covered
    instead of reporting a spurious "already has an active grant" failure.

    Also ensures each target user has base "User Access" to the agent
    (skills alone don't let anyone talk to the agent in Slack). Revoking a
    role later only revokes its skill grants, not this base access — it may
    be needed for unrelated reasons, so it's not auto-stripped.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    if role.archived_at is not None:
        raise HTTPException(status_code=409, detail="This role has been archived.")

    skill_slugs = _role_skill_slugs(role)
    if not skill_slugs:
        raise HTTPException(status_code=400, detail="This role has no skills configured.")

    try:
        target_emails = body.resolved_emails()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    expires_in_days = body.expires_in_days
    if body.grant_type == "permanent":
        expires_in_days = None

    agent_access_result = await _ensure_agent_access_for_role(
        agent=agent,
        agent_id=agent_id,
        role_id=role.id,
        role_name=role.name,
        target_emails=target_emails,
        granter_email=ctx.email,
        db=db,
    )

    already_have: dict[str, set[str]] = {}
    for g in db.query(CrossDepartmentGrant).filter(
        CrossDepartmentGrant.user_email.in_(target_emails),
        CrossDepartmentGrant.skill_slug.in_(skill_slugs),
        CrossDepartmentGrant.status == STATUS_APPROVED,
    ).all():
        already_have.setdefault(g.user_email, set()).add(g.skill_slug)

    granted: list[dict] = []
    errors: list[dict] = []
    already_covered: list[str] = []
    grant_type_val: Optional[str] = None
    expires_at_val: Optional[str] = None

    for email in target_emails:
        missing = [s for s in skill_slugs if s not in already_have.get(email, set())]
        if not missing:
            already_covered.append(email)
            continue
        try:
            sub = await bulk_grant_skill_access(
                skill_slugs=missing,
                target_emails=[email],
                granter_sub=ctx.sub,
                granter_email=ctx.email,
                grant_type=body.grant_type,
                expires_in_days=expires_in_days,
                justification=body.justification.strip(),
                restrictions=body.restrictions,
                db=db,
                allow_critical=ctx.is_admin,
                role_id=role.id,
                role_name=role.name,
            )
        except HTTPException as exc:
            detail = exc.detail
            if isinstance(detail, dict) and detail.get("errors"):
                errors.extend(detail["errors"])
            else:
                errors.append({"email": email, "error": str(detail)})
            continue
        granted.extend(sub.get("grants", []))
        errors.extend(sub.get("errors", []))
        if grant_type_val is None:
            grant_type_val = sub.get("grant_type")
            expires_at_val = sub.get("expires_at")

    return {
        "granted": len(granted),
        "failed": len(errors),
        "grants": granted,
        "errors": errors,
        "already_covered": already_covered,
        "grant_type": grant_type_val,
        "expires_at": expires_at_val,
        "granted_by": ctx.email,
        "agent_access": agent_access_result,
    }


@router.post("/api/agents/{agent_id}/roles/{role_id}/members/{user_email}/revoke")
def revoke_agent_role_member(
    agent_id: str,
    role_id: str,
    user_email: str,
    body: RoleMemberRevokeBody,
    request: Request,
    db: Session = Depends(_db),
):
    """Revoke only the access this specific role granted this user —
    both the skill grants and the base agent access, if this role is what
    granted it.

    A skill (or agent access) can be held two ways: granted directly, or
    granted via a role. Revoking a role must only claw back what *that
    role* gave — it matches grants tagged with this role's id, not every
    active grant. A user who separately has direct access to one of the
    bundle's skills (or got it via a different role) keeps that access
    untouched.

    If this role never actually granted anything to the user (e.g. they
    already held every bundle skill directly before being "assigned" the
    role — see assign_agent_role's already_covered skip), there's nothing
    tagged to revoke, even though they may still show as a "full" member.
    """
    ctx = _auth(request)
    agent = _get_agent_or_404(agent_id, db)
    _ensure_agent_owner_or_admin(ctx, agent)
    role = _get_role_or_404(agent_id, role_id, db)

    reason = (body.reason or "").strip() or None
    skill_count, access_count = _revoke_role_tagged_grants(
        db, role.id, ctx.email, reason, user_email=user_email,
    )
    if skill_count == 0 and access_count == 0:
        raise HTTPException(
            status_code=404,
            detail=(
                "This role never granted this user any of its skills or agent "
                "access directly — nothing to revoke. They may already have "
                "access some other way (a direct grant or a different role)."
            ),
        )
    db.commit()

    logger.info(
        "Agent role revoked: agent=%s role=%s user=%s skill_grants=%d agent_access=%d by=%s",
        agent_id, role_id, user_email, skill_count, access_count, ctx.email,
    )
    return {
        "role_id": role_id,
        "user_email": user_email,
        "revoked": skill_count,
        "revoked_agent_access_grants": access_count,
    }


@router.get("/api/admin/users/{email}/roles")
def admin_user_roles(email: str, request: Request, db: Session = Depends(_db)):
    """Every role this user actually holds, across every agent. Admin only.

    Powers the Roles button in the platform Admin > Users directory. "Holds"
    uses the same membership gate as everywhere else in this module — an
    active grant tagged with that role's id — not mere coincidental skill
    overlap (see module docstring).
    """
    _admin_auth(request)
    email = email.strip().lower()

    role_ids = {
        r[0] for r in (
            db.query(CrossDepartmentGrant.role_id)
            .filter(
                CrossDepartmentGrant.user_email == email,
                CrossDepartmentGrant.role_id.isnot(None),
                CrossDepartmentGrant.status == STATUS_APPROVED,
            )
            .distinct()
            .all()
        )
    }
    if not role_ids:
        return {"email": email, "roles": []}

    roles = db.query(AgentRole).filter(AgentRole.id.in_(role_ids)).all()
    agents_by_id = {
        a.id: a for a in db.query(Agent).filter(Agent.id.in_({r.agent_id for r in roles})).all()
    }

    result = []
    for role in roles:
        agent = agents_by_id.get(role.agent_id)
        if agent is None:
            continue
        slug_set = set(_role_skill_slugs(role))
        grants = [g for g in _grants_for_skills(db, list(slug_set)) if g.user_email == email]
        have = {g.skill_slug for g in grants}
        missing = sorted(slug_set - have)
        has_agent_access = _has_agent_access(agent, email, db)
        result.append({
            "role_id": role.id,
            "role_name": role.name,
            "agent_id": agent.id,
            "agent_name": agent.name,
            "skill_slugs": sorted(slug_set),
            "grants": [
                {
                    "skill_slug": g.skill_slug,
                    "grant_id": g.id,
                    "expires_at": g.expires_at.isoformat() if g.expires_at else None,
                }
                for g in grants
            ],
            "missing_skill_slugs": missing,
            "has_agent_access": has_agent_access,
            "status": "full" if not missing and has_agent_access else "partial",
        })
    result.sort(key=lambda r: (r["agent_name"] or "", r["role_name"]))
    return {"email": email, "roles": result}
