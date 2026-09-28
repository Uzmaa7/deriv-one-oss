"""add feedback assignee and priority

Revision ID: d5e6f7a8b9c0
Revises: c4d5e6f7a8b9
Create Date: 2026-07-15 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5e6f7a8b9c0"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # Columns may already exist from an earlier create_all / partial deploy
    # even when alembic_version is still on c4d5e6f7a8b9.
    if not _has_column("feedback", "assignee"):
        op.add_column("feedback", sa.Column("assignee", sa.String(), nullable=True))
    if not _has_column("feedback", "priority"):
        op.add_column("feedback", sa.Column("priority", sa.String(), nullable=True))


def downgrade() -> None:
    if _has_column("feedback", "priority"):
        op.drop_column("feedback", "priority")
    if _has_column("feedback", "assignee"):
        op.drop_column("feedback", "assignee")
