"""merge staging/qa alembic heads and add slack_ephemeral_replies

Revision ID: p1q2r3s4t5u6
Revises: g1h2i3j4k5l6, 5902a4d5b2d8
Create Date: 2026-08-17 00:00:00.000000

qa shipped slack_ephemeral_replies as e6f7a8b9c0d1. Staging already used that
revision id for the warm-fleet merge, so the table is applied here instead.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "p1q2r3s4t5u6"
down_revision: Union[str, None] = ("g1h2i3j4k5l6", "5902a4d5b2d8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_table(table: str) -> bool:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    return table in insp.get_table_names()


def upgrade() -> None:
    if _has_table("slack_ephemeral_replies"):
        return
    op.create_table(
        "slack_ephemeral_replies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("agent_id", sa.String(length=36), nullable=False),
        sa.Column("channel_id", sa.String(length=80), nullable=False),
        sa.Column("thread_ts", sa.String(length=40), nullable=False),
        sa.Column("slack_user_id", sa.String(length=40), nullable=False),
        sa.Column("reply_text", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_slack_ephemeral_replies_agent_id",
        "slack_ephemeral_replies",
        ["agent_id"],
    )
    op.create_index(
        "ix_slack_ephemeral_replies_channel_id",
        "slack_ephemeral_replies",
        ["channel_id"],
    )
    op.create_index(
        "ix_slack_ephemeral_replies_slack_user_id",
        "slack_ephemeral_replies",
        ["slack_user_id"],
    )
    op.create_index(
        "ix_slack_ephemeral_replies_created_at",
        "slack_ephemeral_replies",
        ["created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_slack_ephemeral_replies_created_at", table_name="slack_ephemeral_replies")
    op.drop_index("ix_slack_ephemeral_replies_slack_user_id", table_name="slack_ephemeral_replies")
    op.drop_index("ix_slack_ephemeral_replies_channel_id", table_name="slack_ephemeral_replies")
    op.drop_index("ix_slack_ephemeral_replies_agent_id", table_name="slack_ephemeral_replies")
    op.drop_table("slack_ephemeral_replies")
