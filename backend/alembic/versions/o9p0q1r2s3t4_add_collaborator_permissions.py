"""add collaborator role and permission columns

Revision ID: o9p0q1r2s3t4
Revises: n8o9p0q1r2s3
Create Date: 2026-08-12 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "o9p0q1r2s3t4"
down_revision: Union[str, None] = "n8o9p0q1r2s3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    return table in sa.inspect(bind).get_table_names()


def _columns(table: str) -> set[str]:
    if not _has_table(table):
        return set()
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _add_collaborator_columns(table: str) -> None:
    if not _has_table(table):
        return
    cols = _columns(table)
    if "slack_user_id" not in cols:
        op.add_column(table, sa.Column("slack_user_id", sa.String(40), nullable=True))
    if "role" not in cols:
        op.add_column(
            table,
            sa.Column("role", sa.String(32), nullable=False, server_default="can_share"),
        )
    if "can_invoke" not in cols:
        op.add_column(
            table,
            sa.Column("can_invoke", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "can_edit_config" not in cols:
        op.add_column(
            table,
            sa.Column("can_edit_config", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "can_share" not in cols:
        op.add_column(
            table,
            sa.Column("can_share", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    if "can_view_logs" not in cols:
        op.add_column(
            table,
            sa.Column("can_view_logs", sa.Boolean(), nullable=False, server_default=sa.true()),
        )


def upgrade() -> None:
    _add_collaborator_columns("agent_collaborators")
    _add_collaborator_columns("skill_collaborators")


def downgrade() -> None:
    for table in ("agent_collaborators", "skill_collaborators"):
        if not _has_table(table):
            continue
        cols = _columns(table)
        for col in (
            "can_view_logs",
            "can_share",
            "can_edit_config",
            "can_invoke",
            "role",
            "slack_user_id",
        ):
            if col in cols:
                op.drop_column(table, col)
