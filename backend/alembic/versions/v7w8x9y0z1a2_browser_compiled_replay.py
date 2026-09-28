"""deterministic replay for saved browser tasks

Adds the compiled-action columns a promoted task needs to replay without the
agent loop, plus the run-mode field that makes a silent degradation back to the
agentic path visible.

All columns are nullable with no server default: NULL means "not compiled", which
is exactly how every existing task already behaves, so this migration needs no
backfill and changes no current behaviour.

Revision ID: v7w8x9y0z1a2
Revises: u6v7w8x9y0z1
Create Date: 2026-08-24 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "v7w8x9y0z1a2"
down_revision: Union[str, None] = "u6v7w8x9y0z1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_task_step" in existing:
        step_cols = {c["name"] for c in insp.get_columns("browser_task_step")}
        if "compiled_json" not in step_cols:
            # JSON array of compiled actions. Text rather than JSON so sqlite (used
            # by the tests) and Postgres behave identically.
            op.add_column("browser_task_step", sa.Column("compiled_json", sa.Text(), nullable=True))
        if "compiled_at" not in step_cols:
            op.add_column("browser_task_step", sa.Column("compiled_at", sa.DateTime(), nullable=True))
        if "compiled_from_job_id" not in step_cols:
            # Provenance, not a FK: browser_job rows are transcript data and can be
            # pruned, and losing the job must not cascade away a working compilation.
            op.add_column(
                "browser_task_step", sa.Column("compiled_from_job_id", sa.String(), nullable=True)
            )

    if "browser_task" in existing:
        task_cols = {c["name"] for c in insp.get_columns("browser_task")}
        if "last_run_mode" not in task_cols:
            # agentic | compiled | compiled_with_fallback
            op.add_column("browser_task", sa.Column("last_run_mode", sa.String(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_task" in existing:
        task_cols = {c["name"] for c in insp.get_columns("browser_task")}
        if "last_run_mode" in task_cols:
            op.drop_column("browser_task", "last_run_mode")

    if "browser_task_step" in existing:
        step_cols = {c["name"] for c in insp.get_columns("browser_task_step")}
        for name in ("compiled_from_job_id", "compiled_at", "compiled_json"):
            if name in step_cols:
                op.drop_column("browser_task_step", name)
