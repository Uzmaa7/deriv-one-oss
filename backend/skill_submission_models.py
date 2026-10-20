"""SQLAlchemy ORM model for user-submitted skills awaiting AI security review.

Tables:
  skill_submissions  — lifecycle: pending → reviewing → awaiting_confirmation → pass | fail | modify
  skill_preflights   — lightweight pre-submit security review tied to a file upload
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Column, DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy import text as sa_text

from database import Base, engine

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


# Review status values
REVIEW_STATUS_PENDING = "pending"
REVIEW_STATUS_REVIEWING = "reviewing"
REVIEW_STATUS_PASS = "pass"
REVIEW_STATUS_FAIL = "fail"
REVIEW_STATUS_MODIFY = "modify"
# Set after AI review passes but before publish: the human must review/edit
# the auto-detected system-access list (see api_skill_publish.py) before the
# skill actually goes live.
REVIEW_STATUS_AWAITING_CONFIRMATION = "awaiting_confirmation"


class SkillSubmission(Base):
    """A skill submitted by a user, pending AI security review.

    ACL metadata fields mirror the structure of skills-acl.yaml so they can be
    written verbatim on approval.  Array-valued fields (departments,
    integrations, cross_dept_approvers) are stored as JSON strings.
    """

    __tablename__ = "skill_submissions"

    id = Column(String, primary_key=True, default=_uuid)

    # Author
    user_id = Column(String, nullable=False, index=True)
    user_email = Column(String, nullable=False)
    user_name = Column(String, nullable=True)
    user_department = Column(String, nullable=False, default="")

    # Proposed ACL metadata
    proposed_slug = Column(String, nullable=False, index=True)
    departments = Column(Text, nullable=False, default="[]")          # JSON array
    integrations = Column(Text, nullable=False, default="[]")         # JSON array
    risk_level = Column(String, nullable=False, default="standard")
    cross_dept_policy = Column(String, nullable=False, default="requestable")
    cross_dept_default_grant = Column(String, nullable=False, default="time_limited_30d")
    cross_dept_approvers = Column(Text, nullable=True, default="[]")  # JSON array
    description = Column(Text, nullable=False)
    display_name = Column(String(120), nullable=False, default="")

    # Raw SKILL.md content
    skill_file_content = Column(Text, nullable=False)

    # Transient secret values held during AI review.
    # Format: JSON array of {"key": "ENV_VAR_NAME", "value": "..."}.
    # Written to the vault inside _publish_approved_skill on pass,
    # then cleared.  Cleared immediately (set to NULL) on fail/modify so
    # plaintext values never persist after review completes.
    pending_secrets = Column(Text, nullable=True)

    # AI review outcome
    review_status = Column(
        String, nullable=False, default=REVIEW_STATUS_PENDING, index=True
    )
    review_reason = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    # Timestamps
    submitted_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    # Who submitted an edit to an existing skill (distinct from user_id which stays the owner).
    editor_sub = Column(String, nullable=True)
    editor_email = Column(String(320), nullable=True)
    editor_name = Column(String(255), nullable=True)
    editor_role = Column(String(32), nullable=True)  # owner | collaborator | admin

    # System-access confirmation gate (set when review_status transitions
    # through REVIEW_STATUS_AWAITING_CONFIRMATION). JSON arrays of
    # skill_system_matcher.MatchResult-shaped dicts.
    proposed_system_links = Column(Text, nullable=True)
    confirmed_system_links = Column(Text, nullable=True)
    system_links_confirmed_at = Column(DateTime, nullable=True)


class SkillSecret(Base):
    """A published skill's secret, stored as vault referenceerence.

    One row per secret key per skill slug.  The actual secret value lives in
    the vault; only the opaque version ref is persisted here.
    """

    __tablename__ = "skill_secrets"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    env_key = Column(String(100), nullable=False)    # e.g. "IAM_API_KEY"
    secret_ref = Column(String(512), nullable=False)  # the platform's version resource name
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SkillPreflight(Base):
    """Lightweight record for a pre-submit AI security review tied to a single file upload.

    Created when the user uploads a SKILL.md before filling the publish form.
    The `content_hash` (SHA-256 of raw file bytes) is stored so that the final
    POST /api/skills/submit can skip a second review if the file hasn't changed.

    `extracted_secret_keys` holds a JSON array of UPPER_SNAKE_CASE env var names
    that the AI reviewer identified as runtime secrets needed by the skill.
    """

    __tablename__ = "skill_preflights"

    id = Column(String, primary_key=True, default=_uuid)
    owner_sub = Column(String, nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)   # hex SHA-256 of file bytes
    review_status = Column(
        String, nullable=False, default=REVIEW_STATUS_PENDING, index=True
    )
    review_reason = Column(Text, nullable=True)
    extracted_secret_keys = Column(Text, nullable=True)  # JSON array of key name strings
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SkillAiEditProposal(Base):
    """Short-lived AI edit proposal that passed security review and awaits user apply."""

    __tablename__ = "skill_ai_edit_proposals"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    user_sub = Column(String, nullable=False, index=True)
    proposed_content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)  # hash of proposed content
    base_content_hash = Column(String(64), nullable=True)  # live hash at propose time
    summary = Column(Text, nullable=True)
    review_reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=False, index=True)


class SkillContentVersion(Base):
    """Immutable snapshot of SKILL.md after each manual/AI save or revert."""

    __tablename__ = "skill_content_versions"
    __table_args__ = (UniqueConstraint("slug", "version", name="uq_skill_content_version"),)

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    version = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    content_hash = Column(String(64), nullable=False)
    reason = Column(Text, nullable=False)
    source = Column(String(32), nullable=False)  # baseline | manual_edit | ai_edit | revert | skill_edit
    user_sub = Column(String, nullable=False)
    user_email = Column(String(320), nullable=True)
    user_name = Column(String(255), nullable=True)
    actor_role = Column(String(32), nullable=False, default="owner")  # owner | collaborator | admin
    lines_added = Column(Integer, nullable=False, default=0)
    lines_removed = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SkillCollaborator(Base):
    """Co-manager of a published skill — can edit content and secrets.

    Only the skill owner (or platform admin) may add collaborators.
    Collaborators cannot delete the skill or add other collaborators.
    """

    __tablename__ = "skill_collaborators"
    __table_args__ = (UniqueConstraint("slug", "user_email", name="uq_skill_collaborators"),)

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    user_email = Column(String(320), nullable=False, index=True)
    user_name = Column(String(255), nullable=True)
    added_by_email = Column(String(320), nullable=False, default="")
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def collaborator_to_dict(c: SkillCollaborator) -> dict[str, Any]:
    return {
        "id": c.id,
        "slug": c.slug,
        "user_email": c.user_email,
        "user_name": c.user_name,
        "added_by_email": c.added_by_email,
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def submission_to_dict(s: SkillSubmission) -> dict[str, Any]:
    import json

    def _parse_json(val: str | None) -> list:
        if not val:
            return []
        try:
            return json.loads(val)
        except (ValueError, TypeError):
            return []

    return {
        "id": s.id,
        "user_id": s.user_id,
        "user_email": s.user_email,
        "user_name": s.user_name,
        "user_department": s.user_department,
        "proposed_slug": s.proposed_slug,
        "departments": _parse_json(s.departments),
        "integrations": _parse_json(s.integrations),
        "risk_level": s.risk_level,
        "cross_dept_policy": s.cross_dept_policy,
        "cross_dept_default_grant": s.cross_dept_default_grant,
        "cross_dept_approvers": _parse_json(s.cross_dept_approvers),
        "description": s.description,
        "display_name": getattr(s, "display_name", "") or "",
        "skill_file_content": s.skill_file_content,
        "review_status": s.review_status,
        "review_reason": s.review_reason,
        "reviewed_at": s.reviewed_at.isoformat() if s.reviewed_at else None,
        "submitted_at": s.submitted_at.isoformat() if s.submitted_at else None,
        "created_at": s.created_at.isoformat() if s.created_at else None,
        "proposed_system_links": _parse_json(getattr(s, "proposed_system_links", None)),
        "confirmed_system_links": _parse_json(getattr(s, "confirmed_system_links", None)),
        "system_links_confirmed_at": (
            s.system_links_confirmed_at.isoformat()
            if getattr(s, "system_links_confirmed_at", None)
            else None
        ),
    }


class SkillDraftRow(Base):
    """A single in-progress skill publish draft for a user (auto-save).

    One row per owner_sub (upserted on save). Stores form fields so the user can
    leave /publish and resume later. Never stores secret *values* — only key names
    and non-credential form state (including SKILL.md text content).
    """

    __tablename__ = "skill_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_sub = Column(String, nullable=False, unique=True, index=True)
    draft = Column(Text, nullable=False)  # JSON of form state
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)


def init_skill_submission_tables(db_engine=None) -> None:
    """Create skill_submissions, skill_secrets, and skill_preflights tables and apply incremental migrations."""
    target = db_engine or engine

    SkillSubmission.__table__.create(target, checkfirst=True)
    SkillSecret.__table__.create(target, checkfirst=True)
    SkillPreflight.__table__.create(target, checkfirst=True)
    SkillDraftRow.__table__.create(target, checkfirst=True)
    SkillCollaborator.__table__.create(target, checkfirst=True)
    SkillAiEditProposal.__table__.create(target, checkfirst=True)
    SkillContentVersion.__table__.create(target, checkfirst=True)

    with target.begin() as conn:
        # ── skill_submissions migrations ───────────────────────────────────────
        from database import table_cols
        existing = table_cols(conn, "skill_submissions")

        for col, defn in [
            ("user_name", "TEXT"),
            ("review_reason", "TEXT"),
            ("pending_secrets", "TEXT"),
            ("display_name", "TEXT NOT NULL DEFAULT ''"),
            ("editor_sub", "TEXT"),
            ("editor_email", "TEXT"),
            ("editor_name", "TEXT"),
            ("editor_role", "TEXT"),
            ("proposed_system_links", "TEXT"),
            ("confirmed_system_links", "TEXT"),
            ("system_links_confirmed_at", "TIMESTAMP"),
        ]:
            if col not in existing:
                conn.execute(
                    sa_text(f"ALTER TABLE skill_submissions ADD COLUMN {col} {defn}")
                )
                logger.info(
                    "skill_submission_models: added column %s to skill_submissions", col
                )

        # ── skill_preflights migrations ────────────────────────────────────────
        pf_existing = table_cols(conn, "skill_preflights")

        for col, defn in [
            ("extracted_secret_keys", "TEXT"),
        ]:
            if col not in pf_existing:
                conn.execute(
                    sa_text(f"ALTER TABLE skill_preflights ADD COLUMN {col} {defn}")
                )
                logger.info(
                    "skill_submission_models: added column %s to skill_preflights", col
                )

        # ── skill_content_versions migrations ────────────────────────────────────
        cv_existing = table_cols(conn, "skill_content_versions")

        for col, defn in [
            ("user_name", "TEXT"),
            ("actor_role", "TEXT NOT NULL DEFAULT 'owner'"),
        ]:
            if col not in cv_existing:
                conn.execute(
                    sa_text(f"ALTER TABLE skill_content_versions ADD COLUMN {col} {defn}")
                )
                logger.info(
                    "skill_submission_models: added column %s to skill_content_versions", col
                )

        # ── skill_ai_edit_proposals migrations ───────────────────────────────────
        prop_existing = table_cols(conn, "skill_ai_edit_proposals")
        if "base_content_hash" not in prop_existing:
            conn.execute(
                sa_text("ALTER TABLE skill_ai_edit_proposals ADD COLUMN base_content_hash TEXT")
            )
            logger.info(
                "skill_submission_models: added column base_content_hash to skill_ai_edit_proposals"
            )
