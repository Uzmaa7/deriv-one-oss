"""Skills Hub — Identity & Access Control API.

Endpoints
─────────
GET  /api/skills                    Effective skills for the current user (Tier 1 + Tier 2 grants)
POST /api/access/request            Submit a cross-dept skill access request
GET  /api/access/requests           My pending / past requests
GET  /api/access/grants             My active cross-dept grants
GET  /api/access/employee           My directory profile (department, manager, role)

Admin endpoints (is_admin required)
────────────────────────────────────
GET  /api/admin/access/pending            All pending requests (optionally filtered to approver's dept)
POST /api/admin/access/{id}/approve       Approve a request
POST /api/admin/access/{id}/deny          Deny a request
POST /api/admin/access/{id}/revoke        Revoke an active grant
PUT  /api/admin/access/{id}/extend        Extend an expiring grant
GET  /api/admin/access/grants             All active grants org-wide
GET  /api/admin/access/expiring           Grants expiring within next 14 days
GET  /api/admin/access/audit              Full audit log (last 500 actions)
POST /api/admin/access/grant-direct       Admin-initiated direct grant (one or many emails)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import re

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, validator
from sqlalchemy import or_
from sqlalchemy.orm import Session

from access_models import (
    CrossDepartmentGrant,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_EXPIRED,
    STATUS_PENDING,
    STATUS_REVOKED,
    GRANT_PERMANENT,
    GRANT_TIME_LIMITED,
    GRANT_RESTRICTED,
    compute_expires_at,
    grant_to_dict,
    init_access_tables,
)
from cf_access import build_auth_context
from database import SessionLocal
from org_directory import (
    Person,
    get_all_employees,
    get_department_managers,
    get_employee_by_email,
)
from skills_acl import (
    all_skill_acls,
    get_skill_acl,
    resolve_skills_for_user,
    skill_display_name,
    SkillAccessResult,
)


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime for SQLite comparisons.

    SQLite strips timezone info on write and returns naive datetimes on read.
    Using an offset-aware datetime in comparisons raises TypeError.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Dependency helpers ────────────────────────────────────────────────────────

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


# ── Skills directory helper ────────────────────────────────────────────────────

def _all_known_slugs() -> list[str]:
    """Slugs present in the ACL manifest.

    The ACL manifest is the authoritative source.  A skill file that exists on
    disk without a corresponding ACL entry is considered orphaned (e.g. a failed
    publish that wrote the file before the ACL could be updated) and must not be
    surfaced to users.
    """
    return sorted(all_skill_acls().keys())


# ── Active grants / pending requests helpers ──────────────────────────────────

def _user_filter(user_id: str, user_email: str):
    """Return a SQLAlchemy filter clause that matches either the Cloudflare JWT
    sub (user_id) or the employee email stored as user_id for admin-initiated
    direct grants.  Both forms are valid values for CrossDepartmentGrant.user_id.
    """
    if user_id == user_email:
        return CrossDepartmentGrant.user_id == user_id
    return or_(
        CrossDepartmentGrant.user_id == user_id,
        CrossDepartmentGrant.user_id == user_email,
    )


def _active_grants_for_user(user_id: str, user_email: str, db: Session) -> list[dict]:
    now = _utcnow()
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(
            _user_filter(user_id, user_email),
            CrossDepartmentGrant.status == STATUS_APPROVED,
        )
        .all()
    )
    valid = []
    for g in rows:
        if g.expires_at and g.expires_at < now:
            g.status = STATUS_EXPIRED
            db.commit()
            continue
        valid.append(grant_to_dict(g))
    return valid


def _pending_requests_for_user(user_id: str, user_email: str, db: Session) -> list[dict]:
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(
            _user_filter(user_id, user_email),
            CrossDepartmentGrant.status == STATUS_PENDING,
        )
        .all()
    )
    return [grant_to_dict(g) for g in rows]


# ── Request body models ────────────────────────────────────────────────────────

class AccessRequestBody(BaseModel):
    skill_slug: str
    justification: str

    @validator("justification")
    def justification_not_empty(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 20:
            raise ValueError("Justification must be at least 20 characters.")
        if len(stripped) > 2000:
            raise ValueError("Justification must be under 2000 characters.")
        return stripped

    @validator("skill_slug")
    def skill_slug_valid(cls, v: str) -> str:
        import re
        if not re.match(r"^[a-z0-9][a-z0-9\-]{0,63}$", v):
            raise ValueError("Invalid skill slug.")
        return v


class ApproveBody(BaseModel):
    grant_type: str = "time_limited"          # permanent | time_limited | restricted
    expires_in_days: Optional[int] = None
    default_grant_override: Optional[str] = None  # e.g. "time_limited_30d"
    restrictions: dict = {}


class DenyBody(BaseModel):
    reason: str

    @validator("reason")
    def reason_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Denial reason is required.")
        return v.strip()


class RevokeBody(BaseModel):
    reason: str


class ExtendBody(BaseModel):
    extend_by_days: int


_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$")
_MAX_BULK_GRANT_EMAILS = 50


def _normalize_email_list(
    emails: Optional[list[str]] = None,
    email: Optional[str] = None,
) -> list[str]:
    """Merge email / emails fields into a deduped, validated list (max 50)."""
    raw: list[str] = []
    if emails:
        for item in emails:
            if not isinstance(item, str):
                continue
            # Allow comma-separated values inside list items
            raw.extend(part.strip() for part in item.split(",") if part.strip())
    if email:
        raw.extend(part.strip() for part in email.split(",") if part.strip())

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


class AdminDirectGrantBody(BaseModel):
    """Body for the admin-initiated direct grant endpoint.

    Accepts a single ``email``, a list ``emails``, or comma-separated values
    in either field. All recipients share the same grant_type / duration / note.
    """

    email: Optional[str] = None
    emails: Optional[list[str]] = None
    skill_slug: str
    grant_type: str = "time_limited"       # permanent | time_limited | restricted
    expires_in_days: Optional[int] = None  # 1–365; required for time_limited/restricted
    justification: str                     # admin note, 10–2000 chars
    restrictions: dict = {}

    @validator("skill_slug")
    def skill_slug_valid(cls, v: str) -> str:
        if not re.match(r"^[a-z0-9][a-z0-9\-]{0,63}$", v):
            raise ValueError("Invalid skill slug.")
        return v

    @validator("grant_type")
    def grant_type_valid(cls, v: str) -> str:
        if v not in ("permanent", "time_limited", "restricted"):
            raise ValueError("grant_type must be permanent, time_limited, or restricted.")
        return v

    @validator("expires_in_days")
    def expires_range(cls, v: Optional[int]) -> Optional[int]:
        if v is not None and not (1 <= v <= 365):
            raise ValueError("expires_in_days must be between 1 and 365.")
        return v

    @validator("justification")
    def justification_valid(cls, v: str) -> str:
        stripped = v.strip()
        if len(stripped) < 10:
            raise ValueError("Justification must be at least 10 characters.")
        if len(stripped) > 2000:
            raise ValueError("Justification must be under 2000 characters.")
        return stripped

    def resolved_emails(self) -> list[str]:
        return _normalize_email_list(emails=self.emails, email=self.email)


# ── GET /api/skills ────────────────────────────────────────────────────────────

@router.get("/api/skills")
async def get_my_skills(request: Request, db: Session = Depends(_db)):
    """Return all known skills enriched with the current user's access status."""
    ctx = _auth(request)

    employee: Optional[Person] = await get_employee_by_email(ctx.email)
    user_department = employee.department if employee else ""
    user_name = employee.name if employee else ""
    user_role = employee.role if employee else None

    active = _active_grants_for_user(ctx.sub, ctx.email, db)
    pending = _pending_requests_for_user(ctx.sub, ctx.email, db)

    from skills_acl import is_super_user
    from skill_submission_models import SkillSubmission, SkillSecret
    from skill_submission_models import REVIEW_STATUS_PASS as _PASS
    from skill_collaborators import collaborator_slugs_for_email

    is_exec = is_super_user(ctx.email)

    # Bulk-load ownership and secrets flags for all skills at once (avoid N+1).
    owned_slugs: frozenset[str] = frozenset(
        r.proposed_slug
        for r in db.query(SkillSubmission.proposed_slug).filter(
            SkillSubmission.user_id == ctx.sub,
            SkillSubmission.review_status == _PASS,
        ).all()
    )
    collaborator_slugs = collaborator_slugs_for_email(db, ctx.email)
    secret_slugs: set[str] = {
        r.slug for r in db.query(SkillSecret.slug).distinct().all()
    }

    slugs = _all_known_slugs()

    from skill_system_links_model import get_system_links_map, max_criticality_for_links
    system_links_by_slug = get_system_links_map(db, slugs)
    results = resolve_skills_for_user(
        user_department,
        active,
        pending,
        slugs,
        job_title=user_role,
        is_executive=is_exec,
        owned_slugs=owned_slugs,
        collaborator_slugs=collaborator_slugs,
        email=ctx.email,
    )

    acls = all_skill_acls()

    return {
        "employee": {
            "email": ctx.email,
            "department": user_department,
            "name": user_name,
            "role": user_role,
            "manager": employee.manager if employee else None,
            "iam_resolved": employee is not None,
            "is_admin": ctx.is_admin or is_exec,
            "is_platform_admin": ctx.is_admin,
        },
        "skills": [
            {
                "slug": r.slug,
                "accessible": r.accessible,
                "access_source": r.access_source,
                "tier1": r.tier1,
                "tier2": r.tier2,
                "grant_id": r.grant_id,
                "expires_at": r.expires_at,
                "restrictions": r.restrictions,
                "pending_request_id": r.pending_request_id,
                "can_request": r.can_request,
                "risk_level": r.acl.risk_level if r.acl else "standard",
                "cross_dept_policy": r.acl.cross_dept_policy if r.acl else "requestable",
                "cross_dept_default_grant": r.acl.cross_dept_default_grant if r.acl else "time_limited_30d",
                "cross_dept_approvers": r.acl.cross_dept_approvers if r.acl else [],
                "departments": r.acl.departments if r.acl else [],
                "integrations": r.acl.integrations if r.acl else [],
                "description": r.acl.description if r.acl else "",
                "name": skill_display_name(r.acl, r.slug),
                "owning_department": r.acl.owning_department if r.acl else "",
                "is_owner": r.slug in owned_slugs,
                "is_collaborator": r.slug in collaborator_slugs,
                "can_manage": (
                    r.slug in owned_slugs
                    or r.slug in collaborator_slugs
                    or ctx.is_admin
                    or is_exec
                ),
                "has_secrets": r.slug in secret_slugs,
                "system_links": [
                    {
                        "integration_tag": link.integration_tag,
                        "system_name": link.system_name,
                        "risk_level": link.risk_level,
                        "match_confidence": link.match_confidence,
                        "source": link.source,
                        "direction": link.direction,
                    }
                    for link in system_links_by_slug.get(r.slug, [])
                ],
                "max_system_risk_level": max_criticality_for_links(system_links_by_slug.get(r.slug, [])),
            }
            for r in results
        ],
    }


