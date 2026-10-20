"""SQLAlchemy ORM model for AI-assisted skill creation jobs.

Table:
  skill_creation_jobs  — lifecycle: pending → fetching → generating → done | error
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import Column, DateTime, String, Text
from sqlalchemy import text as sa_text

from database import Base, engine

logger = logging.getLogger(__name__)


def _uuid() -> str:
    return str(uuid.uuid4())


JOB_STATUS_PENDING = "pending"
JOB_STATUS_FETCHING = "fetching"      # fetching API reference from URL
JOB_STATUS_GENERATING = "generating"  # Claude is writing the skill
JOB_STATUS_DONE = "done"
JOB_STATUS_ERROR = "error"


class SkillCreationJob(Base):
    """Tracks an AI-assisted skill creation request.

    The user provides a name, optional description, and optionally an API
    reference (URL or uploaded file content).  A background task fetches any
    URL content, assembles context, and calls the Claude API to generate a
    complete SKILL.md.  The result is stored in `result_content` for the
    frontend to display and let the user edit before publishing.
    """

    __tablename__ = "skill_creation_jobs"

    id = Column(String, primary_key=True, default=_uuid)
    owner_sub = Column(String, nullable=False, index=True)

    # Inputs
    skill_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    api_ref_url = Column(String(2048), nullable=True)
    api_ref_content = Column(Text, nullable=True)  # uploaded file or fetched URL content

    # Job lifecycle
    status = Column(String, nullable=False, default=JOB_STATUS_PENDING, index=True)
    status_detail = Column(String(500), nullable=True)  # human-readable progress message

    # Output
    result_content = Column(Text, nullable=True)   # generated SKILL.md
    error_message = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)


def init_skill_creation_tables(db_engine=None) -> None:
    """Create skill_creation_jobs table (idempotent)."""
    target = db_engine or engine
    SkillCreationJob.__table__.create(target, checkfirst=True)
    logger.info("skill_creation_models: skill_creation_jobs table ready")
