"""Load approved artifacts and execute call_artifact."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from artifact_models import (
    ARTIFACT_STATUS_APPROVED,
    SkillArtifact,
    parse_manifest,
)
from artifact_params import ParamsValidationError, validate_params
from code_fingerprint import code_sha256

logger = logging.getLogger(__name__)

_SKILL_ARTIFACTS_GLOBAL_ENABLED = os.environ.get("SKILL_ARTIFACTS_ENABLED", "1").lower() in (
    "1",
    "true",
    "yes",
)


def artifacts_enabled_for_agent(agent) -> bool:
    """Return True when this agent should offer call_artifact at runtime."""
    if not _SKILL_ARTIFACTS_GLOBAL_ENABLED:
        return False
    return bool(getattr(agent, "artifacts_enabled", False))


_CACHE_TTL_SECS = 60.0
_cache: dict[tuple[str, ...], tuple[float, list[SkillArtifact]]] = {}


def invalidate_artifact_cache() -> None:
    """Clear cached approved-artifact lists (e.g. after approve/disable)."""
    _cache.clear()


def _cache_key(slugs: list[str]) -> tuple[str, ...]:
    return tuple(sorted(set(slugs)))


def load_approved_artifacts(db: Session, slugs: list[str]) -> list[SkillArtifact]:
    if not slugs:
        return []
    key = _cache_key(slugs)
    now = time.monotonic()
    hit = _cache.get(key)
    if hit and now - hit[0] < _CACHE_TTL_SECS:
        return hit[1]

    rows = (
        db.query(SkillArtifact)
        .filter(
            SkillArtifact.slug.in_(slugs),
            SkillArtifact.status == ARTIFACT_STATUS_APPROVED,
            SkillArtifact.disabled.is_(False),
        )
        .order_by(SkillArtifact.slug, SkillArtifact.name, SkillArtifact.version.desc())
        .all()
    )
    latest: dict[tuple[str, str], SkillArtifact] = {}
    for row in rows:
        k = (row.slug, row.name)
        if k not in latest:
            latest[k] = row
    result = list(latest.values())
    _cache[key] = (now, result)
    return result


def disable_superseded_artifact_versions(
    db: Session,
    slug: str,
    name: str,
    keep_version: int,
) -> int:
    """Mark older versions disabled so only the latest approved row is active."""
    rows = (
        db.query(SkillArtifact)
        .filter(
            SkillArtifact.slug == slug,
            SkillArtifact.name == name,
            SkillArtifact.version < keep_version,
            SkillArtifact.disabled.is_(False),
        )
        .all()
    )
    for row in rows:
        row.disabled = True
    if rows:
        invalidate_artifact_cache()
    return len(rows)


def reconcile_superseded_artifact_versions(db: Session, slug: str) -> int:
    """Disable every non-latest version per artifact name for a skill."""
    rows = (
        db.query(SkillArtifact)
        .filter(
            SkillArtifact.slug == slug,
            SkillArtifact.status == ARTIFACT_STATUS_APPROVED,
            SkillArtifact.disabled.is_(False),
        )
        .order_by(SkillArtifact.name, SkillArtifact.version.desc())
        .all()
    )
    latest_version: dict[str, int] = {}
    for row in rows:
        if row.name not in latest_version:
            latest_version[row.name] = row.version
    disabled = 0
    for row in rows:
        if row.version < latest_version[row.name]:
            row.disabled = True
            disabled += 1
    if disabled:
        invalidate_artifact_cache()
    return disabled


def build_artifact_prompt_section(artifacts: list[SkillArtifact]) -> str:
    if not artifacts:
        return ""
    lines = [
        "## Approved skill artifacts",
        "Prefer call_artifact over run_python when an artifact covers the task.",
        "",
    ]
    for art in artifacts:
        summary = (art.api_summary or f"{art.entrypoint}(...)").strip()
        lines.append(f"- `{art.name}` (skill `{art.slug}`, v{art.version}): {summary}")
    return "\n".join(lines) + "\n"


def resolve_artifact(
    db: Session,
    slugs: list[str],
    name: str,
) -> SkillArtifact | None:
    name = (name or "").strip()
    if not name or not slugs:
        return None
    for art in load_approved_artifacts(db, slugs):
        if art.name == name:
            return art
    return None


def scoped_secrets(
    all_secrets: dict[str, str],
    declared: list[str],
) -> dict[str, str]:
    out: dict[str, str] = {}
    for key in declared:
        if key in all_secrets and all_secrets[key]:
            out[key] = all_secrets[key]
    return out


def _build_call_code(mod: str, entry: str, params: dict) -> str:
    params_json = json.dumps(params, separators=(",", ":"))
    return (
        "import json\n"
        f"from {mod} import {entry} as _fn\n"
        f"_p = json.loads({params_json!r})\n"
        "print(json.dumps(_fn(**_p), default=str))\n"
    )


def build_call_artifact_code(artifact: SkillArtifact, params: dict) -> str:
    return _build_call_code(artifact.name, artifact.entrypoint, params)


def build_draft_call_code(manifest: dict, params: dict) -> str:
    name = manifest.get("name") or manifest.get("entrypoint", "artifact")
    entry = manifest.get("entrypoint", "")
    return _build_call_code(name, entry, params)


def verify_code_integrity(artifact: SkillArtifact) -> bool:
    return code_sha256(artifact.code) == artifact.code_sha256


def record_run(db: Session, artifact: SkillArtifact, ok: bool) -> None:
    artifact.run_count = (artifact.run_count or 0) + 1
    now = datetime.now(timezone.utc)
    if ok:
        artifact.last_ok_at = now
    else:
        artifact.last_error_at = now
    db.add(artifact)
    db.commit()


def prepare_call(
    artifact: SkillArtifact,
    raw_params: Any,
) -> tuple[dict, dict[str, str]]:
    if not verify_code_integrity(artifact):
        raise ValueError("artifact code integrity check failed")
    schema = None
    if artifact.params_schema_json:
        schema = json.loads(artifact.params_schema_json)
    params = validate_params(raw_params, schema)
    declared = json.loads(artifact.declared_secrets_json or "[]")
    return params, declared