# ── GET /api/access/employee ──────────────────────────────────────────────────

@router.get("/api/access/employee")
async def get_my_employee_profile(request: Request):
    """Return the current user's the directory employee profile (department, manager, role)."""
    ctx = _auth(request)
    employee = await get_employee_by_email(ctx.email)
    if not employee:
        return {
            "resolved": False,
            "email": ctx.email,
            "department": None,
            "manager": None,
            "role": None,
            "name": None,
        }
    return {
        "resolved": True,
        "email": employee.email,
        "name": employee.name,
        "department": employee.department,
        "manager": employee.manager,
        "role": employee.role,
        "team": employee.team,
        "team_level1": employee.team_level1,
        "employee_status": employee.employee_status,
    }


# ── POST /api/access/request ──────────────────────────────────────────────────

@router.post("/api/access/request", status_code=201)
async def submit_access_request(
    request: Request,
    body: AccessRequestBody,
    db: Session = Depends(_db),
):
    """Submit a cross-department skill access request."""
    ctx = _auth(request)

    acl = get_skill_acl(body.skill_slug)
    if acl is None:
        raise HTTPException(status_code=404, detail="Skill not found in ACL manifest.")

    if acl.cross_dept_policy == "blocked":
        raise HTTPException(
            status_code=403,
            detail="This skill does not allow cross-department access.",
        )

    # Check user already has access
    employee = await get_employee_by_email(ctx.email)
    user_dept = employee.department if employee else ""
    if acl.user_has_tier1_access(user_dept):
        raise HTTPException(
            status_code=409,
            detail="You already have home-department access to this skill.",
        )

    # Check for duplicate active grant
    existing_grant = (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.user_id == ctx.sub,
            CrossDepartmentGrant.skill_slug == body.skill_slug,
            CrossDepartmentGrant.status == STATUS_APPROVED,
        )
        .first()
    )
    if existing_grant:
        raise HTTPException(status_code=409, detail="You already have an active grant for this skill.")

    # Check for duplicate pending request
    existing_pending = (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.user_id == ctx.sub,
            CrossDepartmentGrant.skill_slug == body.skill_slug,
            CrossDepartmentGrant.status == STATUS_PENDING,
        )
        .first()
    )
    if existing_pending:
        raise HTTPException(status_code=409, detail="You already have a pending request for this skill.")

    # Resolve approvers from the directory.
    # Critical-risk skills bypass department-manager routing entirely — they go
    # straight to the platform admin review queue (visible at /admin).
    approver_emails: list[str] = []
    if acl.risk_level != "critical":
        approver_depts = acl.cross_dept_approvers or [acl.owning_department]
        for dept in approver_depts:
            managers = await get_department_managers(dept)
            approver_emails.extend(m.email for m in managers if m.email)

    grant = CrossDepartmentGrant(
        user_id=ctx.sub,
        user_email=ctx.email,
        user_name=employee.name if employee else "",
        user_department=user_dept,
        skill_slug=body.skill_slug,
        skill_name=acl.description or body.skill_slug,
        skill_department=acl.owning_department,
        skill_risk_level=acl.risk_level,
        justification=body.justification,
        status=STATUS_PENDING,
    )
    db.add(grant)
    db.commit()
    db.refresh(grant)

    logger.info(
        "access_request submitted: id=%s user=%s skill=%s approvers=%s",
        grant.id,
        ctx.email,
        body.skill_slug,
        approver_emails,
    )

    if acl.risk_level == "critical":
        message = (
            "Your request has been submitted. "
            "This is a critical-risk skill — it requires admin approval before access is granted."
        )
    elif approver_emails:
        message = (
            "Your request has been submitted. "
            f"Approver{'s' if len(approver_emails) != 1 else ''} notified."
        )
    else:
        message = "Your request has been submitted and is awaiting admin review."

    return {
        "request_id": grant.id,
        "status": STATUS_PENDING,
        "skill": body.skill_slug,
        "skill_department": acl.owning_department,
        "risk_level": acl.risk_level,
        "routed_to": approver_emails,
        "message": message,
    }


