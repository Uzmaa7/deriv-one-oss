"""SQLAlchemy models for skill-attached Python artifacts."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy import text as sa_text

from database import Base, engine
from skill_submission_models import (
    REVIEW_STATUS_FAIL,
    REVIEW_STATUS_PASS,
    REVIEW_STATUS_PENDING,
)

ARTIFACT_STATUS_DRAFT = "draft"
ARTIFACT_STATUS_PENDING_APPROVAL = "pending_approval"
ARTIFACT_STATUS_APPROVED = "approved"
ARTIFACT_STATUS_BLOCKED = "blocked"
ARTIFACT_STATUS_ARCHIVED = "archived"

EFFECT_READ = "read"
EFFECT_WRITE = "write"

AUTHOR_HUMAN = "human"
AUTHOR_AI_SUGGESTED = "ai_suggested"


def _uuid() -> str:
    return str(uuid.uuid4())


class SkillArtifact(Base):
    __tablename__ = "skill_artifacts"
    __table_args__ = (
        UniqueConstraint("slug", "name", "version", name="uq_skill_artifact_version"),
    )

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    name = Column(String(40), nullable=False)
    version = Column(Integer, nullable=False, default=1)

    code = Column(Text, nullable=False)
    code_sha256 = Column(String(64), nullable=False)
    manifest_json = Column(Text, nullable=False)
    entrypoint = Column(String(80), nullable=False)
    params_schema_json = Column(Text, nullable=True)
    declared_hosts_json = Column(Text, nullable=False, default="[]")
    declared_secrets_json = Column(Text, nullable=False, default="[]")
    effect_class = Column(String(16), nullable=False, default=EFFECT_READ)
    api_summary = Column(Text, nullable=True)

    gate_status = Column(String(16), nullable=False)
    gate_report_json = Column(Text, nullable=True)
    ai_review_status = Column(String(16), nullable=False, default=REVIEW_STATUS_PENDING)
    ai_review_reason = Column(Text, nullable=True)

    status = Column(String(24), nullable=False, default=ARTIFACT_STATUS_DRAFT, index=True)
    author_kind = Column(String(16), nullable=False, default=AUTHOR_HUMAN)

    uploader_sub = Column(String, nullable=False)
    uploader_email = Column(String(320), nullable=True)
    approved_by_sub = Column(String, nullable=True)
    approved_by_email = Column(String(320), nullable=True)
    approved_at = Column(DateTime, nullable=True)

    run_count = Column(Integer, nullable=False, default=0)
    last_ok_at = Column(DateTime, nullable=True)
    last_error_at = Column(DateTime, nullable=True)
    disabled = Column(Boolean, nullable=False, default=False)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class SkillArtifactPreflight(Base):
    __tablename__ = "skill_artifact_preflights"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    owner_sub = Column(String, nullable=False, index=True)
    content_hash = Column(String(64), nullable=False)
    manifest_hash = Column(String(64), nullable=False)
    gate_status = Column(String(16), nullable=False)
    gate_report_json = Column(Text, nullable=True)
    ai_review_status = Column(String, nullable=False, default=REVIEW_STATUS_PENDING)
    ai_review_reason = Column(Text, nullable=True)
    proposed_code = Column(Text, nullable=False)
    proposed_manifest_json = Column(Text, nullable=False)
    reviewed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class ToolCodeSample(Base):
    __tablename__ = "tool_code_samples"

    id = Column(String, primary_key=True, default=_uuid)
    agent_id = Column(String, nullable=False, index=True)
    skill_slugs_json = Column(Text, nullable=False, default="[]")
    tool = Column(String(32), nullable=False, default="run_python")
    code_redacted = Column(Text, nullable=False)
    code_sha256 = Column(String(64), nullable=False)
    shape_sha256 = Column(String(64), nullable=True)
    code_len = Column(Integer, nullable=False)
    ok = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)


JOB_STATUS_PENDING = "pending"
JOB_STATUS_RUNNING = "running"
JOB_STATUS_DONE = "done"
JOB_STATUS_FAILED = "failed"

TEST_STATUS_PENDING = "pending"
TEST_STATUS_RUNNING = "running"
TEST_STATUS_PASSED = "passed"
TEST_STATUS_FAILED = "failed"
TEST_STATUS_ERROR = "error"


REC_STATUS_PROPOSED = "proposed"
REC_STATUS_GENERATING = "generating"
REC_STATUS_DRAFT_READY = "draft_ready"
REC_STATUS_PUBLISHED = "published"
REC_STATUS_DISMISSED = "dismissed"
REC_STATUS_FAILED = "failed"


class SkillArtifactPlan(Base):
    """AI analysis of a skill suggesting discrete artifacts to build."""

    __tablename__ = "skill_artifact_plans"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    owner_sub = Column(String, nullable=False, index=True)
    status = Column(String(16), nullable=False, default=JOB_STATUS_PENDING, index=True)
    status_detail = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SkillArtifactRecommendation(Base):
    """One suggested artifact from a plan; persisted until published or dismissed."""

    __tablename__ = "skill_artifact_recommendations"

    id = Column(String, primary_key=True, default=_uuid)
    plan_id = Column(String, nullable=False, index=True)
    slug = Column(String, nullable=False, index=True)
    name = Column(String(40), nullable=False)
    description = Column(Text, nullable=False)
    effect_class = Column(String(16), nullable=False, default=EFFECT_READ)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default=REC_STATUS_PROPOSED, index=True)
    suggest_job_id = Column(String, nullable=True)
    published_artifact_id = Column(String, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)


def recommendation_to_dict(row: SkillArtifactRecommendation) -> dict:
    return {
        "id": row.id,
        "plan_id": row.plan_id,
        "slug": row.slug,
        "name": row.name,
        "description": row.description,
        "effect_class": row.effect_class,
        "sort_order": row.sort_order,
        "status": row.status,
        "suggest_job_id": row.suggest_job_id,
        "published_artifact_id": row.published_artifact_id,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def plan_to_dict(row: SkillArtifactPlan) -> dict:
    return {
        "id": row.id,
        "slug": row.slug,
        "status": row.status,
        "status_detail": row.status_detail,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


class SkillArtifactTestSession(Base):
    """Sandbox test run for a draft artifact before human publish."""

    __tablename__ = "skill_artifact_test_sessions"

    id = Column(String, primary_key=True, default=_uuid)
    job_id = Column(String, nullable=False, index=True)
    slug = Column(String, nullable=False, index=True)
    owner_sub = Column(String, nullable=False, index=True)
    params_json = Column(Text, nullable=False)
    auto_fix = Column(Boolean, nullable=False, default=False)
    max_attempts = Column(Integer, nullable=False, default=3)
    status = Column(String(16), nullable=False, default=TEST_STATUS_PENDING, index=True)
    attempts_json = Column(Text, nullable=False, default="[]")
    final_code = Column(Text, nullable=True)
    final_manifest_json = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


class SkillArtifactSuggestJob(Base):
    """Background AI draft of a skill artifact awaiting human accept."""

    __tablename__ = "skill_artifact_suggest_jobs"

    id = Column(String, primary_key=True, default=_uuid)
    slug = Column(String, nullable=False, index=True)
    owner_sub = Column(String, nullable=False, index=True)
    artifact_name_hint = Column(String(40), nullable=True)
    artifact_description = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=JOB_STATUS_PENDING, index=True)
    status_detail = Column(Text, nullable=True)
    proposed_code = Column(Text, nullable=True)
    proposed_manifest_json = Column(Text, nullable=True)
    gate_report_json = Column(Text, nullable=True)
    verification_note = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    recommendation_id = Column(String, nullable=True, index=True)
    source_artifact_id = Column(String, nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


def artifact_to_dict(row: SkillArtifact) -> dict:
    return {
        "id": row.id,
        "slug": row.slug,
        "name": row.name,
        "version": row.version,
        "code_sha256": row.code_sha256,
        "entrypoint": row.entrypoint,
        "effect_class": row.effect_class,
        "api_summary": row.api_summary,
        "gate_status": row.gate_status,
        "ai_review_status": row.ai_review_status,
        "status": row.status,
        "author_kind": row.author_kind,
        "disabled": row.disabled,
        "run_count": row.run_count,
        "last_ok_at": row.last_ok_at.isoformat() if row.last_ok_at else None,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def parse_manifest(raw: str) -> dict:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("manifest must be a JSON object")
    return data


def init_artifact_tables(db_engine=None) -> None:
    import logging

    logger = logging.getLogger(__name__)
    target = db_engine or engine
    SkillArtifact.__table__.create(target, checkfirst=True)
    SkillArtifactPreflight.__table__.create(target, checkfirst=True)
    ToolCodeSample.__table__.create(target, checkfirst=True)
    SkillArtifactSuggestJob.__table__.create(target, checkfirst=True)
    SkillArtifactPlan.__table__.create(target, checkfirst=True)
    SkillArtifactRecommendation.__table__.create(target, checkfirst=True)
    SkillArtifactTestSession.__table__.create(target, checkfirst=True)
    with target.begin() as conn:
        from database import table_cols

        existing = table_cols(conn, "skill_artifacts")
        for col, defn in [
            ("author_kind", "TEXT NOT NULL DEFAULT 'human'"),
        ]:
            if col not in existing:
                conn.execute(sa_text(f"ALTER TABLE skill_artifacts ADD COLUMN {col} {defn}"))
                logger.info("artifact_models: added column %s to skill_artifacts", col)

        job_cols = table_cols(conn, "skill_artifact_suggest_jobs")
        if "recommendation_id" not in job_cols:
            conn.execute(sa_text(
                "ALTER TABLE skill_artifact_suggest_jobs ADD COLUMN recommendation_id TEXT"
            ))
            logger.info("artifact_models: added recommendation_id to skill_artifact_suggest_jobs")
        if "artifact_description" not in job_cols:
            conn.execute(sa_text(
                "ALTER TABLE skill_artifact_suggest_jobs ADD COLUMN artifact_description TEXT"
            ))
            logger.info("artifact_models: added artifact_description to skill_artifact_suggest_jobs")
        if "source_artifact_id" not in job_cols:
            conn.execute(sa_text(
                "ALTER TABLE skill_artifact_suggest_jobs ADD COLUMN source_artifact_id TEXT"
            ))
            logger.info("artifact_models: added source_artifact_id to skill_artifact_suggest_jobs")
