"""MemoryStore implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from agents import AgentMemory, load_agent_memories, memory_to_dict, upsert_agent_memory
from memory_store.memory_episodes import append_upsert_episode
from memory_store.maintenance import default_auto_expires_at, trim_scope_to_quota
from memory_store.policy import (
    AgentMemoryPolicy,
    inject_limit_for_scope,
    load_agent_memory_policy,
    store_quota_for_scope,
)
from memory_store.types import MemoryBackendMode, MemoryRecord, MemoryScope

logger = logging.getLogger(__name__)


class MemoryStore(ABC):
    @abstractmethod
    def get_curated_context(
        self,
        agent_id: str,
        *,
        user_email: str | None = None,
        channel_id: str | None = None,
    ) -> list[AgentMemory]:
        ...

    @abstractmethod
    def upsert(
        self,
        *,
        agent_id: str,
        scope: MemoryScope,
        key: str,
        value: str,
        user_email: str | None = None,
        channel_id: str | None = None,
        source: str = "auto",
        expires_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> AgentMemory | None:
        ...

    def forget(self, *args: Any, **kwargs: Any) -> bool:
        return False

    def search(self, *args: Any, **kwargs: Any) -> list[MemoryRecord]:
        return []


class LegacyMemoryStore(MemoryStore):
    def __init__(self, policy: AgentMemoryPolicy | None = None) -> None:
        self._policy = policy or load_agent_memory_policy()

    def get_curated_context(
        self,
        agent_id: str,
        *,
        user_email: str | None = None,
        channel_id: str | None = None,
    ) -> list[AgentMemory]:
        return load_agent_memories(agent_id, user_email, channel_id=channel_id)

    def upsert(
        self,
        *,
        agent_id: str,
        scope: MemoryScope,
        key: str,
        value: str,
        user_email: str | None = None,
        channel_id: str | None = None,
        source: str = "auto",
        expires_at: datetime | None = None,
        provenance: dict[str, Any] | None = None,
    ) -> AgentMemory | None:
        if source == "auto" and expires_at is None:
            expires_at = default_auto_expires_at(self._policy)
        row = upsert_agent_memory(
            agent_id=agent_id,
            scope=scope,
            key=key,
            value=value,
            user_email=user_email,
            channel_id=channel_id,
            source=source,
            expires_at=expires_at,
        )
        if row:
            self._enforce_quota_after_upsert(
                agent_id=agent_id,
                scope=scope,
                user_email=row.user_email,
                channel_id=row.channel_id,
            )
        return row

    def _enforce_quota_after_upsert(
        self,
        *,
        agent_id: str,
        scope: MemoryScope,
        user_email: str | None,
        channel_id: str | None,
    ) -> None:
        from database import SessionLocal

        quota = store_quota_for_scope(self._policy, scope)
        db = SessionLocal()
        try:
            trim_scope_to_quota(
                db,
                agent_id=agent_id,
                scope=scope,
                quota=quota,
                user_email=user_email,
                channel_id=channel_id,
            )
        finally:
            db.close()


class HybridMemoryStore(LegacyMemoryStore):
    """Legacy authoritative storage with the blob store episode dual-write."""

    def upsert(self, **kwargs: Any) -> AgentMemory | None:
        row = super().upsert(**kwargs)
        if row:
            append_upsert_episode(
                agent_id=kwargs.get("agent_id", row.agent_id),
                scope=row.scope,
                key=row.key,
                source=row.source,
                user_email=row.user_email,
                channel_id=row.channel_id,
                memory_id=row.id,
                provenance=kwargs.get("provenance"),
            )
        return row


class ShadowMemoryStore(LegacyMemoryStore):
    """Legacy path remains authoritative; archive write exercises the blob store without changing reads."""

    def upsert(self, **kwargs: Any) -> AgentMemory | None:
        row = super().upsert(**kwargs)
        if row:
            logger.debug(
                "agent_memory_shadow: upsert agent_id=%s scope=%s key=%s id=%s",
                row.agent_id,
                row.scope,
                row.key,
                row.id,
            )
            append_upsert_episode(
                agent_id=row.agent_id,
                scope=row.scope,
                key=row.key,
                source=row.source,
                user_email=row.user_email,
                channel_id=row.channel_id,
                memory_id=row.id,
                provenance=kwargs.get("provenance"),
            )
        return row


def build_memory_store(mode: MemoryBackendMode, policy: AgentMemoryPolicy | None = None) -> MemoryStore:
    policy = policy or load_agent_memory_policy()
    if mode == MemoryBackendMode.HYBRID:
        return HybridMemoryStore(policy)
    if mode == MemoryBackendMode.SHADOW:
        return ShadowMemoryStore(policy)
    return LegacyMemoryStore(policy)


def records_from_rows(rows: list[AgentMemory]) -> list[dict[str, Any]]:
    return [memory_to_dict(r) for r in rows]


def injection_limits(policy: AgentMemoryPolicy | None = None) -> dict[str, int]:
    policy = policy or load_agent_memory_policy()
    return {
        "agent": inject_limit_for_scope(policy, "agent"),
        "user": inject_limit_for_scope(policy, "user"),
        "channel": inject_limit_for_scope(policy, "channel"),
    }
