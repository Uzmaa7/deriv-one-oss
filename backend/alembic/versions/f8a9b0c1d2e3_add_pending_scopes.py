"""add pending_scopes to agents

Revision ID: f8a9b0c1d2e3
Revises: e6f7a8b9c0d1
Create Date: 2026-07-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a9b0c1d2e3"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return column in {c["name"] for c in insp.get_columns(table)}


def upgrade() -> None:
    # Column may already exist from agents.ensure_schema / a prior partial deploy
    # even when alembic_version has not yet recorded this revision.
    if not _has_column("agents", "pending_scopes"):
        op.add_column("agents", sa.Column("pending_scopes", sa.Text(), nullable=True))


def downgrade() -> None:
    if _has_column("agents", "pending_scopes"):
        op.drop_column("agents", "pending_scopes")
