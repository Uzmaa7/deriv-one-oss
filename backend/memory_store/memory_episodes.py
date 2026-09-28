"""Immutable memory episodes.

Every time an agent commits something to memory, a small Markdown shard is
written describing what changed and why. Shards are append-only and keyed by
agent and day, so the archive doubles as an audit trail: current memory says
what the agent believes, the episodes say how it came to believe it.

Writes are best-effort — an archive failure must never fail the memory write
that triggered it.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

_AGENT_ID_RE = re.compile(r"^[0-9a-fA-F-]{36}$")
_SCOPE_RE = re.compile(r"^(agent|user|channel)$")
_OBJECT_PREFIX = "agent-memory"


def _utc_date() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _safe_agent_id(agent_id: str) -> str | None:
    agent_id = (agent_id or "").strip()
    if not _AGENT_ID_RE.match(agent_id):
        return None
    return agent_id


def episode_object_path(
    *,
    agent_id: str,
    episode_id: str,
    day: str | None = None,
) -> str | None:
    aid = _safe_agent_id(agent_id)
    if not aid:
        return None
    eid = (episode_id or "").strip()
    if not eid or "/" in eid or ".." in eid:
        return None
    day_part = day or _utc_date()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", day_part):
        return None
    return f"{_OBJECT_PREFIX}/agents/{aid}/memory/{day_part}/{eid}.md"


def append_upsert_episode(
    *,
    agent_id: str,
    scope: str,
    key: str,
    source: str,
    user_email: str | None,
    channel_id: str | None,
    memory_id: str | None,
    provenance: dict[str, Any] | None = None,
) -> bool:
    """Write a small Markdown shard describing a memory upsert. Best-effort; never raises."""
    if not _SCOPE_RE.match(scope or ""):
        return False
    episode_id = uuid.uuid4().hex
    path = episode_object_path(agent_id=agent_id, episode_id=episode_id)
    if not path:
        return False

    meta = {
        "agent_id": agent_id,
        "scope": scope,
        "key": key[:200],
        "source": source,
        "user_email": user_email,
        "channel_id": channel_id,
        "memory_id": memory_id,
        "provenance": provenance or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    body = (
        f"# Memory episode {episode_id}\n\n"
        f"- scope: {scope}\n"
        f"- key: {key}\n"
        f"- source: {source}\n"
    )
    if user_email:
        body += f"- user: {user_email}\n"
    if channel_id:
        body += f"- channel: {channel_id}\n"
    body += f"\n```json\n{json.dumps(meta, indent=2)}\n```\n"

    try:
        from providers import get_blob_store

        get_blob_store().put(
            path, body.encode("utf-8"), content_type="text/markdown; charset=utf-8"
        )
        logger.info(
            "memory_episodes: wrote %s agent_id=%s scope=%s", path, agent_id, scope
        )
        return True
    except Exception:
        logger.exception(
            "memory_episodes: failed episode write agent_id=%s scope=%s",
            agent_id,
            scope,
        )
        return False
