"""owner-scoped flows on the workflow tables

Adds `owner_sub` and `kind` to `workflow_definitions` / `workflow_runs` so a
second, owner-scoped kind of graph ("flow", api_flows.py) can live alongside
the original agent-scoped, Slack-triggered graphs ("playbook", api_workflows.py)
without either query surface seeing the other's rows. `agent_id` on both tables
becomes nullable — a flow has no owning agent.

Purely additive: every existing row is backfilled to kind="playbook" with
owner_sub=OWNER_LEGACY_SENTINEL, and agent_id NOT NULL -> NULL is a widening
change that admits every value already stored.

Revision ID: z1a2b3c4d5e6
Revises: y0z1a2b3c4d5
Create Date: 2026-08-25 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "z1a2b3c4d5e6"
down_revision: Union[str, None] = "y0z1a2b3c4d5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OWNER_LEGACY_SENTINEL = "__LEGACY_UNASSIGNED__"


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "workflow_definitions" in existing:
        cols = {c["name"] for c in insp.get_columns("workflow_definitions")}
        if "owner_sub" not in cols:
            op.add_column("workflow_definitions", sa.Column("owner_sub", sa.String(), nullable=True))
            op.execute(
                sa.text(
                    "UPDATE workflow_definitions SET owner_sub = :s WHERE owner_sub IS NULL"
                ).bindparams(s=_OWNER_LEGACY_SENTINEL)
            )
            op.alter_column("workflow_definitions", "owner_sub", nullable=False)
            op.create_index(
                "ix_workflow_definitions_owner_sub", "workflow_definitions", ["owner_sub"]
            )
        if "kind" not in cols:
            op.add_column(
                "workflow_definitions",
                sa.Column("kind", sa.String(length=16), nullable=True),
            )
            op.execute("UPDATE workflow_definitions SET kind = 'playbook' WHERE kind IS NULL")
            op.alter_column("workflow_definitions", "kind", nullable=False)
        op.alter_column("workflow_definitions", "agent_id", existing_type=sa.String(), nullable=True)

    if "workflow_runs" in existing:
        cols = {c["name"] for c in insp.get_columns("workflow_runs")}
        if "owner_sub" not in cols:
            op.add_column("workflow_runs", sa.Column("owner_sub", sa.String(), nullable=True))
            op.execute(
                sa.text(
                    "UPDATE workflow_runs SET owner_sub = :s WHERE owner_sub IS NULL"
                ).bindparams(s=_OWNER_LEGACY_SENTINEL)
            )
            op.alter_column("workflow_runs", "owner_sub", nullable=False)
            op.create_index("ix_workflow_runs_owner_sub", "workflow_runs", ["owner_sub"])
        op.alter_column("workflow_runs", "agent_id", existing_type=sa.String(), nullable=True)


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "workflow_runs" in existing:
        cols = {c["name"] for c in insp.get_columns("workflow_runs")}
        if "owner_sub" in cols:
            op.drop_index("ix_workflow_runs_owner_sub", table_name="workflow_runs")
            op.drop_column("workflow_runs", "owner_sub")

    if "workflow_definitions" in existing:
        cols = {c["name"] for c in insp.get_columns("workflow_definitions")}
        if "kind" in cols:
            op.drop_column("workflow_definitions", "kind")
        if "owner_sub" in cols:
            op.drop_index("ix_workflow_definitions_owner_sub", table_name="workflow_definitions")
            op.drop_column("workflow_definitions", "owner_sub")
