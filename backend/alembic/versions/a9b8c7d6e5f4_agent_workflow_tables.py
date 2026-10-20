"""agent workflow tables

Revision ID: a9b8c7d6e5f4
Revises: f8a9b0c1d2e3
Create Date: 2026-07-27 00:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "workflow_definitions" not in insp.get_table_names():
        op.create_table(
            "workflow_definitions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(length=120), nullable=False),
            sa.Column("name", sa.String(length=200), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("graph_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("published_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_workflow_definitions_agent_id", "workflow_definitions", ["agent_id"])

    if "workflow_runs" not in insp.get_table_names():
        op.create_table(
            "workflow_runs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("definition_id", sa.String(), nullable=False),
            sa.Column("definition_version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("current_step", sa.String(length=80), nullable=False),
            sa.Column("context_json", sa.Text(), nullable=False),
            sa.Column("pending_gate_json", sa.Text(), nullable=True),
            sa.Column("started_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.Column("ended_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"]),
            sa.ForeignKeyConstraint(["definition_id"], ["workflow_definitions.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_workflow_runs_agent_id", "workflow_runs", ["agent_id"])
        op.create_index("ix_workflow_runs_status", "workflow_runs", ["status"])

    if "workflow_run_events" not in insp.get_table_names():
        op.create_table(
            "workflow_run_events",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("step_id", sa.String(length=80), nullable=True),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_workflow_run_events_run_id", "workflow_run_events", ["run_id"])

    if "workflow_wait_bindings" not in insp.get_table_names():
        op.create_table(
            "workflow_wait_bindings",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("run_id", sa.String(), nullable=False),
            sa.Column("wait_id", sa.String(), nullable=False),
            sa.Column("kind", sa.String(length=32), nullable=False),
            sa.Column("channel_id", sa.String(length=32), nullable=True),
            sa.Column("thread_ts", sa.String(length=32), nullable=True),
            sa.Column("expected_senders_json", sa.Text(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["run_id"], ["workflow_runs.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_workflow_wait_bindings_channel_id", "workflow_wait_bindings", ["channel_id"])
        op.create_index("ix_workflow_wait_bindings_thread_ts", "workflow_wait_bindings", ["thread_ts"])


def downgrade() -> None:
    op.drop_table("workflow_wait_bindings")
    op.drop_table("workflow_run_events")
    op.drop_table("workflow_runs")
    op.drop_table("workflow_definitions")
