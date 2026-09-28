"""Retention cleanup and per-scope storage quotas for agent_memories."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from agents import AgentMemory
from memory_store.policy import AgentMemoryPolicy, load_agent_memory_policy, store_quota_for_scope
from memory_store.types import MemoryScope

logger = logging.getLogger(__name__)


@dataclass
class AgentMemoryMaintenanceResult:
    expired_deleted: int = 0
    quota_trimmed: int = 0
    agents_touched: int = 0


def _utcnow_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def purge_expired_agent_memories(db: Session, *, now: datetime | None = None) -> int:
    """Delete rows whose expires_at is in the past. Returns rows deleted."""
    now = now or _utcnow_naive()
    try:
        q = db.query(AgentMemory).filter(
            AgentMemory.expires_at.isnot(None),
            AgentMemory.expires_at <= now,
        )
        count = q.count()
        if count:
            q.delete(synchronize_session=False)
            db.commit()
        return count
    except Exception:
        logger.exception("purge_expired_agent_memories failed")
        db.rollback()
        return 0


def _scope_filter(q, scope: MemoryScope, *, agent_id: str, user_email: str | None, channel_id: str | None):
    q = q.filter(AgentMemory.agent_id == agent_id, AgentMemory.scope == scope)
    if scope == "user":
        return q.filter(AgentMemory.user_email == user_email)
    if scope == "channel":
        return q.filter(AgentMemory.channel_id == channel_id)
    return q


def trim_scope_to_quota(
    db: Session,
    *,
    agent_id: str,
    scope: MemoryScope,
    quota: int,
    user_email: str | None = None,
    channel_id: str | None = None,
) -> int:
    """Evict oldest auto rows first, then oldest manual, until count <= quota."""
    if quota < 1:
        quota = 1
    base = _scope_filter(
        db.query(AgentMemory),
        scope,
        agent_id=agent_id,
        user_email=user_email,
        channel_id=channel_id,
    )
    try:
        total = base.count()
        if total <= quota:
            return 0
        excess = total - quota
        # Prefer dropping auto-sourced memories before manual edits.
        victims = (
            base.order_by(
                AgentMemory.source.asc(),  # 'auto' < 'manual'
                AgentMemory.updated_at.asc(),
            )
            .limit(excess)
            .all()
        )
        deleted = 0
        for row in victims:
            db.delete(row)
            deleted += 1
        if deleted:
            db.commit()
        return deleted
    except Exception:
        logger.exception(
            "trim_scope_to_quota failed agent_id=%s scope=%s",
            agent_id,
            scope,
        )
        db.rollback()
        return 0


def enforce_storage_quotas_for_agent(
    db: Session,
    agent_id: str,
    policy: AgentMemoryPolicy,
) -> int:
    """Trim all scopes for one agent. Returns total rows deleted."""
    trimmed = 0
    trimmed += trim_scope_to_quota(
        db,
        agent_id=agent_id,
        scope="agent",
        quota=policy.store_quota_agent,
    )
    user_keys = (
        db.query(AgentMemory.user_email)
        .filter(
            AgentMemory.agent_id == agent_id,
            AgentMemory.scope == "user",
            AgentMemory.user_email.isnot(None),
        )
        .distinct()
        .all()
    )
    for (email,) in user_keys:
        if not email:
            continue
        trimmed += trim_scope_to_quota(
            db,
            agent_id=agent_id,
            scope="user",
            quota=policy.store_quota_user,
            user_email=email,
        )
    channel_keys = (
        db.query(AgentMemory.channel_id)
        .filter(
            AgentMemory.agent_id == agent_id,
            AgentMemory.scope == "channel",
            AgentMemory.channel_id.isnot(None),
        )
        .distinct()
        .all()
    )
    for (ch_id,) in channel_keys:
        if not ch_id:
            continue
        trimmed += trim_scope_to_quota(
            db,
            agent_id=agent_id,
            scope="channel",
            quota=policy.store_quota_channel,
            channel_id=ch_id,
        )
    return trimmed


def run_agent_memory_maintenance(db: Session) -> AgentMemoryMaintenanceResult:
    """Expire stale rows and enforce quotas for agents over limit."""
    policy = load_agent_memory_policy()
    result = AgentMemoryMaintenanceResult()
    result.expired_deleted = purge_expired_agent_memories(db)

    try:
        overcrowded = (
            db.query(AgentMemory.agent_id, func.count(AgentMemory.id))
            .group_by(AgentMemory.agent_id)
            .having(func.count(AgentMemory.id) > policy.store_quota_agent)
            .all()
        )
        agent_ids = {row[0] for row in overcrowded}
        # Also check agents with heavy user/channel scopes
        for scope, quota in (
            ("user", policy.store_quota_user),
            ("channel", policy.store_quota_channel),
        ):
            sub = (
                db.query(AgentMemory.agent_id)
                .filter(AgentMemory.scope == scope)
                .group_by(
                    AgentMemory.agent_id,
                    AgentMemory.user_email if scope == "user" else AgentMemory.channel_id,
                )
                .having(func.count(AgentMemory.id) > quota)
            )
            for (aid,) in sub:
                agent_ids.add(aid)

        for agent_id in agent_ids:
            trimmed = enforce_storage_quotas_for_agent(db, agent_id, policy)
            if trimmed:
                result.agents_touched += 1
                result.quota_trimmed += trimmed
    except Exception:
        logger.exception("run_agent_memory_maintenance quota pass failed")

    if result.expired_deleted or result.quota_trimmed:
        logger.info(
            "agent_memory_maintenance: expired_deleted=%d quota_trimmed=%d agents_touched=%d",
            result.expired_deleted,
            result.quota_trimmed,
            result.agents_touched,
        )
    return result


def default_auto_expires_at(policy: AgentMemoryPolicy) -> datetime | None:
    if policy.auto_ttl_days is None:
        return None
    return _utcnow_naive() + timedelta(days=policy.auto_ttl_days)


def memory_maintenance_metrics_snapshot(db: Session) -> dict[str, Any]:
    """Aggregate counts for observability (no memory bodies)."""
    try:
        total = db.query(func.count(AgentMemory.id)).scalar() or 0
        by_scope = dict(
            db.query(AgentMemory.scope, func.count(AgentMemory.id))
            .group_by(AgentMemory.scope)
            .all()
        )
        expired_pending = (
            db.query(func.count(AgentMemory.id))
            .filter(
                AgentMemory.expires_at.isnot(None),
                AgentMemory.expires_at <= _utcnow_naive(),
            )
            .scalar()
            or 0
        )
        return {
            "total_rows": int(total),
            "by_scope": {k: int(v) for k, v in by_scope.items()},
            "expired_pending": int(expired_pending),
        }
    except Exception:
        logger.exception("memory_maintenance_metrics_snapshot failed")
        return {}
