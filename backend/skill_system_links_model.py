"""SQLAlchemy ORM model for resolved skill -> connected-system links.

One row per (skill_slug, integration_tag) pair, holding the best-effort match
against the system catalogue — name + criticality only (see
skill_system_matcher.py). Populated by backend/scripts/backfill_skill_systems.py
for existing skills, and refreshed automatically whenever a skill is published
or edited (backend/api_skill_publish.py).
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Session

from database import Base, engine

logger = logging.getLogger(__name__)


class SkillSystemLink(Base):
    """A single skill integration tag resolved (or not) to an the system catalogue system."""

    __tablename__ = "skill_system_links"
    __table_args__ = (
        UniqueConstraint("skill_slug", "integration_tag", name="uq_skill_system_links_slug_tag"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_slug = Column(String, nullable=False, index=True)
    integration_tag = Column(String, nullable=False)

    system_name = Column(String, nullable=True)
    risk_level = Column(String, nullable=True)
    match_confidence = Column(String, nullable=False, default="unmatched")
    # "integration_tag" (declared by publisher) or "skill_content" (detected
    # from the skill's name/description/SKILL.md body).
    source = Column(String, nullable=False, default="integration_tag")
    # "read" | "write" | "read_write" | None (unknown).
    direction = Column(String, nullable=True)

    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)


def init_skill_system_links_table(db_engine=None) -> None:
    target = db_engine or engine
    SkillSystemLink.__table__.create(target, checkfirst=True)

    with target.begin() as conn:
        from database import table_cols
        existing = table_cols(conn, "skill_system_links")
        if "source" not in existing:
            conn.execute(
                sa_text(
                    "ALTER TABLE skill_system_links ADD COLUMN source TEXT NOT NULL DEFAULT 'integration_tag'"
                )
            )
            logger.info("skill_system_links_model: added column source to skill_system_links")
        if "direction" not in existing:
            conn.execute(sa_text("ALTER TABLE skill_system_links ADD COLUMN direction TEXT"))
            logger.info("skill_system_links_model: added column direction to skill_system_links")


_RISK_ORDER = {"critical": 3, "high": 2, "elevated": 2, "medium": 1, "low": 0}


def _risk_rank(risk_level: str | None) -> int:
    if not risk_level:
        return -1
    return _RISK_ORDER.get(risk_level.strip().lower(), 0)


def upsert_skill_system_links(db: Session, skill_slug: str, results: list) -> None:
    """Replace all skill_system_links rows for *skill_slug* with *results*.

    *results* is a list of skill_system_matcher.MatchResult.
    """
    db.query(SkillSystemLink).filter(SkillSystemLink.skill_slug == skill_slug).delete()
    for r in results:
        db.add(
            SkillSystemLink(
                skill_slug=skill_slug,
                integration_tag=r.integration_tag,
                system_name=r.system_name,
                risk_level=r.risk_level,
                match_confidence=r.confidence,
                source=getattr(r, "source", "integration_tag"),
                direction=getattr(r, "direction", None),
            )
        )
    db.commit()


def get_system_links_for_skill(db: Session, skill_slug: str) -> list[SkillSystemLink]:
    return (
        db.query(SkillSystemLink)
        .filter(SkillSystemLink.skill_slug == skill_slug)
        .order_by(SkillSystemLink.integration_tag)
        .all()
    )


def get_system_links_map(db: Session, skill_slugs: list[str]) -> dict[str, list[SkillSystemLink]]:
    if not skill_slugs:
        return {}
    rows = (
        db.query(SkillSystemLink)
        .filter(SkillSystemLink.skill_slug.in_(skill_slugs))
        .all()
    )
    result: dict[str, list[SkillSystemLink]] = {slug: [] for slug in skill_slugs}
    for row in rows:
        result.setdefault(row.skill_slug, []).append(row)
    return result


def max_criticality_for_links(links: list[SkillSystemLink]) -> str | None:
    """Return the highest the system catalogue risk_level among *links*, or None if all unmatched."""
    matched = [l for l in links if l.risk_level]
    if not matched:
        return None
    return max(matched, key=lambda l: _risk_rank(l.risk_level)).risk_level
