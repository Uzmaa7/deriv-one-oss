"""artifact plans and recommendations

Revision ID: i3j4k5l6m7n8
Revises: h2i3j4k5l6m7
Create Date: 2026-07-29 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i3j4k5l6m7n8"
down_revision: Union[str, None] = "h2i3j4k5l6m7"
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

    if "skill_artifact_plans" not in names:
        op.create_table(
            "skill_artifact_plans",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("status_detail", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_index_if_missing("ix_skill_artifact_plans_slug", "skill_artifact_plans", ["slug"])
        _create_index_if_missing(
            "ix_skill_artifact_plans_owner_sub", "skill_artifact_plans", ["owner_sub"]
        )
        _create_index_if_missing("ix_skill_artifact_plans_status", "skill_artifact_plans", ["status"])

    if "skill_artifact_recommendations" not in names:
        op.create_table(
            "skill_artifact_recommendations",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("plan_id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=40), nullable=False),
            sa.Column("description", sa.Text(), nullable=False),
            sa.Column("effect_class", sa.String(length=16), nullable=False),
            sa.Column("sort_order", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("suggest_job_id", sa.String(), nullable=True),
            sa.Column("published_artifact_id", sa.String(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )
        _create_index_if_missing(
            "ix_skill_artifact_recommendations_plan_id",
            "skill_artifact_recommendations",
            ["plan_id"],
        )
        _create_index_if_missing(
            "ix_skill_artifact_recommendations_slug",
            "skill_artifact_recommendations",
            ["slug"],
        )
        _create_index_if_missing(
            "ix_skill_artifact_recommendations_status",
            "skill_artifact_recommendations",
            ["status"],
        )

    if "skill_artifact_suggest_jobs" in names:
        insp = sa.inspect(op.get_bind())
        cols = {c["name"] for c in insp.get_columns("skill_artifact_suggest_jobs")}
        if "recommendation_id" not in cols:
            op.add_column("skill_artifact_suggest_jobs", sa.Column("recommendation_id", sa.String(), nullable=True))
            _create_index_if_missing(
                "ix_skill_artifact_suggest_jobs_recommendation_id",
                "skill_artifact_suggest_jobs",
                ["recommendation_id"],
            )


def downgrade() -> None:
    names = _table_names()
    if "skill_artifact_suggest_jobs" in names:
        insp = sa.inspect(op.get_bind())
        cols = {c["name"] for c in insp.get_columns("skill_artifact_suggest_jobs")}
        if "recommendation_id" in cols:
            ix = _index_names("skill_artifact_suggest_jobs")
            if "ix_skill_artifact_suggest_jobs_recommendation_id" in ix:
                op.drop_index(
                    "ix_skill_artifact_suggest_jobs_recommendation_id",
                    table_name="skill_artifact_suggest_jobs",
                )
            op.drop_column("skill_artifact_suggest_jobs", "recommendation_id")
    if "skill_artifact_recommendations" in names:
        op.drop_table("skill_artifact_recommendations")
    if "skill_artifact_plans" in names:
        op.drop_table("skill_artifact_plans")
