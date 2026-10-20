"""browser session tables for agent computer use

Revision ID: r3s4t5u6v7w8
Revises: q2r3s4t5u6v7
Create Date: 2026-08-22 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "r3s4t5u6v7w8"
down_revision: Union[str, None] = "q2r3s4t5u6v7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "browser_session" in insp.get_table_names():
        return

    op.create_table(
        "browser_session",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("chat_id", sa.String(), nullable=True),
        sa.Column("target_host", sa.String(), nullable=False),
        sa.Column("mode", sa.String(), nullable=False, server_default="read_only"),
        sa.Column("state", sa.String(), nullable=False, server_default="provisioning"),
        sa.Column("control_state", sa.String(), nullable=False, server_default="user_driving"),
        sa.Column("sandbox_name", sa.String(), nullable=True),
        sa.Column("sandbox_claim", sa.String(), nullable=True),
        sa.Column("lease_token", sa.String(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
        sa.Column("current_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("ready_at", sa.DateTime(), nullable=True),
        sa.Column("expires_at", sa.DateTime(), nullable=True),
        sa.Column("last_heartbeat_at", sa.DateTime(), nullable=True),
        sa.Column("terminated_at", sa.DateTime(), nullable=True),
        sa.Column("termination_reason", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browser_session_owner_sub", "browser_session", ["owner_sub"])

    op.create_table(
        "browser_job",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("task", sa.Text(), nullable=False),
        sa.Column("state", sa.String(), nullable=False, server_default="queued"),
        sa.Column("step_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("input_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("output_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column("error_class", sa.String(), nullable=True),
        sa.Column("pending_approval", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["session_id"], ["browser_session.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browser_job_owner_sub", "browser_job", ["owner_sub"])
    op.create_index("ix_browser_job_session_id", "browser_job", ["session_id"])

    op.create_table(
        "browser_action_log",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        sa.Column("step_index", sa.Integer(), nullable=True),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("selector", sa.Text(), nullable=True),
        sa.Column("screenshot_ref", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["browser_session.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["browser_job.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browser_action_log_owner_sub", "browser_action_log", ["owner_sub"])
    op.create_index("ix_browser_action_log_session_id", "browser_action_log", ["session_id"])

    if bind.dialect.name == "postgresql":
        op.execute(
            sa.text(
                "CREATE UNIQUE INDEX uq_browser_session_live_owner "
                "ON browser_session (owner_sub) "
                "WHERE state <> 'terminated'"
            )
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute(sa.text("DROP INDEX IF EXISTS uq_browser_session_live_owner"))
    op.drop_table("browser_action_log")
    op.drop_table("browser_job")
    op.drop_table("browser_session")
