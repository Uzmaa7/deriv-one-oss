"""drop computer-use and role-blueprint tables

Revision ID: aa1b2c3d4e5f
Revises: a2b3c4d5e6f7
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "aa1b2c3d4e5f"
down_revision: Union[str, None] = "a2b3c4d5e6f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

BROWSER_TABLES = (
    "browser_attachment",
    "browser_task_run",
    "browser_task_step",
    "browser_task",
    "browser_action_log",
    "browser_interrupt",
    "browser_message",
    "browser_job",
    "browser_session",
)

JD_TABLES = (
    "jd_draft_jobs",
    "jd_skill_specs",
    "jd_systems",
    "jd_tasks",
    "job_descriptions",
)


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    for table in (*BROWSER_TABLES, *JD_TABLES):
        if table in existing:
            op.drop_table(table)


def downgrade() -> None:
    # Tables are not recreated here — restore from the original create migrations
    # if a rollback is required.
    pass
