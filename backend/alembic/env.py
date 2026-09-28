import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Add the backend directory to sys.path so all model modules can be imported.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Import every model module so Base.metadata is fully populated before autogenerate runs.
from database import Base  # noqa: E402

import access_models  # noqa: F401, E402 — CrossDepartmentGrant, AgentAccessGrant
import agents  # noqa: F401, E402 — Agent, SlackEventDedup, etc.
import apps_models  # noqa: F401, E402 — App
import skill_submission_models  # noqa: F401, E402 — SkillSubmission, SkillPreflight
import skill_creation_models  # noqa: F401, E402 — SkillCreationSession
import agent_channel_configs  # noqa: F401, E402 — AgentChannelConfig
import database  # noqa: F401, E402 — UserEvent (analytics)
import artifact_models  # noqa: F401, E402 — SkillArtifact, ToolCodeSample, etc.

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    url = os.environ.get("DATABASE_URL", "")
    # Strip asyncpg driver prefix — Alembic uses psycopg2 (sync).
    return url.replace("postgresql+asyncpg://", "postgresql+psycopg2://")


def run_migrations_offline() -> None:
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = _get_url()
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
