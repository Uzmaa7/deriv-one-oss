"""agent_memories retention indexes

Revision ID: g1h2i3j4k5l6
Revises: a9b8c7d6e5f4
Create Date: 2026-07-28 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "g1h2i3j4k5l6"
down_revision: Union[str, None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _index_names(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return {ix["name"] for ix in insp.get_indexes(table)}


def upgrade() -> None:
    names = _index_names("agent_memories")
    if "ix_agent_memories_agent_scope_updated" not in names:
        op.create_index(
            "ix_agent_memories_agent_scope_updated",
            "agent_memories",
            ["agent_id", "scope", "updated_at"],
            unique=False,
        )
    if "ix_agent_memories_expires_at" not in names:
        op.create_index(
            "ix_agent_memories_expires_at",
            "agent_memories",
            ["expires_at"],
            unique=False,
        )


def downgrade() -> None:
    names = _index_names("agent_memories")
    if "ix_agent_memories_expires_at" in names:
        op.drop_index("ix_agent_memories_expires_at", table_name="agent_memories")
    if "ix_agent_memories_agent_scope_updated" in names:
        op.drop_index(
            "ix_agent_memories_agent_scope_updated",
            table_name="agent_memories",
        )
