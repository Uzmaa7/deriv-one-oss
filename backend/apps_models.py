"""SQLAlchemy model for user-created Lab apps.

Each app is a directory of files stored in the hub-generated Docker volume at
/app/generated/apps/{app_id}/.  Only metadata (name, slug, owner, visibility,
status) is persisted in SQLite; the actual files live on disk.

Path scheme:  /app/generated/apps/{app_id}/
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy import text as sa_text

from database import Base, _uuid

# ── Constants ─────────────────────────────────────────────────────────────────

APP_VISIBILITIES = ("private", "shared", "workspace")
APP_STATUSES = ("draft", "deployed", "error", "archived")
APP_TECH_STACKS = ("static", "python", "react")
LAB_MODES = ("plan", "agent")

MAX_APP_NAME = 120
MAX_APP_DESCRIPTION = 500
MAX_SLUG = 80

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{0,78}[a-z0-9]$|^[a-z0-9]$")


def _slugify(name: str, suffix: str) -> str:
    """Convert a name to a URL-safe slug with a short unique suffix."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    s = s[:40].strip("-") or "app"
    return f"{s}-{suffix}"


# ── Model ─────────────────────────────────────────────────────────────────────

class App(Base):
    """A user-created web application built in the Lab."""

    __tablename__ = "apps"

    id            = Column(String, primary_key=True, default=_uuid)
    slug          = Column(String(MAX_SLUG), unique=True, nullable=False, index=True)
    name          = Column(String(MAX_APP_NAME), nullable=False)
    description   = Column(String(MAX_APP_DESCRIPTION), nullable=True)

    # Tenancy
    owner_sub     = Column(String, nullable=False, index=True)
    creator_email = Column(String(320), nullable=False, default="")

    # State
    visibility    = Column(String(16), nullable=False, default="private")
    status        = Column(String(16), nullable=False, default="draft")
    tech_stack    = Column(String(16), nullable=False, default="static")
    # Build mode: "standard" (interactive chat builder) | "overnight" (autonomous spec-driven build)
    mode          = Column(String(16), nullable=False, default="standard")
    # Lab workflow: "plan" (refine plan + Q&A) | "agent" (write code)
    lab_mode      = Column(String(16), nullable=False, default="plan")

    # JSON list of relative file paths currently in the app directory
    file_manifest = Column(Text, nullable=False, default="[]")

    # Conversation history for the AI builder (list of {role, content} dicts)
    chat_history  = Column(Text, nullable=False, default="[]")

    # JSON list of skill slugs attached to this app
    skill_ids     = Column(Text, nullable=False, default="[]")

    # Fernet-encrypted JSON blob {"ENV_KEY": "value"} for per-app secrets
    env_config    = Column(Text, nullable=True)

    # JSON list of email addresses explicitly granted read access (visibility="shared")
    allowed_users = Column(Text, nullable=False, default="[]")

    # JSON list of email addresses granted admin role (see all records; workspace or shared)
    admin_users = Column(Text, nullable=False, default="[]")

    # Standard Lab background build state (survives navigation away from /lab)
    build_phase     = Column(String(16), nullable=True)   # building | completed | failed
    build_timeline  = Column(Text, nullable=False, default="[]")  # JSON event log
    build_updated_at = Column(DateTime, nullable=True)

    # AI classification metadata from create_app (JSON) — persisted in the database so every replica sees it
    classification_meta = Column(Text, nullable=True)

    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, nullable=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

