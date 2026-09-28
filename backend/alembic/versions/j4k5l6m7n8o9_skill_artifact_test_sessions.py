"""skill artifact test sessions

Revision ID: j4k5l6m7n8o9
Revises: i3j4k5l6m7n8
Create Date: 2026-07-30 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j4k5l6m7n8o9"
down_revision: Union[str, None] = "i3j4k5l6m7n8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    return set(sa.inspect(op.get_bind()).get_table_names())


def _index_names(table: str) -> set[str]:
    insp = sa.inspect(op.get_bind())
    if table not in insp.get_table_names():
        return set()
    return {ix["name"] for ix in insp.get_indexes(table)}


def _create_index_if_missing(name: str, table: str, columns: list[str]) -> None:
    if name not in _index_names(table):
        op.create_index(name, table, columns, unique=False)


def upgrade() -> None:
    names = _table_names()

    if "skill_artifact_test_sessions" not in names:
        op.create_table(
            "skill_artifact_test_sessions",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("job_id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("params_json", sa.Text(), nullable=False),
            sa.Column("auto_fix", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("attempts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("final_code", sa.Text(), nullable=True),
            sa.Column("final_manifest_json", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_index_if_missing(
            "ix_skill_artifact_test_sessions_job_id",
            "skill_artifact_test_sessions",
            ["job_id"],
        )
        _create_index_if_missing(
            "ix_skill_artifact_test_sessions_slug",
            "skill_artifact_test_sessions",
            ["slug"],
        )
        _create_index_if_missing(
            "ix_skill_artifact_test_sessions_owner_sub",
            "skill_artifact_test_sessions",
            ["owner_sub"],
        )
        _create_index_if_missing(
            "ix_skill_artifact_test_sessions_status",
            "skill_artifact_test_sessions",
            ["status"],
        )


def downgrade() -> None:
    names = _table_names()
    if "skill_artifact_test_sessions" in names:
        op.drop_table("skill_artifact_test_sessions")