# ── GET /api/access/requests ──────────────────────────────────────────────────

@router.get("/api/access/requests")
def get_my_requests(request: Request, db: Session = Depends(_db)):
    """Return all my cross-dept access requests (any status)."""
    ctx = _auth(request)
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(_user_filter(ctx.sub, ctx.email))
        .order_by(CrossDepartmentGrant.requested_at.desc())
        .limit(100)
        .all()
    )
    return {"requests": [grant_to_dict(g) for g in rows]}


# ── GET /api/access/grants ────────────────────────────────────────────────────

@router.get("/api/access/grants")
def get_my_grants(request: Request, db: Session = Depends(_db)):
    """Return my active cross-dept skill grants."""
    ctx = _auth(request)
    grants = _active_grants_for_user(ctx.sub, ctx.email, db)
    return {"grants": grants}


# ── Admin: GET /api/admin/access/pending ──────────────────────────────────────

@router.get("/api/admin/access/pending")
def admin_list_pending(request: Request, db: Session = Depends(_db)):
    ctx = _admin_auth(request)
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(CrossDepartmentGrant.status == STATUS_PENDING)
        .order_by(CrossDepartmentGrant.requested_at.asc())
        .all()
    )
    return {"pending": [grant_to_dict(g) for g in rows]}


# ── Admin: POST /api/admin/access/{id}/approve ────────────────────────────────

