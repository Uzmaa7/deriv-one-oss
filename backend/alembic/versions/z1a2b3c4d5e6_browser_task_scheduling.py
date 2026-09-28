"""scheduling, run history and approval gates for saved browser tasks

Saved tasks could only be run by hand, from a page that was doing the driving.
Three additions change that:

  * a cron on browser_task, so the scheduler worker can fire a task with nobody
    watching. Kept separate from schedule_active so pausing a schedule does not
    throw away the expression;
  * browser_task_run, one row per execution. browser_task.last_run_* only ever
    described the latest attempt, which made "how often does this need a human?"
    unanswerable. Those columns stay as a denormalised summary of the newest run;
  * requires_approval on browser_task_step — a policy gate that stops a run
    before the step, distinct from needed_human, which is an advisory note about
    what happened when the task was captured.

owner_email is captured because a scheduled run has no request to read an
identity from, and nothing else in the schema maps a JWT sub to an address.

Revision ID: z1a2b3c4d5e6
Revises: y0z1a2b3c4d5
Create Date: 2026-08-25 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "y0z1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TASK_COLUMNS = (
    ("owner_email", lambda: sa.Column("owner_email", sa.String(), nullable=True)),
    ("schedule", lambda: sa.Column("schedule", sa.String(length=120), nullable=True)),
    (
        "timezone",
        lambda: sa.Column(
            "timezone", sa.String(length=64), nullable=False, server_default="Asia/Dubai"
        ),
    ),
    (
        "schedule_active",
        lambda: sa.Column(
            "schedule_active", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    ),
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_task" in existing:
        task_cols = {c["name"] for c in insp.get_columns("browser_task")}
        for name, build in _TASK_COLUMNS:
            if name not in task_cols:
                op.add_column("browser_task", build())

    if "browser_task_step" in existing:
        step_cols = {c["name"] for c in insp.get_columns("browser_task_step")}
        if "requires_approval" not in step_cols:
            op.add_column(
                "browser_task_step",
                sa.Column(
                    "requires_approval", sa.Boolean(), nullable=False, server_default=sa.false()
                ),
            )

    if "browser_task_run" not in existing:
        op.create_table(
            "browser_task_run",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("task_id", sa.String(), nullable=False),
            sa.Column("trigger", sa.String(), nullable=False, server_default="manual"),
            # Null for a run that never claimed a sandbox.
            sa.Column("session_id", sa.String(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="running"),
            sa.Column("mode", sa.String(), nullable=True),
            sa.Column("steps_total", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("steps_completed", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("current_seq", sa.Integer(), nullable=True),
            sa.Column("interrupt_id", sa.String(), nullable=True),
            sa.Column("result", sa.Text(), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("finished_at", sa.DateTime(), nullable=True),
            sa.Column("duration_secs", sa.Float(), nullable=True),
            # Run history describes a task and means nothing without it.
            sa.ForeignKeyConstraint(["task_id"], ["browser_task.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_browser_task_run_owner_sub", "browser_task_run", ["owner_sub"])
        # The history list is always "this task, newest first".
        op.create_index(
            "ix_browser_task_run_task_started",
            "browser_task_run",
            ["task_id", "started_at"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_task_run" in existing:
        op.drop_index("ix_browser_task_run_task_started", table_name="browser_task_run")
        op.drop_index("ix_browser_task_run_owner_sub", table_name="browser_task_run")
        op.drop_table("browser_task_run")

    if "browser_task_step" in existing:
        step_cols = {c["name"] for c in insp.get_columns("browser_task_step")}
        if "requires_approval" in step_cols:
            op.drop_column("browser_task_step", "requires_approval")

    if "browser_task" in existing:
        task_cols = {c["name"] for c in insp.get_columns("browser_task")}
        for name, _build in reversed(_TASK_COLUMNS):
            if name in task_cols:
                op.drop_column("browser_task", name)
