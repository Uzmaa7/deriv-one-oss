"""add role blueprint (JD builder) tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-07-17 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if "job_descriptions" in inspector.get_table_names():
        # Already present from ORM bootstrap / prior partial apply.
        return

    op.create_table(
        "job_descriptions",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("team_level1", sa.String(200), nullable=False),
        sa.Column("team_level2", sa.String(200), nullable=False),
        sa.Column("title", sa.String(300), nullable=False),
        sa.Column("role_slug", sa.String(120), nullable=False),
        sa.Column("level", sa.String(80), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("role_identity", sa.Text(), nullable=True),
        sa.Column("evidence_basis", sa.Text(), nullable=True),
        sa.Column("headcount", sa.Integer(), nullable=True),
        sa.Column("coverage_pct", sa.Integer(), nullable=True),
        sa.Column("not_in_scope", sa.Text(), nullable=True),
        sa.Column("source", sa.String(20), nullable=False, server_default="manual"),
        sa.Column("created_by", sa.String(320), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_job_descriptions_team_level1", "job_descriptions", ["team_level1"])
    op.create_index("ix_job_descriptions_team_level2", "job_descriptions", ["team_level2"])
    op.create_index("ix_job_descriptions_role_slug", "job_descriptions", ["role_slug"])
    op.create_index("ix_job_descriptions_status", "job_descriptions", ["status"])
    op.create_index("ix_job_descriptions_owner_sub", "job_descriptions", ["owner_sub"])

    op.create_table(
        "jd_tasks",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("jd_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(500), nullable=False),
        sa.Column("frequency", sa.String(80), nullable=True),
        sa.Column("time_cost", sa.String(80), nullable=True),
        sa.Column("pain_rating", sa.String(10), nullable=True),
        sa.Column("work_type", sa.String(20), nullable=True),
        sa.Column("automatable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["jd_id"], ["job_descriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jd_tasks_jd_id", "jd_tasks", ["jd_id"])

    op.create_table(
        "jd_systems",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("jd_id", sa.String(), nullable=False),
        sa.Column("system_name", sa.String(200), nullable=False),
        sa.Column("task_cluster", sa.String(300), nullable=True),
        sa.Column("api_tier", sa.String(10), nullable=True),
        sa.Column("auth_model", sa.String(200), nullable=True),
        sa.Column("rw_scope", sa.String(80), nullable=True),
        sa.Column("human_only", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["jd_id"], ["job_descriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jd_systems_jd_id", "jd_systems", ["jd_id"])

    op.create_table(
        "jd_skill_specs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("jd_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("problem_statement", sa.Text(), nullable=True),
        sa.Column("autonomy_level", sa.String(20), nullable=True),
        sa.Column("capabilities", sa.JSON(), nullable=True),
        sa.Column("integrations", sa.JSON(), nullable=True),
        sa.Column("guardrails", sa.Text(), nullable=True),
        sa.Column("success_metrics", sa.Text(), nullable=True),
        sa.Column("linked_skill_slug", sa.String(120), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="spec"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.ForeignKeyConstraint(["jd_id"], ["job_descriptions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jd_skill_specs_jd_id", "jd_skill_specs", ["jd_id"])

    op.create_table(
        "jd_draft_jobs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("team_level1", sa.String(200), nullable=False),
        sa.Column("team_level2", sa.String(200), nullable=False),
        sa.Column("lookback_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("status_detail", sa.String(500), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_jd_draft_jobs_owner_sub", "jd_draft_jobs", ["owner_sub"])
    op.create_index("ix_jd_draft_jobs_status", "jd_draft_jobs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_jd_draft_jobs_status", "jd_draft_jobs")
    op.drop_index("ix_jd_draft_jobs_owner_sub", "jd_draft_jobs")
    op.drop_table("jd_draft_jobs")

    op.drop_index("ix_jd_skill_specs_jd_id", "jd_skill_specs")
    op.drop_table("jd_skill_specs")

    op.drop_index("ix_jd_systems_jd_id", "jd_systems")
    op.drop_table("jd_systems")

    op.drop_index("ix_jd_tasks_jd_id", "jd_tasks")
    op.drop_table("jd_tasks")

    op.drop_index("ix_job_descriptions_owner_sub", "job_descriptions")
    op.drop_index("ix_job_descriptions_status", "job_descriptions")
    op.drop_index("ix_job_descriptions_role_slug", "job_descriptions")
    op.drop_index("ix_job_descriptions_team_level2", "job_descriptions")
    op.drop_index("ix_job_descriptions_team_level1", "job_descriptions")
    op.drop_table("job_descriptions")
