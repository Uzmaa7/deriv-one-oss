"""add agent skill routing index and thread skill hints

Revision ID: m7n8o9p0q1r2
Revises: e6f7a8b9c0d1
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m7n8o9p0q1r2"
down_revision: Union[str, None] = "l6m7n8o9p0q1"
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
    if _has_table("agents"):
        if not _has_column("agents", "skill_routing_enabled"):
            op.add_column(
                "agents",
                sa.Column(
                    "skill_routing_enabled",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.false(),
                ),
            )
        if not _has_column("agents", "skill_index_text"):
            op.add_column("agents", sa.Column("skill_index_text", sa.Text(), nullable=True))
        if not _has_column("agents", "skill_index_generated_at"):
            op.add_column(
                "agents",
                sa.Column("skill_index_generated_at", sa.DateTime(), nullable=True),
            )
        if not _has_column("agents", "skill_index_source_skill_ids"):
            op.add_column(
                "agents",
                sa.Column("skill_index_source_skill_ids", sa.Text(), nullable=True),
            )

    if _has_table("agent_interaction_logs") and not _has_column(
        "agent_interaction_logs", "routing_metadata"
    ):
        op.add_column(
            "agent_interaction_logs",
            sa.Column("routing_metadata", sa.Text(), nullable=True),
        )

    if not _has_table("agent_thread_skill_routing"):
        op.create_table(
            "agent_thread_skill_routing",
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("agent_id", sa.String(length=36), nullable=False),
            sa.Column("channel_id", sa.String(length=80), nullable=False),
            sa.Column("thread_ts", sa.String(length=40), nullable=False),
            sa.Column("selected_slugs", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            "ix_agent_thread_skill_routing_lookup",
            "agent_thread_skill_routing",
            ["agent_id", "channel_id", "thread_ts"],
            unique=True,
        )


def downgrade() -> None:
    if _has_table("agent_thread_skill_routing"):
        op.drop_index("ix_agent_thread_skill_routing_lookup", table_name="agent_thread_skill_routing")
        op.drop_table("agent_thread_skill_routing")

    if _has_table("agent_interaction_logs") and _has_column(
        "agent_interaction_logs", "routing_metadata"
    ):
        op.drop_column("agent_interaction_logs", "routing_metadata")

    if _has_table("agents"):
        for col in (
            "skill_index_source_skill_ids",
            "skill_index_generated_at",
            "skill_index_text",
            "skill_routing_enabled",
        ):
            if _has_column("agents", col):
                op.drop_column("agents", col)
