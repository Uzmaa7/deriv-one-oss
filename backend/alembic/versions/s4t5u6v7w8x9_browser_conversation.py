"""browser conversation transcript and agent interrupts

Revision ID: s4t5u6v7w8x9
Revises: r3s4t5u6v7w8
Create Date: 2026-08-22 05:40:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "s4t5u6v7w8x9"
down_revision: Union[str, None] = "r3s4t5u6v7w8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_message" not in existing:
        op.create_table(
            "browser_message",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=True),
            sa.Column("role", sa.String(), nullable=False),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("tools_json", sa.Text(), nullable=True),
            sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["session_id"], ["browser_session.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_id"], ["browser_job.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_browser_message_owner_sub", "browser_message", ["owner_sub"])
        op.create_index(
            "ix_browser_message_session_seq", "browser_message", ["session_id", "seq"]
        )

    if "browser_interrupt" not in existing:
        op.create_table(
            "browser_interrupt",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("session_id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=True),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("tool_name", sa.String(), nullable=True),
            sa.Column("args_json", sa.Text(), nullable=True),
            sa.Column("reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(), nullable=False, server_default="pending"),
            sa.Column("resume_state", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("resolved_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["session_id"], ["browser_session.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["job_id"], ["browser_job.id"], ondelete="SET NULL"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_browser_interrupt_owner_sub", "browser_interrupt", ["owner_sub"])
        op.create_index("ix_browser_interrupt_session_id", "browser_interrupt", ["session_id"])

        # A session can only be blocked on one human decision at a time.
        if bind.dialect.name == "postgresql":
            op.execute(
                sa.text(
                    "CREATE UNIQUE INDEX uq_browser_interrupt_pending_session "
                    "ON browser_interrupt (session_id) "
                    "WHERE status = 'pending'"
                )
            )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP INDEX IF EXISTS uq_browser_interrupt_pending_session"))
    op.drop_table("browser_interrupt")
    op.drop_table("browser_message")