def app_to_workspace_dict(a: App) -> dict:
    """Minimal app metadata for workspace/shared viewers (no source or build internals)."""
    return {
        "id": a.id,
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "visibility": a.visibility,
        "status": a.status,
        "tech_stack": a.tech_stack,
        "mode": getattr(a, "mode", "standard") or "standard",
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


def app_to_dict(a: App, env_var_keys: Optional[list] = None) -> dict:
    """Public serialisation of an App row. Never includes raw file content or secret values."""
    return {
        "id": a.id,
        "slug": a.slug,
        "name": a.name,
        "description": a.description,
        "creator_email": a.creator_email,
        "visibility": a.visibility,
        "status": a.status,
        "tech_stack": a.tech_stack,
        "mode": getattr(a, "mode", "standard") or "standard",
        "lab_mode": getattr(a, "lab_mode", "plan") or "plan",
        "file_manifest": _load_json_list(a.file_manifest),
        "chat_history": _load_json_list(a.chat_history),
        "skill_ids": _load_json_list(a.skill_ids),
        "allowed_users": _load_json_list(a.allowed_users),
        "admin_users": _load_json_list(getattr(a, "admin_users", None)),
        "env_var_keys": env_var_keys if env_var_keys is not None else [],
        "build_phase": getattr(a, "build_phase", None),
        "build_updated_at": (
            a.build_updated_at.isoformat() if getattr(a, "build_updated_at", None) else None
        ),
        "created_at": a.created_at.isoformat(),
        "updated_at": a.updated_at.isoformat(),
    }


def _load_json_list(value: Optional[str]) -> list:
    if not value:
        return []
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def generate_slug(name: str, db_session) -> str:
    """Generate a unique slug for the given app name."""
    import uuid
    for _ in range(10):
        suffix = uuid.uuid4().hex[:6]
        candidate = _slugify(name, suffix)
        existing = db_session.query(App.id).filter(App.slug == candidate).first()
        if not existing:
            return candidate
    # Last resort: pure uuid-based slug
    return f"app-{uuid.uuid4().hex[:12]}"


def validate_slug(slug: str) -> bool:
    return bool(_SLUG_RE.match(slug)) and len(slug) <= MAX_SLUG


# ── DDL migration ─────────────────────────────────────────────────────────────

def _table_exists(conn, table: str) -> bool:
    row = conn.execute(
        sa_text(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = :n AND table_schema = 'public'"
        ),
        {"n": table},
    ).fetchone()
    if row is not None:
        return True
    try:
        row = conn.execute(
            sa_text("SELECT 1 FROM sqlite_master WHERE type='table' AND name=:n"),
            {"n": table},
        ).fetchone()
        return row is not None
    except Exception:
        return False


def _add_column_if_missing(conn, table: str, column: str, column_def: str) -> None:
    """Add a column to an existing table if it is not already present.

    Supports PostgreSQL (information_schema) and SQLite (sqlite_master pragma).
    """
    # PostgreSQL path — preferred
    try:
        row = conn.execute(
            sa_text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
        if not row:
            conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}"))  # noqa: S608
        return
    except Exception:
        pass

    # SQLite fallback
    try:
        rows = conn.execute(sa_text(f"PRAGMA table_info({table})")).fetchall()  # noqa: S608
        existing = {row[1] for row in rows}
        if column not in existing:
            conn.execute(sa_text(f"ALTER TABLE {table} ADD COLUMN {column} {column_def}"))  # noqa: S608
    except Exception:
        pass


class LabDraftRow(Base):
    """A single in-progress Lab (New App) draft for a user (auto-save).

    One row per owner_sub. Stores New App modal form state so the user can leave
    and resume. No credentials — name, description, mode, skill slugs, sheet URL.
    """

    __tablename__ = "lab_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    owner_sub = Column(String, nullable=False, unique=True, index=True)
    draft = Column(Text, nullable=False)  # JSON of form state
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False, onupdate=datetime.utcnow)


def init_app_tables(engine) -> None:
    """Create the apps table if it does not yet exist, then add any missing columns. Idempotent."""
    Base.metadata.create_all(engine, tables=[App.__table__, LabDraftRow.__table__])
    with engine.connect() as conn:
        _add_column_if_missing(conn, "apps", "skill_ids",     "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "apps", "env_config",    "TEXT")
        _add_column_if_missing(conn, "apps", "allowed_users", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "apps", "admin_users", "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "apps", "mode",          "VARCHAR(16) NOT NULL DEFAULT 'standard'")
        _add_column_if_missing(conn, "apps", "lab_mode", "VARCHAR(16) NOT NULL DEFAULT 'plan'")
        _add_column_if_missing(conn, "apps", "build_phase",     "VARCHAR(16)")
        _add_column_if_missing(conn, "apps", "build_timeline",  "TEXT NOT NULL DEFAULT '[]'")
        _add_column_if_missing(conn, "apps", "build_updated_at", "TIMESTAMP")
        _add_column_if_missing(conn, "apps", "classification_meta", "TEXT")
        conn.commit()

