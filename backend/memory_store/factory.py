"""Resolve per-agent memory backend mode and MemoryStore instances."""

from __future__ import annotations

from functools import lru_cache

from memory_store.legacy import MemoryStore, build_memory_store
from memory_store.policy import load_agent_memory_policy
from memory_store.types import MemoryBackendMode


@lru_cache(maxsize=1)
def _cached_policy():
    return load_agent_memory_policy()


def resolve_memory_backend_mode(agent_id: str) -> MemoryBackendMode:
    policy = _cached_policy()
    if agent_id in policy.pilot_agent_ids:
        return policy.pilot_mode
    return policy.default_mode


def get_agent_memory_store(agent_id: str) -> MemoryStore:
    mode = resolve_memory_backend_mode(agent_id)
    return build_memory_store(mode, _cached_policy())


def clear_memory_store_caches() -> None:
    _cached_policy.cache_clear()
