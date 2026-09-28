"""SQLAlchemy model + helpers for AgentChannelConfig.

One row per (agent_id, channel_id) pair. Stores the per-channel overrides for
soul, skills, guardrails, access policy, memory scope, and join behaviour for
the platform agent (singleton) and any future channel-aware agents.

Table creation is handled in init_agent_tables() (agents.py) which is called
from init_db() at startup.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    JSON,
    String,
    Text,
    UniqueConstraint,
    text as sa_text,
)

from database import Base, SessionLocal, _uuid

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CHANNEL_TYPES = ("TEAM", "PERSONAL", "BROADCAST")
MEMORY_SCOPES = ("channel", "global", "user")
CHANNEL_FALLBACK_BEHAVIORS = ("default", "silent")

_MAX_CHANNEL_ID = 32
_MAX_DISPLAY_NAME = 120
_MAX_CHANNEL_NAME = 200
_MAX_CHANNEL_TOPIC = 500
_MAX_SOUL = 8000


# ── Model ─────────────────────────────────────────────────────────────────────

class AgentChannelConfig(Base):
    """Per-channel configuration for a channel-aware agent.

    Fields that are None / null inherit from the parent Agent row at runtime.
    """

    __tablename__ = "agent_channel_configs"
    __table_args__ = (
        UniqueConstraint("agent_id", "channel_id", name="uq_agent_channel_configs"),
    )

    id = Column(String(36), primary_key=True, default=_uuid)
    agent_id = Column(
        String(36),
        ForeignKey("agents.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Slack channel info (snapshot taken at join time)
    channel_id = Column(String(_MAX_CHANNEL_ID), nullable=False, index=True)
    channel_name = Column(String(_MAX_CHANNEL_NAME), nullable=True)
    channel_topic = Column(String(_MAX_CHANNEL_TOPIC), nullable=True)
    channel_type = Column(String(16), nullable=True)   # TEAM | PERSONAL | BROADCAST

    # Per-channel identity override
    display_name = Column(String(_MAX_DISPLAY_NAME), nullable=True)

    # Per-channel personality (LLM-generated, editable by admin)
    soul_text = Column(Text, nullable=True)
    soul_generated_at = Column(DateTime, nullable=True)

    # Per-channel skill / guardrail overrides (JSON arrays; None → inherit global)
    skill_ids = Column(Text, nullable=True)   # JSON array of slugs
    guardrails = Column(JSON, nullable=True)  # list of guardrail dicts

    # Access policy: {"mode": "open"|"allowlist", "emails": [...]}
    access_policy = Column(JSON, nullable=True, default=lambda: {"mode": "open", "emails": []})

    # Memory scope for this channel
    memory_scope = Column(String(16), nullable=False, default="channel")

    # Join behaviour: {"on_join": "full_intro"|"silent"|"custom", "custom_message": "...",
    #                  "proactivity": "reactive"|"ambient"}
    join_behavior = Column(JSON, nullable=True, default=lambda: {"on_join": "full_intro", "proactivity": "reactive"})

    # Raw channel metadata snapshot from Slack channels.info
    channel_metadata = Column(JSON, nullable=True)

    # Join flow status (for UI "Pending" spinner)
    # "pending" → join flow running, "done" → complete, "error" → failed
    join_status = Column(String(16), nullable=False, default="pending")

    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc).replace(tzinfo=None), nullable=False)


# ── Serialiser ────────────────────────────────────────────────────────────────

def channel_config_to_dict(c: AgentChannelConfig) -> dict:
    skill_ids: list = []
    if c.skill_ids:
        try:
            skill_ids = json.loads(c.skill_ids)
        except Exception:
            pass
    return {
        "id": c.id,
        "agent_id": c.agent_id,
        "channel_id": c.channel_id,
        "channel_name": c.channel_name,
        "channel_topic": c.channel_topic,
        "channel_type": c.channel_type,
        "display_name": c.display_name,
        "soul_text": c.soul_text,
        "soul_generated_at": c.soul_generated_at.isoformat() if c.soul_generated_at else None,
        "skill_ids": skill_ids,
        "guardrails": c.guardrails,
        "access_policy": c.access_policy or {"mode": "open", "emails": []},
        "memory_scope": c.memory_scope,
        "join_behavior": c.join_behavior or {"on_join": "full_intro", "proactivity": "reactive"},
        "join_status": c.join_status,
        "is_active": c.is_active,
        "channel_metadata": c.channel_metadata,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────

def get_channel_config(agent_id: str, channel_id: str) -> Optional[AgentChannelConfig]:
    """Return the config row for (agent_id, channel_id), or None if not found."""
    if not agent_id or not channel_id:
        return None
    db = SessionLocal()
    try:
        return (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == agent_id,
                AgentChannelConfig.channel_id == channel_id,
                AgentChannelConfig.is_active == True,  # noqa: E712 — works for both BOOLEAN and INTEGER storage
            )
            .first()
        )
    except Exception:
        logger.exception("get_channel_config failed agent_id=%s channel_id=%s", agent_id, channel_id)
        return None
    finally:
        db.close()


def upsert_channel_config(
    agent_id: str,
    channel_id: str,
    *,
    channel_name: Optional[str] = None,
    channel_topic: Optional[str] = None,
    channel_type: Optional[str] = None,
    display_name: Optional[str] = None,
    soul_text: Optional[str] = None,
    soul_generated_at: Optional[datetime] = None,
    skill_ids: Optional[list] = None,
    guardrails: Optional[list] = None,
    access_policy: Optional[dict] = None,
    memory_scope: str = "channel",
    join_behavior: Optional[dict] = None,
    channel_metadata: Optional[dict] = None,
    join_status: Optional[str] = None,
    is_active: bool = True,
) -> Optional[AgentChannelConfig]:
    """Create or update a channel config row. Never raises."""
    if not agent_id or not channel_id:
        return None
    db = SessionLocal()
    try:
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        existing = (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == agent_id,
                AgentChannelConfig.channel_id == channel_id,
            )
            .first()
        )
        if existing:
            if channel_name is not None:
                existing.channel_name = channel_name[:_MAX_CHANNEL_NAME]
            if channel_topic is not None:
                existing.channel_topic = channel_topic[:_MAX_CHANNEL_TOPIC]
            if channel_type is not None and channel_type in CHANNEL_TYPES:
                existing.channel_type = channel_type
            if display_name is not None:
                existing.display_name = display_name[:_MAX_DISPLAY_NAME]
            if soul_text is not None:
                existing.soul_text = soul_text[:_MAX_SOUL]
            if soul_generated_at is not None:
                existing.soul_generated_at = soul_generated_at
            if skill_ids is not None:
                existing.skill_ids = json.dumps(skill_ids)
            if guardrails is not None:
                existing.guardrails = guardrails
            if access_policy is not None:
                existing.access_policy = access_policy
            if memory_scope in MEMORY_SCOPES:
                existing.memory_scope = memory_scope
            if join_behavior is not None:
                existing.join_behavior = join_behavior
            if channel_metadata is not None:
                existing.channel_metadata = channel_metadata
            if join_status is not None:
                existing.join_status = join_status
            existing.is_active = is_active
            existing.updated_at = now
            db.commit()
            db.refresh(existing)
            return existing

        cfg = AgentChannelConfig(
            agent_id=agent_id,
            channel_id=channel_id,
            channel_name=(channel_name or "")[:_MAX_CHANNEL_NAME] or None,
            channel_topic=(channel_topic or "")[:_MAX_CHANNEL_TOPIC] or None,
            channel_type=channel_type if channel_type in CHANNEL_TYPES else None,
            display_name=(display_name or "")[:_MAX_DISPLAY_NAME] or None,
            soul_text=(soul_text or "")[:_MAX_SOUL] or None,
            soul_generated_at=soul_generated_at,
            skill_ids=json.dumps(skill_ids) if skill_ids is not None else None,
            guardrails=guardrails,
            access_policy=access_policy or {"mode": "open", "emails": []},
            memory_scope=memory_scope if memory_scope in MEMORY_SCOPES else "channel",
            join_behavior=join_behavior or {"on_join": "full_intro", "proactivity": "reactive"},
            channel_metadata=channel_metadata,
            join_status=join_status or "pending",
            is_active=is_active,
            created_at=now,
            updated_at=now,
        )
        db.add(cfg)
        db.commit()
        db.refresh(cfg)
        return cfg
    except Exception:
        logger.exception(
            "upsert_channel_config failed agent_id=%s channel_id=%s", agent_id, channel_id
        )
        db.rollback()
        return None
    finally:
        db.close()


# ── Platform agent singleton ──────────────────────────────────────────────────

def ensure_platform_agent() -> None:
    """Create or sync the platform agent singleton.

    Safe to call multiple times. When the row already exists, updates ``name``
    and ``description`` from ``APP_NAME`` if they drifted (e.g. after rebrand).
    Called once at backend startup from main.py lifespan.
    """
    from agents import Agent

    db = SessionLocal()
    try:
        existing = (
            db.query(Agent)
            .filter(Agent.is_platform_agent.is_(True))
            .first()
        )
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        from app_config import APP_NAME

        platform_description = (
            "The platform-level channel-adaptive Slack agent. Managed by admins only."
        )

        if existing:
            changed = False
            if existing.name != APP_NAME:
                existing.name = APP_NAME
                changed = True
            if existing.description != platform_description:
                existing.description = platform_description
                changed = True
            if changed:
                existing.updated_at = now
                db.commit()
                logger.info(
                    "ensure_platform_agent: synced platform agent id=%s name=%s",
                    existing.id,
                    existing.name,
                )
            else:
                logger.info(
                    "ensure_platform_agent: platform agent already exists id=%s name=%s",
                    existing.id,
                    existing.name,
                )
            return

        platform_agent = Agent(
            name=APP_NAME,
            description=platform_description,
            agent_type="platform",
            is_platform_agent=True,
            channel_fallback_behavior="silent",
            soul_text=None,
            skill_ids="[]",
            channel_ids="[]",
            trigger_mode="mention",
            status="active",
            creator_email="platform@example.com",
            creator_department="Platform",
            created_at=now,
            updated_at=now,
        )
        db.add(platform_agent)
        db.commit()
        db.refresh(platform_agent)
        logger.info(
            "ensure_platform_agent: created platform agent id=%s", platform_agent.id
        )
    except Exception:
        logger.exception("ensure_platform_agent failed")
        db.rollback()
    finally:
        db.close()


def get_platform_agent():
    """Return the platform agent row, or None if not yet created."""
    from agents import Agent

    db = SessionLocal()
    try:
        return db.query(Agent).filter(Agent.is_platform_agent.is_(True)).first()
    except Exception:
        logger.exception("get_platform_agent failed")
        return None
    finally:
        db.close()