@router.post("/api/admin/access/{grant_id}/approve")
def admin_approve(
    grant_id: str,
    request: Request,
    body: ApproveBody,
    db: Session = Depends(_db),
):
    ctx = _admin_auth(request)
    g = db.query(CrossDepartmentGrant).filter(CrossDepartmentGrant.id == grant_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Request not found.")
    if g.status != STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"Request is already {g.status}.")

    acl = get_skill_acl(g.skill_slug)
    default_grant_str = acl.cross_dept_default_grant if acl else "time_limited_30d"
    override = body.default_grant_override or default_grant_str

    # Map grant_type string to enum
    if body.grant_type == "permanent":
        g.grant_type = GRANT_PERMANENT
        g.expires_at = None
    elif body.grant_type == "restricted":
        g.grant_type = GRANT_RESTRICTED
        g.expires_at = compute_expires_at(override, body.expires_in_days)
    else:
        g.grant_type = GRANT_TIME_LIMITED
        g.expires_at = compute_expires_at(override, body.expires_in_days)

    g.status = STATUS_APPROVED
    g.decided_by = ctx.sub
    g.decided_by_email = ctx.email
    g.decided_at = datetime.now(timezone.utc)
    g.restrictions = json.dumps(body.restrictions)

    db.commit()

    logger.info(
        "access approved: id=%s skill=%s user=%s by=%s expires=%s",
        grant_id, g.skill_slug, g.user_email, ctx.email, g.expires_at,
    )

    return {
        "grant_id": grant_id,
        "status": STATUS_APPROVED,
        "user": g.user_email,
        "skill": g.skill_slug,
        "grant_type": g.grant_type,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "effective_immediately": True,
    }


