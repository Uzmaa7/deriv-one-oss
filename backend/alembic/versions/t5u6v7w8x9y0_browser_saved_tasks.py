"""saved re-runnable browser tasks

Revision ID: t5u6v7w8x9y0
Revises: s4t5u6v7w8x9
Create Date: 2026-08-22 09:10:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "t5u6v7w8x9y0"
down_revision: Union[str, None] = "s4t5u6v7w8x9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_task" not in existing:
        op.create_table(
            "browser_task",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=120), nullable=False),
            sa.Column("target_host", sa.String(), nullable=False),
            sa.Column("mode", sa.String(), nullable=False, server_default="read_only"),
            sa.Column("source_session_id", sa.String(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("last_run_at", sa.DateTime(), nullable=True),
            sa.Column("last_run_status", sa.String(), nullable=True),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            # A saved task outlives the session it was captured from, so this
            # nulls out rather than cascading like the transcript tables.
            sa.ForeignKeyConstraint(
                ["source_session_id"], ["browser_session.id"], ondelete="SET NULL"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_browser_task_owner_sub", "browser_task", ["owner_sub"])
        # Names are how the user picks a task, so they must be unambiguous per
        # user. A plain unique index, so no dialect guard is needed.
        op.create_index(
            "uq_browser_task_owner_name", "browser_task", ["owner_sub", "name"], unique=True
        )

    if "browser_task_step" not in existing:
        op.create_table(
            "browser_task_step",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("prompt", sa.Text(), nullable=False),
            sa.Column("needed_human", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.ForeignKeyConstraint(["task_id"], ["browser_task.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_browser_task_step_owner_sub", "browser_task_step", ["owner_sub"])
        op.create_index(
            "ix_browser_task_step_task_seq", "browser_task_step", ["task_id", "seq"]
        )

    # Attributes a turn to the saved task being replayed, so the run outcome can
    # be recorded even for the synthetic resume-after-takeover job. Intentionally
    # not a foreign key — it is audit metadata, not a join.
    if "browser_job" in existing:
        job_cols = {c["name"] for c in insp.get_columns("browser_job")}
        if "task_id" not in job_cols:
            op.add_column("browser_job", sa.Column("task_id", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "browser_job" in set(insp.get_table_names()):
        job_cols = {c["name"] for c in insp.get_columns("browser_job")}
        if "task_id" in job_cols:
            op.drop_column("browser_job", "task_id")
    op.drop_table("browser_task_step")
    op.drop_table("browser_task")
