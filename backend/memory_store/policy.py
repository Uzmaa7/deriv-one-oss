"""Configurable quotas, TTL, and injection limits for agent_memories."""

from __future__ import annotations

import os
from dataclasses import dataclass

from memory_store.types import MemoryBackendMode, MemoryScope


def _int_env(name: str, default: int, *, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class AgentMemoryPolicy:
    value_max_chars: int
    key_max_chars: int
    inject_agent_limit: int
    inject_user_limit: int
    inject_channel_limit: int
    store_quota_agent: int
    store_quota_user: int
    store_quota_channel: int
    auto_ttl_days: int | None
    default_mode: MemoryBackendMode
    pilot_agent_ids: frozenset[str]
    pilot_mode: MemoryBackendMode


def load_agent_memory_policy() -> AgentMemoryPolicy:
    mode_raw = os.environ.get("AGENT_MEMORY_DEFAULT_MODE", "legacy").strip().lower()
    try:
        default_mode = MemoryBackendMode(mode_raw)
    except ValueError:
        default_mode = MemoryBackendMode.LEGACY

    pilot_mode_raw = os.environ.get("AGENT_MEMORY_PILOT_MODE", "shadow").strip().lower()
    try:
        pilot_mode = MemoryBackendMode(pilot_mode_raw)
    except ValueError:
        pilot_mode = MemoryBackendMode.SHADOW

    pilot_ids: set[str] = set()
    for part in os.environ.get("AGENT_MEMORY_PILOT_AGENT_IDS", "").split(","):
        part = part.strip()
        if part:
            pilot_ids.add(part)

    ttl_days_raw = os.environ.get("AGENT_MEMORY_AUTO_TTL_DAYS", "90").strip()
    auto_ttl_days: int | None
    if not ttl_days_raw or ttl_days_raw == "0":
        auto_ttl_days = None
    else:
        try:
            auto_ttl_days = max(1, int(ttl_days_raw))
        except ValueError:
            auto_ttl_days = 90

    return AgentMemoryPolicy(
        value_max_chars=_int_env("AGENT_MEMORY_VALUE_MAX", 2_000, minimum=256),
        key_max_chars=_int_env("AGENT_MEMORY_KEY_MAX", 200, minimum=32),
        inject_agent_limit=_int_env("AGENT_MEMORY_INJECT_AGENT_LIMIT", 30),
        inject_user_limit=_int_env("AGENT_MEMORY_INJECT_USER_LIMIT", 20),
        inject_channel_limit=_int_env("AGENT_MEMORY_INJECT_CHANNEL_LIMIT", 30),
        store_quota_agent=_int_env("AGENT_MEMORY_STORE_QUOTA_AGENT", 500),
        store_quota_user=_int_env("AGENT_MEMORY_STORE_QUOTA_USER", 150),
        store_quota_channel=_int_env("AGENT_MEMORY_STORE_QUOTA_CHANNEL", 150),
        auto_ttl_days=auto_ttl_days,
        default_mode=default_mode,
        pilot_agent_ids=frozenset(pilot_ids),
        pilot_mode=pilot_mode,
    )


def store_quota_for_scope(policy: AgentMemoryPolicy, scope: MemoryScope) -> int:
    if scope == "user":
        return policy.store_quota_user
    if scope == "channel":
        return policy.store_quota_channel
    return policy.store_quota_agent


def inject_limit_for_scope(policy: AgentMemoryPolicy, scope: MemoryScope) -> int:
    if scope == "user":
        return policy.inject_user_limit
    if scope == "channel":
        return policy.inject_channel_limit
    return policy.inject_agent_limit
