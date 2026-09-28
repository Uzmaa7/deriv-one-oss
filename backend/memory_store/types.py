"""Typed contracts for agent memory (storage-agnostic)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

MemoryScope = Literal["agent", "user", "channel"]
MemorySource = Literal["auto", "manual"]
MemoryStatus = Literal["active", "superseded", "tombstone"]


class MemoryBackendMode(str, Enum):
    """Per-agent rollout mode for the sustainable memory pilot."""

    LEGACY = "legacy"
    SHADOW = "shadow"  # legacy authoritative; archive/index exercised for metrics only
    HYBRID = "hybrid"  # legacy reads + dual-write episodes to the blob store
    NEW = "new"  # reserved — not used in pilot foundation


@dataclass(frozen=True)
class MemoryRecord:
    """Logical memory fact (maps to agent_memories today; extensible later)."""

    entry_id: str
    agent_id: str
    scope: MemoryScope
    key: str
    value: str
    source: MemorySource = "auto"
    user_email: str | None = None
    channel_id: str | None = None
    observed_at: datetime | None = None
    updated_at: datetime | None = None
    expires_at: datetime | None = None
    provenance: dict[str, Any] = field(default_factory=dict)
    confidence: float | None = None
    importance: int | None = None
    status: MemoryStatus = "active"
    supersedes: str | None = None
    sensitivity: str | None = None
