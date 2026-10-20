"""Read the stored archive of an agent's memory.

The archive holds three kinds of object under one prefix per agent:

    <prefix>MEMORY.md                         the curated memory the agent reads
    <prefix>memory/<day>/<episode>.md         one shard per memory write
    <prefix>migration/<run>/manifest.json     what a memory migration did
    <prefix>migration/<run>/episodes/<id>.md  and the episodes it produced

This module is read-mostly and exists so the memory UI can show where a belief
came from. Every function is defensive: an unreadable archive returns an empty
summary rather than raising, because a broken archive must not take down the
page that displays it.

Path validation matters here — object paths arrive from the client. Every read
is checked against the requesting agent's own prefix, so one agent can never
read another's archive by asking for its path.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from memory_store.memory_episodes import _OBJECT_PREFIX, _safe_agent_id
from providers import get_blob_store

logger = logging.getLogger(__name__)

_MAX_LIST = 500
_MAX_CONTENT_CHARS = 48_000


def _agent_prefix(agent_id: str) -> str | None:
    aid = _safe_agent_id(agent_id)
    if not aid:
        return None
    return f"{_OBJECT_PREFIX}/agents/{aid}/"


def is_allowed_object_path(agent_id: str, object_path: str) -> bool:
    """Whether ``object_path`` belongs to ``agent_id``.

    The check is deliberately strict: no traversal, no leading slash, and the
    path must sit under this agent's own prefix.
    """
    prefix = _agent_prefix(agent_id)
    if not prefix:
        return False
    path = (object_path or "").strip().lstrip("/")
    if ".." in path or path != object_path.strip().lstrip("/"):
        return False
    return path.startswith(prefix)


def read_object_text(
    agent_id: str, object_path: str, *, max_chars: int = _MAX_CONTENT_CHARS
) -> str | None:
    if not is_allowed_object_path(agent_id, object_path):
        return None
    try:
        text = get_blob_store().get_text(object_path)
    except KeyError:
        return None
    except Exception:
        logger.exception("memory_archive: read failed path=%s", object_path)
        return None
    if len(text) > max_chars:
        return text[:max_chars] + "\n\n… [truncated]\n"
    return text


def _blob_summary(info: Any) -> dict[str, Any]:
    from datetime import datetime, timezone

    updated = info.modified_at
    return {
        "path": info.key,
        "size_bytes": int(info.size or 0),
        "updated_at": (
            datetime.fromtimestamp(updated, tz=timezone.utc).isoformat()
            if updated
            else None
        ),
        "storage_url": info.key,
    }


def list_agent_memory_archive(agent_id: str) -> dict[str, Any]:
    """Summarise everything stored for one agent. Never raises."""
    prefix = _agent_prefix(agent_id)
    empty: dict[str, Any] = {
        "agent_id": agent_id,
        "prefix": prefix,
        "configured": False,
        "curated": None,
        "migrations": [],
        "hybrid_episodes": [],
        "migration_episodes": [],
        "object_count": 0,
    }
    if not prefix:
        return empty

    try:
        blobs = get_blob_store().list(prefix)[:_MAX_LIST]
    except Exception:
        logger.exception("memory_archive: list failed agent_id=%s", agent_id)
        return empty

    curated_path = f"{prefix}MEMORY.md"
    migrations: dict[str, dict[str, Any]] = {}
    hybrid_episodes: list[dict[str, Any]] = []
    migration_episodes: list[dict[str, Any]] = []
    curated_meta: dict[str, Any] | None = None

    mig_run_re = re.compile(rf"^{re.escape(prefix)}migration/([^/]+)/manifest\.json$")
    mig_ep_re = re.compile(rf"^{re.escape(prefix)}migration/([^/]+)/episodes/([^/]+)\.md$")
    hybrid_re = re.compile(rf"^{re.escape(prefix)}memory/(\d{{4}}-\d{{2}}-\d{{2}})/([^/]+)\.md$")

    for info in blobs:
        name = info.key
        if name == curated_path:
            curated_meta = _blob_summary(info)
            continue
        m = mig_run_re.match(name)
        if m:
            run_id = m.group(1)
            migrations[run_id] = {
                "run_id": run_id,
                "manifest_path": name,
                **_blob_summary(info),
            }
            continue
        m = mig_ep_re.match(name)
        if m:
            migration_episodes.append(
                {"run_id": m.group(1), "memory_id": m.group(2), **_blob_summary(info)}
            )
            continue
        m = hybrid_re.match(name)
        if m:
            hybrid_episodes.append(
                {"day": m.group(1), "episode_id": m.group(2), **_blob_summary(info)}
            )

    migration_list = sorted(
        migrations.values(), key=lambda x: x.get("updated_at") or "", reverse=True
    )
    for mig in migration_list:
        raw = read_object_text(agent_id, mig["manifest_path"], max_chars=32_000)
        if raw:
            try:
                mig["manifest"] = json.loads(raw)
            except json.JSONDecodeError:
                mig["manifest_preview"] = raw[:2000]

    hybrid_episodes.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
    migration_episodes.sort(key=lambda x: x.get("updated_at") or "", reverse=True)

    curated_block: dict[str, Any] | None = None
    if curated_meta:
        curated_block = {**curated_meta, "content": read_object_text(agent_id, curated_path)}

    return {
        "agent_id": agent_id,
        "prefix": prefix,
        "configured": True,
        "curated": curated_block,
        "migrations": migration_list,
        "hybrid_episodes": hybrid_episodes[:100],
        "migration_episodes": migration_episodes[:100],
        "object_count": len(blobs),
    }


def curated_memory_path(agent_id: str) -> str | None:
    prefix = _agent_prefix(agent_id)
    return f"{prefix}MEMORY.md" if prefix else None


def write_curated_memory_md(agent_id: str, content: str) -> dict[str, Any] | None:
    """Write an agent's curated MEMORY.md. Returns metadata, or ``None`` on failure."""
    path = curated_memory_path(agent_id)
    if not path:
        return None
    try:
        from datetime import datetime, timezone

        get_blob_store().put(
            path, content.encode("utf-8"), content_type="text/markdown; charset=utf-8"
        )
        return {
            "path": path,
            "size_bytes": len(content.encode("utf-8")),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "storage_url": path,
        }
    except Exception:
        logger.exception("memory_archive: write failed agent_id=%s", agent_id)
        return None
