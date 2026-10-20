"""SQLAlchemy ORM models for the Skills Hub access control system.

Tables:
  cross_department_grants  — skill-level requests + grants lifecycle (pending → approved/denied)
  agent_access_grants      — agent-level individual access grants (pending → approved/denied)
  agent_roles              — named, reusable bundles of skills scoped to one agent
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import text as sa_text

from database import Base, engine

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


# Grant / request status values
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_DENIED = "denied"
STATUS_EXPIRED = "expired"
STATUS_REVOKED = "revoked"

# Grant type values
GRANT_PERMANENT = "permanent"
GRANT_TIME_LIMITED = "time_limited"
GRANT_RESTRICTED = "restricted"

# Risk level values
RISK_STANDARD = "standard"
RISK_ELEVATED = "elevated"
RISK_CRITICAL = "critical"


class CrossDepartmentGrant(Base):
    """Unified table for cross-department skill access requests and grants.

    A row starts as status='pending' when submitted, then transitions to
    'approved', 'denied', 'expired', or 'revoked'.
    """

    __tablename__ = "cross_department_grants"

    id = Column(String, primary_key=True, default=_uuid)

    # Requester
    user_id = Column(String, nullable=False, index=True)   # Cloudflare sub / email
    user_email = Column(String, nullable=False)
    user_name = Column(String, nullable=True)
    user_department = Column(String, nullable=False, default="")

    # Skill
    skill_slug = Column(String, nullable=False, index=True)
    skill_name = Column(String, nullable=False, default="")
    skill_department = Column(String, nullable=False, default="")
    skill_risk_level = Column(String, nullable=False, default=RISK_STANDARD)

    # Request phase
    justification = Column(Text, nullable=False)
    requested_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Approval phase
    status = Column(String, nullable=False, default=STATUS_PENDING, index=True)
    decided_by = Column(String, nullable=True)         # approver's user_id
    decided_by_email = Column(String, nullable=True)
    decided_by_name = Column(String, nullable=True)
    decided_at = Column(DateTime, nullable=True)
    deny_reason = Column(Text, nullable=True)

    # Grant configuration (populated on approval)
    grant_type = Column(String, nullable=True)          # permanent | time_limited | restricted
    expires_at = Column(DateTime, nullable=True)
    # restrictions stored as JSON string: {"interactive_only": true, "read_only": true, ...}
    restrictions = Column(Text, nullable=True, default="{}")

    # Usage tracking
    last_used_at = Column(DateTime, nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)

    # Revocation
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String, nullable=True)
    revoke_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Role bundle this grant was created from (nullable — most grants are ad hoc).
    # role_name is denormalized so audit history survives a role rename/delete.
    role_id = Column(String, nullable=True, index=True)
    role_name = Column(String, nullable=True)


def grant_to_dict(g: CrossDepartmentGrant) -> dict[str, Any]:
    return {
        "id": g.id,
        "user_id": g.user_id,
        "user_email": g.user_email,
        "user_name": g.user_name,
        "user_department": g.user_department,
        "skill_slug": g.skill_slug,
        "skill_name": g.skill_name,
        "skill_department": g.skill_department,
        "skill_risk_level": g.skill_risk_level,
        "justification": g.justification,
        "requested_at": g.requested_at.isoformat() if g.requested_at else None,
        "status": g.status,
        "decided_by": g.decided_by,
        "decided_by_email": g.decided_by_email,
        "decided_by_name": g.decided_by_name,
        "decided_at": g.decided_at.isoformat() if g.decided_at else None,
        "deny_reason": g.deny_reason,
        "grant_type": g.grant_type,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "restrictions": g.restrictions,
        "last_used_at": g.last_used_at.isoformat() if g.last_used_at else None,
        "usage_count": g.usage_count,
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "revoked_by": g.revoked_by,
        "revoke_reason": g.revoke_reason,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "role_id": g.role_id,
        "role_name": g.role_name,
    }


def compute_expires_at(grant_type_str: str, expires_in_days: int | None = None) -> datetime | None:
    """Return the expiry datetime for a given grant type string."""
    mapping = {
        "time_limited_30d": timedelta(days=30),
        "time_limited_90d": timedelta(days=90),
        "restricted_read_only": timedelta(days=30),
    }
    if grant_type_str in mapping:
        return datetime.now(timezone.utc) + mapping[grant_type_str]
    if expires_in_days:
        return datetime.now(timezone.utc) + timedelta(days=expires_in_days)
    return None


class AgentAccessGrant(Base):
    """Individual agent-level access grant.

    A row starts as status='pending' when a user requests access, then
    transitions to 'approved', 'denied', or 'revoked'.  No department-wide
    access is supported — every grant is tied to a specific user email.
    """

    __tablename__ = "agent_access_grants"

    id = Column(String, primary_key=True, default=_uuid)

    # Which agent
    agent_id = Column(String, nullable=False, index=True)

    # Who is being granted access
    user_email = Column(String(320), nullable=False, index=True)
    user_name = Column(String(255), nullable=True)

    # Status lifecycle: pending → approved | denied | revoked
    status = Column(String(20), nullable=False, default=STATUS_PENDING, index=True)

    # Optional justification from the requester
    justification = Column(Text, nullable=True)

    # Admin who actioned the grant
    granted_by_email = Column(String(320), nullable=True)
    granted_by_name = Column(String(255), nullable=True)

    # Approval / denial details
    decided_at = Column(DateTime, nullable=True)
    deny_reason = Column(Text, nullable=True)

    # Optional expiry (None = permanent)
    expires_at = Column(DateTime, nullable=True)

    # Revocation
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(String(320), nullable=True)
    revoke_reason = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Role bundle this base-access grant was created for (nullable — most
    # agent-access grants are direct). Lets deleting/revoking a role sweep up
    # exactly the agent access it granted, without touching access the user
    # holds for any other reason. role_name is denormalized so audit history
    # survives a role rename/delete.
    role_id = Column(String, nullable=True, index=True)
    role_name = Column(String, nullable=True)


def agent_access_grant_to_dict(g: AgentAccessGrant) -> dict:
    return {
        "id": g.id,
        "agent_id": g.agent_id,
        "user_email": g.user_email,
        "user_name": g.user_name,
        "status": g.status,
        "justification": g.justification,
        "granted_by_email": g.granted_by_email,
        "granted_by_name": g.granted_by_name,
        "decided_at": g.decided_at.isoformat() if g.decided_at else None,
        "deny_reason": g.deny_reason,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        "revoked_at": g.revoked_at.isoformat() if g.revoked_at else None,
        "revoked_by": g.revoked_by,
        "revoke_reason": g.revoke_reason,
        "created_at": g.created_at.isoformat() if g.created_at else None,
        "updated_at": g.updated_at.isoformat() if g.updated_at else None,
        "role_id": g.role_id,
        "role_name": g.role_name,
    }


class AgentSlackAppGrant(Base):
    """Authorises a Slack app (bot) to invoke a specific agent with full skill access.

    Created by an agent owner or admin; keyed on the bot's Slack user ID so the
    event handler can look it up directly from the inbound event without resolving
    an email.
    """

    __tablename__ = "agent_slack_app_grants"
    __table_args__ = (UniqueConstraint("agent_id", "slack_user_id"),)

    id             = Column(String, primary_key=True, default=_uuid)
    agent_id       = Column(String, nullable=False, index=True)
    slack_user_id  = Column(String(40), nullable=False, index=True)  # SlackAppRecord.user_id
    app_name       = Column(String(255), nullable=True)              # cached display name
    granted_by_email = Column(String(320), nullable=True)
    created_at     = Column(DateTime, nullable=False, default=datetime.utcnow)


def agent_slack_app_grant_to_dict(g: AgentSlackAppGrant) -> dict:
    return {
        "id": g.id,
        "agent_id": g.agent_id,
        "slack_user_id": g.slack_user_id,
        "app_name": g.app_name,
        "granted_by_email": g.granted_by_email,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


class AgentSlackWorkflowGrant(Base):
    """Authorises a Slack Workflow Builder workflow to invoke a specific agent.

    Owners paste a workflow ID (Wf…) from Workflow Builder.  At event time the
    handler matches that ID (or the workflow's app ID A…) against identities
    extracted from the inbound bot ``app_mention`` payload.
    """

    __tablename__ = "agent_slack_workflow_grants"
    __table_args__ = (UniqueConstraint("agent_id", "workflow_id"),)

    id               = Column(String, primary_key=True, default=_uuid)
    agent_id         = Column(String, nullable=False, index=True)
    workflow_id      = Column(String(80), nullable=False, index=True)
    resolved_app_id  = Column(String(40), nullable=True, index=True)  # A… from Wf… lookup
    label            = Column(String(255), nullable=True)  # optional display name
    granted_by_email = Column(String(320), nullable=True)
    created_at       = Column(DateTime, nullable=False, default=datetime.utcnow)


def agent_slack_workflow_grant_to_dict(g: AgentSlackWorkflowGrant) -> dict:
    return {
        "id": g.id,
        "agent_id": g.agent_id,
        "workflow_id": g.workflow_id,
        "resolved_app_id": g.resolved_app_id,
        "label": g.label,
        "granted_by_email": g.granted_by_email,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


class SlackAppRegistrationClaim(Base):
    """Exclusive claim on a Slack app ID during guided registration.

    Prevents one member from pushing a canonical manifest to another member's
    unregistered Slack app while the platform configuration token has access.
    """

    __tablename__ = "slack_app_registration_claims"

    slack_app_id = Column(String(40), primary_key=True)
    owner_sub = Column(String, nullable=False, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class AgentRole(Base):
    """A named, reusable bundle of skills scoped to one agent.

    Assigning a role to a user creates real per-skill CrossDepartmentGrant rows
    tagged with this role's id (see bulk_grant_skill_access role_id/role_name
    params) — the role itself has no membership list; "who holds this role" is
    computed on read from those tagged grants.
    """

    __tablename__ = "agent_roles"
    __table_args__ = (
        # Name may be reused after a role is archived/deleted. Only one *active*
        # role per (agent_id, name) is allowed.
        Index(
            "uq_agent_roles_agent_id_name_active",
            "agent_id",
            "name",
            unique=True,
            postgresql_where=sa_text("archived_at IS NULL"),
        ),
    )

    id = Column(String, primary_key=True, default=_uuid)
    agent_id = Column(String, nullable=False, index=True)
    name = Column(String(120), nullable=False)
    description = Column(Text, nullable=True)
    skill_slugs = Column(Text, nullable=False, default="[]")  # JSON array of skill slugs

    created_by_email = Column(String(320), nullable=True)
    created_by_name = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    archived_at = Column(DateTime, nullable=True)
    archived_by = Column(String(320), nullable=True)


def agent_role_to_dict(r: AgentRole) -> dict:
    try:
        skill_slugs = json.loads(r.skill_slugs) if r.skill_slugs else []
    except (TypeError, ValueError):
        skill_slugs = []
    return {
        "id": r.id,
        "agent_id": r.agent_id,
        "name": r.name,
        "description": r.description,
        "skill_slugs": skill_slugs,
        "created_by_email": r.created_by_email,
        "created_by_name": r.created_by_name,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "archived_at": r.archived_at.isoformat() if r.archived_at else None,
        "archived_by": r.archived_by,
    }


def init_access_tables(db_engine=None) -> None:
    """Create access control tables and run any necessary migrations."""
    target = db_engine or engine

    # Let SQLAlchemy create tables if missing
    CrossDepartmentGrant.__table__.create(target, checkfirst=True)
    AgentAccessGrant.__table__.create(target, checkfirst=True)
    AgentSlackAppGrant.__table__.create(target, checkfirst=True)
    AgentSlackWorkflowGrant.__table__.create(target, checkfirst=True)
    SlackAppRegistrationClaim.__table__.create(target, checkfirst=True)
    AgentRole.__table__.create(target, checkfirst=True)

    # Incremental migrations (ADD COLUMN IF NOT EXISTS)
    with target.begin() as conn:
        from database import table_cols
        existing = table_cols(conn, "cross_department_grants")

        additions: list[tuple[str, str]] = [
            ("user_name", "TEXT"),
            ("decided_by_name", "TEXT"),
            ("skill_name", "TEXT DEFAULT ''"),
            ("skill_department", "TEXT DEFAULT ''"),
            ("role_id", "TEXT"),
            ("role_name", "TEXT"),
        ]
        for col, defn in additions:
            if col not in existing:
                conn.execute(
                    sa_text(f"ALTER TABLE cross_department_grants ADD COLUMN {col} {defn}")
                )
                logger.info("access_models: added column %s to cross_department_grants", col)

        aag_cols = table_cols(conn, "agent_access_grants")
        for col, defn in [("role_id", "TEXT"), ("role_name", "TEXT")]:
            if aag_cols and col not in aag_cols:
                conn.execute(
                    sa_text(f"ALTER TABLE agent_access_grants ADD COLUMN {col} {defn}")
                )
                logger.info("access_models: added column %s to agent_access_grants", col)

        wf_cols = table_cols(conn, "agent_slack_workflow_grants")
        if wf_cols and "label" not in wf_cols:
            conn.execute(
                sa_text("ALTER TABLE agent_slack_workflow_grants ADD COLUMN label VARCHAR(255)")
            )
            logger.info("access_models: added column label to agent_slack_workflow_grants")
        if wf_cols and "resolved_app_id" not in wf_cols:
            conn.execute(
                sa_text("ALTER TABLE agent_slack_workflow_grants ADD COLUMN resolved_app_id VARCHAR(40)")
            )
            logger.info("access_models: added column resolved_app_id to agent_slack_workflow_grants")

        # agent_roles: replace unique(agent_id, name) with a partial unique index
        # so archived names can be reused on a new active role.
        role_cols = table_cols(conn, "agent_roles")
        if role_cols and conn.dialect.name == "postgresql":
            idx = conn.execute(sa_text(
                "SELECT 1 FROM pg_indexes "
                "WHERE tablename = 'agent_roles' "
                "AND indexname = 'uq_agent_roles_agent_id_name_active'"
            )).first()
            if not idx:
                conn.execute(sa_text(
                    "ALTER TABLE agent_roles "
                    "DROP CONSTRAINT IF EXISTS agent_roles_agent_id_name_key"
                ))
                conn.execute(sa_text(
                    "CREATE UNIQUE INDEX uq_agent_roles_agent_id_name_active "
                    "ON agent_roles (agent_id, name) WHERE archived_at IS NULL"
                ))
                logger.info(
                    "access_models: agent_roles unique name is now active-only "
                    "(uq_agent_roles_agent_id_name_active)"
                )
