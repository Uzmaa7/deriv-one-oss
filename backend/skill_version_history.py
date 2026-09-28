"""Skill SKILL.md version snapshots — record, list, revert."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, Optional

import difflib
from sqlalchemy import func
from sqlalchemy.orm import Session

from skill_submission_models import SkillContentVersion

logger = logging.getLogger(__name__)


def content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def diff_stats(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _max_version(db: Session, slug: str) -> int:
    return int(db.query(func.max(SkillContentVersion.version)).filter(SkillContentVersion.slug == slug).scalar() or 0)


def _version_row(db: Session, slug: str, version: int) -> Optional[SkillContentVersion]:
    return (
        db.query(SkillContentVersion)
        .filter(SkillContentVersion.slug == slug, SkillContentVersion.version == version)
        .first()
    )


def resolve_edit_actor(db: Session, slug: str, auth) -> tuple[Optional[str], Optional[str], str]:
    """Return (display_name, email, role) for audit trail."""
    from skill_collaborators import is_skill_owner
    from skill_submission_models import REVIEW_STATUS_PASS, SkillCollaborator, SkillSubmission

    email = (getattr(auth, "email", None) or "").strip().lower() or None
    is_admin = bool(getattr(auth, "is_admin", False))
    is_owner = is_skill_owner(db, slug, auth.sub)

    if is_admin and not is_owner:
        role = "admin"
    elif is_owner:
        role = "owner"
    else:
        role = "collaborator"

    name: Optional[str] = None
    if role == "owner":
        row = (
            db.query(SkillSubmission.user_name)
            .filter(
                SkillSubmission.proposed_slug == slug,
                SkillSubmission.review_status == REVIEW_STATUS_PASS,
            )
            .first()
        )
        name = (row[0] if row else None) or None
    elif role == "collaborator" and email:
        row = (
            db.query(SkillCollaborator.user_name)
            .filter(SkillCollaborator.slug == slug, SkillCollaborator.user_email == email)
            .first()
        )
        name = (row[0] if row else None) or None

    return name, email, role


def record_skill_version(
    db: Session,
    slug: str,
    *,
    new_content: str,
    previous_content: str,
    reason: str,
    source: str,
    user_sub: str,
    user_email: Optional[str],
    user_name: Optional[str] = None,
    actor_role: str = "owner",
) -> int:
    """Append version row(s). Seeds a baseline snapshot on first tracked edit."""
    now = datetime.now(timezone.utc)
    existing = _max_version(db, slug)
    next_num = existing + 1

    if existing == 0 and previous_content.strip():
        db.add(
            SkillContentVersion(
                slug=slug,
                version=1,
                content=previous_content,
                content_hash=content_hash(previous_content),
                reason="Baseline before version tracking",
                source="baseline",
                user_sub=user_sub,
                user_email=user_email,
                user_name=user_name,
                actor_role=actor_role,
                lines_added=0,
                lines_removed=0,
                created_at=now,
            )
        )
        next_num = 2

    added, removed = diff_stats(previous_content, new_content)
    db.add(
        SkillContentVersion(
            slug=slug,
            version=next_num,
            content=new_content,
            content_hash=content_hash(new_content),
            reason=reason.strip(),
            source=source,
            user_sub=user_sub,
            user_email=user_email,
            user_name=user_name,
            actor_role=actor_role,
            lines_added=added,
            lines_removed=removed,
            created_at=now,
        )
    )
    db.commit()
    logger.info("skill_version: slug=%s v%s source=%s", slug, next_num, source)
    return next_num


def version_to_summary(row: SkillContentVersion, *, is_current: bool) -> dict[str, Any]:
    actor_label = row.user_name or row.user_email or row.user_sub
    return {
        "version": row.version,
        "is_current": is_current,
        "reason": row.reason,
        "source": row.source,
        "user_email": row.user_email,
        "user_name": row.user_name,
        "actor_role": row.actor_role or "owner",
        "actor_label": actor_label,
        "lines_added": row.lines_added,
        "lines_removed": row.lines_removed,
        "content_hash": row.content_hash,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def list_skill_audit_trail(db: Session, slug: str, *, live_hash: str) -> dict[str, Any]:
    """Human edits only — excludes auto-seeded baseline snapshots."""
    data = list_skill_versions(db, slug, live_hash=live_hash)
    events = [v for v in data["versions"] if v["source"] != "baseline"]
    return {"current_version": data["current_version"], "events": events}


def list_skill_versions(db: Session, slug: str, *, live_hash: str) -> dict[str, Any]:
    rows = (
        db.query(SkillContentVersion)
        .filter(SkillContentVersion.slug == slug)
        .order_by(SkillContentVersion.version.desc())
        .all()
    )
    current_version = None
    for row in rows:
        if row.content_hash == live_hash:
            current_version = row.version
            break
    return {
        "current_version": current_version,
        "versions": [version_to_summary(r, is_current=r.content_hash == live_hash) for r in rows],
    }


def get_skill_version_detail(db: Session, slug: str, version: int) -> Optional[dict[str, Any]]:
    row = _version_row(db, slug, version)
    if row is None:
        return None
    prev = _version_row(db, slug, version - 1) if version > 1 else None
    return {
        "version": row.version,
        "content": row.content,
        "previous_content": prev.content if prev else "",
        "previous_version": prev.version if prev else None,
        "reason": row.reason,
        "source": row.source,
        "user_email": row.user_email,
        "user_name": row.user_name,
        "actor_role": row.actor_role or "owner",
        "actor_label": row.user_name or row.user_email or row.user_sub,
        "lines_added": row.lines_added,
        "lines_removed": row.lines_removed,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
