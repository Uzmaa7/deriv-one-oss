"""add user_events table

Revision ID: a1b2c3d4e5f6
Revises: 7f42a0046e65
Create Date: 2026-07-14 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "7f42a0046e65"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "user_events" in inspector.get_table_names():
        # Table may already exist from ORM create_all / a prior partial apply
        # while alembic_version was still at the baseline revision.
        return

    op.create_table(
        "user_events",
        sa.Column("id",         sa.String(),     nullable=False),
        sa.Column("created_at", sa.DateTime(),   nullable=False, server_default=sa.func.now()),
        sa.Column("owner_sub",  sa.String(),     nullable=False),
        sa.Column("email",      sa.String(320),  nullable=False, server_default=""),
        sa.Column("event",      sa.String(80),   nullable=False),
        sa.Column("properties", sa.Text(),       nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_events_created_at", "user_events", ["created_at"])
    op.create_index("ix_user_events_owner_sub",  "user_events", ["owner_sub"])
    op.create_index("ix_user_events_email",      "user_events", ["email"])
    op.create_index("ix_user_events_event",      "user_events", ["event"])


def downgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "user_events" not in inspector.get_table_names():
        return
    existing = {ix["name"] for ix in inspector.get_indexes("user_events")}
    for name in (
        "ix_user_events_event",
        "ix_user_events_email",
        "ix_user_events_owner_sub",
        "ix_user_events_created_at",
    ):
        if name in existing:
            op.drop_index(name, "user_events")
    op.drop_table("user_events")
