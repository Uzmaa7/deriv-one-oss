"""Home page aggregate stats for the personalized landing experience."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func
from sqlalchemy.orm import Session

from access_models import AgentAccessGrant, STATUS_APPROVED, STATUS_PENDING
from agents import Agent, AgentCollaborator, AgentScheduledTask
from api_access import (
    _active_grants_for_user,
    _all_known_slugs,
    _auth,
    _db,
    _pending_requests_for_user,
    _utcnow,
)
from cf_access import AuthContext
from database import Automation
from org_directory import get_employee_by_email
from skills_acl import is_super_user, resolve_skills_for_user

logger = logging.getLogger(__name__)

router = APIRouter()

_AGENT_ACCESS_LEVELS = frozenset({"owner", "collaborator", "approved"})


def _agent_has_slack(agent: Agent) -> bool:
    return bool(agent.xoxb_secret_ref or agent.slack_bot_user_id)


def _my_access_status(agent: Agent, user_sub: str, user_email: str, db: Session) -> str:
    """Mirror api_agents._my_access_status without importing api_agents (heavy deps)."""
    from agent_access_helpers import is_agent_owner

    if is_agent_owner(agent, user_sub, user_email):
        return "owner"
    caller = (user_email or "").strip().lower()
    collab = (
        db.query(AgentCollaborator)
        .filter(
            AgentCollaborator.agent_id == agent.id,
            AgentCollaborator.user_email == caller,
        )
        .first()
    ) if caller else None
    if collab:
        return "collaborator"
    now = _utcnow()
    active = (
        db.query(AgentAccessGrant.id)
        .filter(
            AgentAccessGrant.agent_id == agent.id,
            AgentAccessGrant.user_email == user_email,
            AgentAccessGrant.status == STATUS_APPROVED,
        )
        .filter(
            (AgentAccessGrant.expires_at == None)  # noqa: E711
            | (AgentAccessGrant.expires_at > now)
        )
        .first()
    )
    if active:
        return "approved"
    pending = (
        db.query(AgentAccessGrant.id)
        .filter(
            AgentAccessGrant.agent_id == agent.id,
            AgentAccessGrant.user_email == user_email,
            AgentAccessGrant.status == STATUS_PENDING,
        )
        .first()
    )
    if pending:
        return "pending"
    return "none"


async def _skill_counts(ctx: AuthContext, db: Session) -> tuple[int, int]:
    employee = await get_employee_by_email(ctx.email)
    user_department = employee.department if employee else ""
    user_role = employee.role if employee else None
    is_exec = is_super_user(ctx.email)

    from skill_submission_models import REVIEW_STATUS_PASS as _PASS
    from skill_submission_models import SkillSubmission
    from skill_collaborators import collaborator_slugs_for_email

    owned_slugs: frozenset[str] = frozenset(
        r.proposed_slug
        for r in db.query(SkillSubmission.proposed_slug).filter(
            SkillSubmission.user_id == ctx.sub,
            SkillSubmission.review_status == _PASS,
        ).all()
    )
    collaborator_slugs = collaborator_slugs_for_email(db, ctx.email)

    active = _active_grants_for_user(ctx.sub, ctx.email, db)
    pending = _pending_requests_for_user(ctx.sub, ctx.email, db)
    results = resolve_skills_for_user(
        user_department,
        active,
        pending,
        _all_known_slugs(),
        job_title=user_role,
        is_executive=is_exec,
        owned_slugs=owned_slugs,
        collaborator_slugs=collaborator_slugs,
        email=ctx.email,
    )

    available = sum(1 for r in results if r.accessible)
    to_request = sum(1 for r in results if r.can_request and not r.accessible)
    return available, to_request


def _slack_agents_live(ctx: AuthContext, db: Session) -> int:
    email = (ctx.email or "").lower()
    agents = db.query(Agent).filter(Agent.status == "active").all()
    count = 0
    for agent in agents:
        if not _agent_has_slack(agent):
            continue
        if _my_access_status(agent, ctx.sub, email, db) in _AGENT_ACCESS_LEVELS:
            count += 1
    return count


def _automations_active(owner_sub: str, db: Session) -> int:
    return (
        db.query(func.count(Automation.id))
        .filter(Automation.owner_sub == owner_sub, Automation.is_active.is_(True))
        .scalar()
        or 0
    )


def _agent_scheduled_tasks_active(ctx: AuthContext, db: Session) -> int:
    email = (ctx.email or "").lower()
    rows = (
        db.query(AgentScheduledTask, Agent)
        .join(Agent, AgentScheduledTask.agent_id == Agent.id)
        .filter(
            AgentScheduledTask.is_active.is_(True),
            Agent.status != "decommissioned",
        )
        .all()
    )
    count = 0
    for _task, agent in rows:
        if _my_access_status(agent, ctx.sub, email, db) in ("owner", "collaborator"):
            count += 1
    return count


@router.get("/api/home/summary")
async def home_summary(request: Request, db: Session = Depends(_db)):
    """Aggregate counts for the personalized Home page stat pills."""
    ctx = _auth(request)
    try:
        skills_available, skills_to_request = await _skill_counts(ctx, db)
        return {
            "skills_available": skills_available,
            "skills_to_request": skills_to_request,
            "slack_agents_live": _slack_agents_live(ctx, db),
            "automations_active": _automations_active(ctx.sub, db),
            "agent_scheduled_tasks_active": _agent_scheduled_tasks_active(ctx, db),
        }
    except Exception:
        logger.exception("home_summary failed for sub=%s", ctx.sub[:24] if ctx.sub else "?")
        raise
