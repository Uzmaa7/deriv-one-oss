"""The catalogue of connected systems.

A "connected system" is anything outside this platform that a skill or an agent
might reach: a billing API, a data warehouse, an internal admin tool. The
platform uses the catalogue for two things:

* **Declared reach.** When a skill is published, its instructions are matched
  against the catalogue so the skill records which systems it touches. That list
  is shown to reviewers and to anyone requesting access to the skill.
* **Risk-aware alerting.** Each entry carries a risk level. Attaching a skill
  that touches a high-risk system to an agent raises an alert.

The catalogue is a file, not a service. Organisations that maintain a system
inventory elsewhere export it to this file on whatever schedule suits them; the
platform re-reads it when it changes. Deployments with no inventory leave it
absent, and both features above quietly do nothing rather than failing.

File shape (``SYSTEM_CATALOG_FILE``, default ``data/systems.yaml``)::

    systems:
      - name: Billing API
        risk_level: High          # Critical | High | Medium | Low
        status: Active            # Active | Inactive
        owner: platform-team      # optional, free text
        description: Customer invoices and refunds   # optional

Names are the join key and are matched case-insensitively.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

ACTIVE_STATUS = "active"

#: Ordered least- to most-severe, so callers can take a maximum.
RISK_ORDER: tuple[str, ...] = ("low", "medium", "high", "critical")

#: Risk levels that warrant an alert when attached to an agent.
HIGH_RISK_LEVELS: frozenset[str] = frozenset({"high", "critical"})

_lock = threading.Lock()
_cache: dict[str, dict] | None = None
_cache_mtime: float | None = None


def _catalog_path() -> Path:
    return Path(os.environ.get("SYSTEM_CATALOG_FILE") or "data/systems.yaml")


def _read_rows(path: Path) -> list[dict]:
    raw = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml

        data = yaml.safe_load(raw) or {}
    else:
        data = json.loads(raw or "{}")
    if isinstance(data, list):
        return [r for r in data if isinstance(r, dict)]
    if isinstance(data, dict):
        return [r for r in (data.get("systems") or []) if isinstance(r, dict)]
    return []


def _load() -> dict[str, dict]:
    """Read the catalogue, re-reading only when the file has changed."""
    global _cache, _cache_mtime
    path = _catalog_path()
    try:
        mtime = path.stat().st_mtime if path.exists() else None
    except OSError:
        mtime = None

    if _cache is not None and mtime == _cache_mtime:
        return _cache

    with _lock:
        if _cache is not None and mtime == _cache_mtime:
            return _cache
        mapping: dict[str, dict] = {}
        if mtime is not None:
            try:
                for row in _read_rows(path):
                    name = str(row.get("name") or "").strip()
                    if not name:
                        continue
                    risk = str(row.get("risk_level") or "").strip().lower()
                    mapping[name.lower()] = {
                        "system_name": name,
                        "risk_level": risk if risk in RISK_ORDER else "",
                        "system_status": str(row.get("status") or "Active").strip(),
                        "owner": str(row.get("owner") or "").strip(),
                        "description": str(row.get("description") or "").strip(),
                    }
            except Exception:
                logger.exception("failed to read system catalogue %s", path)
                mapping = _cache or {}
        else:
            logger.debug("no system catalogue at %s; system linking disabled", path)
        _cache = mapping
        _cache_mtime = mtime
        return mapping


def is_active_system_status(status: str | None) -> bool:
    return (status or "").strip().lower() == ACTIVE_STATUS


def get_system_map(*, include_inactive: bool = False) -> dict[str, dict]:
    """Lower-cased system name -> record.

    Inactive systems are excluded by default: matching a skill against a retired
    system both overstates the skill's reach and can attach a stale risk level.
    """
    mapping = _load()
    if include_inactive:
        return dict(mapping)
    return {
        key: record
        for key, record in mapping.items()
        if is_active_system_status(record.get("system_status"))
    }


def get_system(name: str) -> dict | None:
    return _load().get((name or "").strip().lower())


def risk_rank(risk_level: str | None) -> int:
    """Sortable severity. Unknown levels rank below everything known."""
    try:
        return RISK_ORDER.index((risk_level or "").strip().lower()) + 1
    except ValueError:
        return 0


def is_high_risk(risk_level: str | None) -> bool:
    return (risk_level or "").strip().lower() in HIGH_RISK_LEVELS


def invalidate_cache() -> None:
    global _cache, _cache_mtime
    with _lock:
        _cache = None
        _cache_mtime = None