# ── Admin: POST /api/admin/access/{id}/deny ───────────────────────────────────

@router.post("/api/admin/access/{grant_id}/deny")
def admin_deny(
    grant_id: str,
    request: Request,
    body: DenyBody,
    db: Session = Depends(_db),
):
    ctx = _admin_auth(request)
    g = db.query(CrossDepartmentGrant).filter(CrossDepartmentGrant.id == grant_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Request not found.")
    if g.status != STATUS_PENDING:
        raise HTTPException(status_code=409, detail=f"Request is already {g.status}.")

    g.status = STATUS_DENIED
    g.decided_by = ctx.sub
    g.decided_by_email = ctx.email
    g.decided_at = datetime.now(timezone.utc)
    g.deny_reason = body.reason

    db.commit()

    logger.info("access denied: id=%s skill=%s user=%s by=%s", grant_id, g.skill_slug, g.user_email, ctx.email)

    return {"grant_id": grant_id, "status": STATUS_DENIED, "user": g.user_email, "skill": g.skill_slug}


# ── Admin: POST /api/admin/access/{id}/revoke ─────────────────────────────────

@router.post("/api/admin/access/{grant_id}/revoke")
def admin_revoke(
    grant_id: str,
    request: Request,
    body: RevokeBody,
    db: Session = Depends(_db),
):
    ctx = _admin_auth(request)
    g = db.query(CrossDepartmentGrant).filter(CrossDepartmentGrant.id == grant_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if g.status != STATUS_APPROVED:
        raise HTTPException(status_code=409, detail=f"Grant is not active (status: {g.status}).")

    g.status = STATUS_REVOKED
    g.revoked_at = datetime.now(timezone.utc)
    g.revoked_by = ctx.email
    g.revoke_reason = body.reason

    db.commit()

    logger.info("access revoked: id=%s skill=%s user=%s by=%s", grant_id, g.skill_slug, g.user_email, ctx.email)

    return {"grant_id": grant_id, "status": STATUS_REVOKED}


# ── Admin: PUT /api/admin/access/{id}/extend ──────────────────────────────────

@router.put("/api/admin/access/{grant_id}/extend")
def admin_extend(
    grant_id: str,
    request: Request,
    body: ExtendBody,
    db: Session = Depends(_db),
):
    ctx = _admin_auth(request)
    if body.extend_by_days < 1 or body.extend_by_days > 365:
        raise HTTPException(status_code=422, detail="extend_by_days must be 1–365.")
    g = db.query(CrossDepartmentGrant).filter(CrossDepartmentGrant.id == grant_id).first()
    if not g:
        raise HTTPException(status_code=404, detail="Grant not found.")
    if g.status != STATUS_APPROVED:
        raise HTTPException(status_code=409, detail=f"Grant is not active (status: {g.status}).")

    base = g.expires_at if (g.expires_at and g.expires_at > _utcnow()) else _utcnow()
    g.expires_at = base + timedelta(days=body.extend_by_days)
    db.commit()

    return {"grant_id": grant_id, "expires_at": g.expires_at.isoformat()}


# ── Admin: GET /api/admin/access/grants ───────────────────────────────────────

@router.get("/api/admin/access/grants")
def admin_all_grants(request: Request, db: Session = Depends(_db)):
    ctx = _admin_auth(request)
    now = _utcnow()
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.status == STATUS_APPROVED,
        )
        .order_by(CrossDepartmentGrant.decided_at.desc())
        .all()
    )
    valid = []
    for g in rows:
        if g.expires_at and g.expires_at < now:
            g.status = STATUS_EXPIRED
            db.commit()
            continue
        valid.append(grant_to_dict(g))
    return {"grants": valid}


