"""drop self-hosted-agent migration_sessions table

Revision ID: bb2c3d4e5f6a
Revises: aa1b2c3d4e5f
Create Date: 2026-09-09

"""
from typing import Sequence, Union

from alembic import op
from sqlalchemy import inspect

revision: str = "bb2c3d4e5f6a"
down_revision: Union[str, None] = "aa1b2c3d4e5f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    existing = set(inspect(bind).get_table_names())
    if "migration_sessions" in existing:
        op.drop_table("migration_sessions")


def downgrade() -> None:
    pass
