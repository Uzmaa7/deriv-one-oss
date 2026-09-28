"""add skill_artifact_suggest_jobs.artifact_description

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-08-03 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "l6m7n8o9p0q1"
down_revision: Union[str, None] = "k5l6m7n8o9p0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _job_columns() -> set[str]:
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns("skill_artifact_suggest_jobs")}


def upgrade() -> None:
    if "artifact_description" not in _job_columns():
        op.add_column(
            "skill_artifact_suggest_jobs",
            sa.Column("artifact_description", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if "artifact_description" in _job_columns():
        op.drop_column("skill_artifact_suggest_jobs", "artifact_description")