# ── Admin: GET /api/admin/access/expiring ─────────────────────────────────────

@router.get("/api/admin/access/expiring")
def admin_expiring(request: Request, db: Session = Depends(_db)):
    ctx = _admin_auth(request)
    now = _utcnow()
    cutoff = now + timedelta(days=14)
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(
            CrossDepartmentGrant.status == STATUS_APPROVED,
            CrossDepartmentGrant.expires_at != None,  # noqa: E711
            CrossDepartmentGrant.expires_at > now,
            CrossDepartmentGrant.expires_at <= cutoff,
        )
        .order_by(CrossDepartmentGrant.expires_at.asc())
        .all()
    )
    return {"expiring": [grant_to_dict(g) for g in rows]}


# ── Shared bulk skill grant helper ───────────────────────────────────────────

async def bulk_grant_skill_access(
    *,
    skill_slugs: list[str],
    target_emails: list[str],
    granter_sub: str,
    granter_email: str,
    grant_type: str,
    expires_in_days: Optional[int],
    justification: str,
    restrictions: dict,
    db: Session,
    allow_critical: bool = False,
    role_id: Optional[str] = None,
    role_name: Optional[str] = None,
) -> dict:
    """Grant one or more skills to one or many users.

    Returns partial-success shape: ``{ granted, failed, grants[], errors[] }``.
    Each error entry includes ``email``, ``error``, and ``skill_slug`` when
    multiple skills are involved.
    """
    if not skill_slugs:
        raise HTTPException(status_code=422, detail="At least one skill slug is required.")

    now = datetime.now(timezone.utc)
    if grant_type == "permanent":
        grant_type_val = GRANT_PERMANENT
        expires_at = None
    elif grant_type == "restricted":
        grant_type_val = GRANT_RESTRICTED
        expires_at = compute_expires_at("", expires_in_days or 30)
    else:
        grant_type_val = GRANT_TIME_LIMITED
        expires_at = compute_expires_at("", expires_in_days) if expires_in_days else None

    granted: list[dict] = []
    errors: list[dict] = []

    for skill_slug in skill_slugs:
        acl = get_skill_acl(skill_slug)
        if acl is None:
            for target_email in target_emails:
                errors.append({
                    "email": target_email,
                    "skill_slug": skill_slug,
                    "error": "Skill not found in ACL manifest.",
                })
            continue

        if acl.cross_dept_policy == "blocked":
            for target_email in target_emails:
                errors.append({
                    "email": target_email,
                    "skill_slug": skill_slug,
                    "error": "This skill does not allow cross-department access.",
                })
            continue

        if acl.risk_level == "critical" and not allow_critical:
            for target_email in target_emails:
                errors.append({
                    "email": target_email,
                    "skill_slug": skill_slug,
                    "error": "Critical-risk skills require platform admin approval.",
                })
            continue

        skill_name = acl.description or skill_slug

        for target_email in target_emails:
            employee: Optional[Person] = await get_employee_by_email(target_email)
            if employee is None:
                errors.append({
                    "email": target_email,
                    "skill_slug": skill_slug,
                    "error": "No active person found for this email address in the org directory.",
                })
                continue

            if employee.email.lower() == granter_email.lower():
                errors.append({
                    "email": employee.email,
                    "skill_slug": skill_slug,
                    "error": "Use the standard access mechanisms for your own account.",
                })
                continue

            if acl.user_has_tier1_access(employee.department):
                errors.append({
                    "email": employee.email,
                    "skill_slug": skill_slug,
                    "error": (
                        f"{employee.name or employee.email} already has Tier 1 (department) "
                        "access to this skill — a grant is not needed."
                    ),
                })
                continue

            existing_active = (
                db.query(CrossDepartmentGrant)
                .filter(
                    or_(
                        CrossDepartmentGrant.user_id == employee.email,
                        CrossDepartmentGrant.user_email == employee.email,
                    ),
                    CrossDepartmentGrant.skill_slug == skill_slug,
                    CrossDepartmentGrant.status == STATUS_APPROVED,
                )
                .first()
            )
            if existing_active:
                errors.append({
                    "email": employee.email,
                    "skill_slug": skill_slug,
                    "error": f"{employee.name or employee.email} already has an active grant for this skill.",
                })
                continue

            existing_pending = (
                db.query(CrossDepartmentGrant)
                .filter(
                    or_(
                        CrossDepartmentGrant.user_id == employee.email,
                        CrossDepartmentGrant.user_email == employee.email,
                    ),
                    CrossDepartmentGrant.skill_slug == skill_slug,
                    CrossDepartmentGrant.status == STATUS_PENDING,
                )
                .first()
            )
            if existing_pending:
                errors.append({
                    "email": employee.email,
                    "skill_slug": skill_slug,
                    "error": (
                        f"{employee.name or employee.email} already has a pending request for this skill. "
                        "Approve or deny it instead."
                    ),
                })
                continue

            grant = CrossDepartmentGrant(
                user_id=employee.email,
                user_email=employee.email,
                user_name=employee.name or "",
                user_department=employee.department or "",
                skill_slug=skill_slug,
                skill_name=skill_name,
                skill_department=acl.owning_department,
                skill_risk_level=acl.risk_level,
                justification=justification,
                status=STATUS_APPROVED,
                grant_type=grant_type_val,
                expires_at=expires_at,
                restrictions=json.dumps(restrictions),
                decided_by=granter_sub,
                decided_by_email=granter_email,
                decided_at=now,
                role_id=role_id,
                role_name=role_name,
            )
            db.add(grant)
            db.flush()

            result = {
                "grant_id": grant.id,
                "status": STATUS_APPROVED,
                "user": {
                    "email": employee.email,
                    "name": employee.name,
                    "department": employee.department,
                    "role": employee.role,
                },
                "skill_slug": skill_slug,
                "skill_name": skill_name,
                "grant_type": grant_type_val,
                "expires_at": grant.expires_at.isoformat() if grant.expires_at else None,
                "granted_by": granter_email,
                "granted_at": now.isoformat(),
                "role_id": role_id,
                "role_name": role_name,
            }
            granted.append(result)
            logger.info(
                "skill_access_granted: id=%s granter=%s -> user=%s skill=%s grant_type=%s expires=%s",
                grant.id, granter_email, employee.email, skill_slug, grant_type_val, expires_at,
            )

    if granted:
        db.commit()
    else:
        db.rollback()

    if not granted:
        first = errors[0]["error"] if errors else "Grant failed."
        if len(target_emails) == 1 and len(skill_slugs) == 1:
            lower = first.lower()
            if "no active employee" in lower:
                raise HTTPException(status_code=404, detail=first)
            if "own account" in lower:
                raise HTTPException(status_code=400, detail=first)
            raise HTTPException(status_code=409, detail=first)
        raise HTTPException(
            status_code=422,
            detail={"message": "All grants failed.", "errors": errors},
        )

    response: dict = {
        "granted": len(granted),
        "failed": len(errors),
        "grants": granted,
        "errors": errors,
        "grant_type": grant_type_val,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "granted_by": granter_email,
        "granted_at": now.isoformat(),
    }
    if len(skill_slugs) == 1:
        response["skill_slug"] = skill_slugs[0]
        acl = get_skill_acl(skill_slugs[0])
        response["skill_name"] = (acl.description if acl else None) or skill_slugs[0]
    if len(granted) == 1 and not errors:
        response.update(granted[0])
    return response


