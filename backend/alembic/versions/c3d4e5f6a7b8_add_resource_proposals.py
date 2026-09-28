"""add resource proposals table (in-chat propose/approve for Skills/Automations/Labs)

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-20 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "resource_proposals" in inspector.get_table_names():
        # Already present from ORM bootstrap / prior partial apply.
        return

    op.create_table(
        "resource_proposals",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("name", sa.String(300), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("created_resource_id", sa.String(), nullable=True),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["chat_id"], ["chats.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_resource_proposals_chat_id", "resource_proposals", ["chat_id"])
    op.create_index("ix_resource_proposals_status", "resource_proposals", ["status"])
    op.create_index("ix_resource_proposals_owner_sub", "resource_proposals", ["owner_sub"])


def downgrade() -> None:
    op.drop_index("ix_resource_proposals_owner_sub", "resource_proposals")
    op.drop_index("ix_resource_proposals_status", "resource_proposals")
    op.drop_index("ix_resource_proposals_chat_id", "resource_proposals")
    op.drop_table("resource_proposals")
