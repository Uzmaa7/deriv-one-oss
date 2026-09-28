"""Skill collaborator helpers — co-managers who can edit skills and secrets."""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from skill_submission_models import (
    REVIEW_STATUS_PASS,
    SkillCollaborator,
    SkillSubmission,
)


def collaborator_slugs_for_email(db: Session, email: str) -> frozenset[str]:
    """Return skill slugs where *email* is a collaborator."""
    normalized = (email or "").strip().lower()
    if not normalized:
        return frozenset()
    rows = (
        db.query(SkillCollaborator.slug)
        .filter(SkillCollaborator.user_email == normalized)
        .all()
    )
    return frozenset(r.slug for r in rows)


def is_skill_owner(db: Session, slug: str, user_sub: str) -> bool:
    return (
        db.query(SkillSubmission.id)
        .filter(
            SkillSubmission.proposed_slug == slug,
            SkillSubmission.review_status == REVIEW_STATUS_PASS,
            SkillSubmission.user_id == user_sub,
        )
        .first()
        is not None
    )


def is_skill_collaborator(db: Session, slug: str, email: str) -> bool:
    normalized = (email or "").strip().lower()
    if not normalized:
        return False
    return (
        db.query(SkillCollaborator.id)
        .filter(
            SkillCollaborator.slug == slug,
            SkillCollaborator.user_email == normalized,
        )
        .first()
        is not None
    )


def is_skill_owner_or_collaborator(
    db: Session,
    slug: str,
    user_sub: str,
    email: str,
) -> bool:
    return is_skill_owner(db, slug, user_sub) or is_skill_collaborator(db, slug, email)


def skill_owner_email(db: Session, slug: str) -> Optional[str]:
    row = (
        db.query(SkillSubmission.user_email)
        .filter(
            SkillSubmission.proposed_slug == slug,
            SkillSubmission.review_status == REVIEW_STATUS_PASS,
        )
        .order_by(SkillSubmission.submitted_at.desc())
        .first()
    )
    if not row or not row.user_email:
        return None
    return row.user_email.strip().lower()