# ── Admin: POST /api/admin/access/grant-direct ────────────────────────────────

@router.post("/api/admin/access/grant-direct", status_code=201)
async def admin_grant_direct(
    request: Request,
    body: AdminDirectGrantBody,
    db: Session = Depends(_db),
):
    """Admin-initiated direct grant — bypass the user request/approve cycle.

    Accepts one or many emails (``email``, ``emails``, or comma-separated).
    All recipients share the same grant_type, duration, and admin note.
    Looks up each person via the org directory, guards against duplicates, then
    creates CrossDepartmentGrant rows in STATUS_APPROVED.

    Response always includes ``grants`` / ``errors`` / ``granted`` / ``failed``.
    For a single successful grant the legacy top-level fields are also present.
    """
    ctx = _admin_auth(request)

    try:
        target_emails = body.resolved_emails()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if get_skill_acl(body.skill_slug) is None:
        raise HTTPException(status_code=404, detail="Skill not found in ACL manifest.")

    return await bulk_grant_skill_access(
        skill_slugs=[body.skill_slug],
        target_emails=target_emails,
        granter_sub=ctx.sub,
        granter_email=ctx.email,
        grant_type=body.grant_type,
        expires_in_days=body.expires_in_days,
        justification=body.justification,
        restrictions=body.restrictions,
        db=db,
        allow_critical=True,
    )


