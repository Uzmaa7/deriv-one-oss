"""add agents.artifacts_enabled

Revision ID: k5l6m7n8o9p0
Revises: j4k5l6m7n8o9
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k5l6m7n8o9p0"
down_revision: Union[str, None] = "j4k5l6m7n8o9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _agent_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("agents")}


def upgrade() -> None:
    if "artifacts_enabled" not in _agent_columns():
        op.add_column(
            "agents",
            sa.Column(
                "artifacts_enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if "artifacts_enabled" in _agent_columns():
        op.drop_column("agents", "artifacts_enabled")
