"""Persistent platform setup wizard state."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, JSON, String

from database import Base


class PlatformSetup(Base):
    """Singleton row tracking first-run setup wizard progress."""

    __tablename__ = "platform_setup"

    id = Column(String, primary_key=True, default="default")
    completed = Column(Boolean, nullable=False, default=False)
    deployment_mode = Column(String(32), nullable=True)  # personal | team
    draft_json = Column(JSON, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=True, default=datetime.utcnow)


def init_setup_tables(engine) -> None:
    PlatformSetup.__table__.create(engine, checkfirst=True)