# ── Admin: GET /api/admin/access/audit ───────────────────────────────────────

@router.get("/api/admin/access/audit")
def admin_audit(request: Request, db: Session = Depends(_db)):
    ctx = _admin_auth(request)
    rows = (
        db.query(CrossDepartmentGrant)
        .filter(CrossDepartmentGrant.status != STATUS_PENDING)
        .order_by(CrossDepartmentGrant.decided_at.desc())
        .limit(500)
        .all()
    )
    return {"audit": [grant_to_dict(g) for g in rows]}


# ── Admin: GET /api/admin/directory ──────────────────────────────────────────

@router.get("/api/admin/directory")
async def admin_directory(request: Request, db: Session = Depends(_db)):
    """Return all active employees joined with their skill access data.

    Combines:
    - the directory employee list (all active employees)
    - Tier 1 skill access derived from ACL manifest + employee department
    - Tier 2 cross-dept grants (approved) from DB
    - Pending requests from DB

    Response is cached implicitly by the IAM client (5 min TTL).
    """
    _admin_auth(request)

    # 1. Fetch all employees from the directory
    employees = await get_all_employees()

    # 2. Load all skill ACLs from manifest
    acls = all_skill_acls()  # dict[slug, SkillAcl]

    # 3. Pull all approved grants from DB, keyed by lower-case email
    approved_rows = (
        db.query(CrossDepartmentGrant)
        .filter(CrossDepartmentGrant.status == STATUS_APPROVED)
        .all()
    )
    now = _utcnow()
    granted_by_email: dict[str, list[dict]] = {}
    for g in approved_rows:
        if g.expires_at and g.expires_at < now:
            continue
        key = (g.user_email or "").lower()
        if key not in granted_by_email:
            granted_by_email[key] = []
        granted_by_email[key].append({
            "skill_slug": g.skill_slug,
            "grant_type": g.grant_type,
            "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        })

    # 4. Pull pending requests keyed by lower-case email
    pending_rows = (
        db.query(CrossDepartmentGrant)
        .filter(CrossDepartmentGrant.status == STATUS_PENDING)
        .all()
    )
    pending_by_email: dict[str, list[str]] = {}
    for g in pending_rows:
        key = (g.user_email or "").lower()
        if key not in pending_by_email:
            pending_by_email[key] = []
        pending_by_email[key].append(g.skill_slug)

    # 4b. Build a map of email -> owned skill slugs (published skills).
    # Critical-risk skills are only accessible to their owner, so we need this
    # per-employee to correctly compute Tier 1 access in the directory view.
    from skill_submission_models import SkillSubmission
    from skill_submission_models import REVIEW_STATUS_PASS as _PASS
    owned_rows = db.query(SkillSubmission.user_email, SkillSubmission.proposed_slug).filter(
        SkillSubmission.review_status == _PASS,
    ).all()
    owned_by_email: dict[str, frozenset[str]] = {}
    for row in owned_rows:
        key = (row.user_email or "").lower()
        owned_by_email[key] = owned_by_email.get(key, frozenset()) | frozenset([row.proposed_slug])

    # 5. Build employee records with skill access
    from skills_acl import resolve_tier1_slugs as _resolve_tier1
    employee_list = []
    for emp in employees:
        dept = emp.department or ""
        email_key = emp.email.lower()
        emp_owned = owned_by_email.get(email_key, frozenset())
        tier1_slugs = sorted(
            _resolve_tier1(dept, job_title=emp.role or None, owned_slugs=emp_owned, email=emp.email)
        )
        employee_list.append({
            "email": emp.email,
            "name": emp.name,
            "department": dept,
            "sage_department": emp.sage_department,
            "job_title": emp.role or "",
            "manager": emp.manager or "",
            "team_level2": emp.team_level2 or "",
            "skills": {
                "tier1": sorted(tier1_slugs),
                "granted": granted_by_email.get(email_key, []),
                "pending": pending_by_email.get(email_key, []),
            },
        })

    # 6. Build skill metadata for the frontend
    skill_meta = {
        slug: {
            "name": slug.replace("-", " ").title(),
            "risk_level": acl.risk_level,
            "departments": acl.departments,
            "description": acl.description,
            "is_universal": acl.is_universal,
        }
        for slug, acl in acls.items()
    }

    return {
        "employees": employee_list,
        "skill_meta": skill_meta,
        "total": len(employee_list),
    }
