"""add failure fields to agent_interaction_logs

Revision ID: 5902a4d5b2d8
Revises: o9p0q1r2s3t4
Create Date: 2026-08-07 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5902a4d5b2d8"
down_revision: Union[str, None] = "o9p0q1r2s3t4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if table not in insp.get_table_names():
        return False
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    if not _has_table("agent_interaction_logs"):
        return
    if not _has_column("agent_interaction_logs", "failure_kind"):
        op.add_column(
            "agent_interaction_logs",
            sa.Column("failure_kind", sa.String(length=32), nullable=True),
        )
        op.create_index(
            "ix_agent_interaction_logs_failure_kind",
            "agent_interaction_logs",
            ["failure_kind"],
            unique=False,
        )
    if not _has_column("agent_interaction_logs", "failure_detail"):
        op.add_column(
            "agent_interaction_logs",
            sa.Column("failure_detail", sa.Text(), nullable=True),
        )
    if not _has_column("agent_interaction_logs", "resolved_model"):
        op.add_column(
            "agent_interaction_logs",
            sa.Column("resolved_model", sa.String(length=120), nullable=True),
        )


def downgrade() -> None:
    if not _has_table("agent_interaction_logs"):
        return
    if _has_column("agent_interaction_logs", "resolved_model"):
        op.drop_column("agent_interaction_logs", "resolved_model")
    if _has_column("agent_interaction_logs", "failure_detail"):
        op.drop_column("agent_interaction_logs", "failure_detail")
    if _has_column("agent_interaction_logs", "failure_kind"):
        op.drop_index("ix_agent_interaction_logs_failure_kind", table_name="agent_interaction_logs")
        op.drop_column("agent_interaction_logs", "failure_kind")
