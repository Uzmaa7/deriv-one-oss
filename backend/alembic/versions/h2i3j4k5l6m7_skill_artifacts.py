"""add skill artifact and tool code sample tables

Revision ID: h2i3j4k5l6m7
Revises: e6f7a8b9c0d1
Create Date: 2026-07-29 00:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h2i3j4k5l6m7"
down_revision: Union[str, None] = "e6f7a8b9c0d1"
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

    if "skill_artifacts" not in names:
        op.create_table(
            "skill_artifacts",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("name", sa.String(length=40), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("code", sa.Text(), nullable=False),
            sa.Column("code_sha256", sa.String(length=64), nullable=False),
            sa.Column("manifest_json", sa.Text(), nullable=False),
            sa.Column("entrypoint", sa.String(length=80), nullable=False),
            sa.Column("params_schema_json", sa.Text(), nullable=True),
            sa.Column("declared_hosts_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("declared_secrets_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("effect_class", sa.String(length=16), nullable=False),
            sa.Column("api_summary", sa.Text(), nullable=True),
            sa.Column("gate_status", sa.String(length=16), nullable=False),
            sa.Column("gate_report_json", sa.Text(), nullable=True),
            sa.Column("ai_review_status", sa.String(length=16), nullable=False),
            sa.Column("ai_review_reason", sa.Text(), nullable=True),
            sa.Column("status", sa.String(length=24), nullable=False),
            sa.Column("author_kind", sa.String(length=16), nullable=False, server_default="human"),
            sa.Column("uploader_sub", sa.String(), nullable=False),
            sa.Column("uploader_email", sa.String(length=320), nullable=True),
            sa.Column("approved_by_sub", sa.String(), nullable=True),
            sa.Column("approved_by_email", sa.String(length=320), nullable=True),
            sa.Column("approved_at", sa.DateTime(), nullable=True),
            sa.Column("run_count", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_ok_at", sa.DateTime(), nullable=True),
            sa.Column("last_error_at", sa.DateTime(), nullable=True),
            sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("slug", "name", "version", name="uq_skill_artifact_version"),
        )

    if "skill_artifact_preflights" not in names:
        op.create_table(
            "skill_artifact_preflights",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("manifest_hash", sa.String(length=64), nullable=False),
            sa.Column("gate_status", sa.String(length=16), nullable=False),
            sa.Column("gate_report_json", sa.Text(), nullable=True),
            sa.Column("ai_review_status", sa.String(length=16), nullable=False),
            sa.Column("ai_review_reason", sa.Text(), nullable=True),
            sa.Column("proposed_code", sa.Text(), nullable=False),
            sa.Column("proposed_manifest_json", sa.Text(), nullable=False),
            sa.Column("reviewed_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "tool_code_samples" not in names:
        op.create_table(
            "tool_code_samples",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("skill_slugs_json", sa.Text(), nullable=False, server_default="[]"),
            sa.Column("tool", sa.String(length=32), nullable=False, server_default="run_python"),
            sa.Column("code_redacted", sa.Text(), nullable=False),
            sa.Column("code_sha256", sa.String(length=64), nullable=False),
            sa.Column("shape_sha256", sa.String(length=64), nullable=True),
            sa.Column("code_len", sa.Integer(), nullable=False),
            sa.Column("ok", sa.Boolean(), nullable=False, server_default=sa.true()),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("expires_at", sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
        )

    if "skill_artifact_suggest_jobs" not in names:
        op.create_table(
            "skill_artifact_suggest_jobs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("slug", sa.String(), nullable=False),
            sa.Column("owner_sub", sa.String(), nullable=False),
            sa.Column("artifact_name_hint", sa.String(length=40), nullable=True),
            sa.Column("status", sa.String(length=16), nullable=False),
            sa.Column("status_detail", sa.Text(), nullable=True),
            sa.Column("proposed_code", sa.Text(), nullable=True),
            sa.Column("proposed_manifest_json", sa.Text(), nullable=True),
            sa.Column("gate_report_json", sa.Text(), nullable=True),
            sa.Column("verification_note", sa.Text(), nullable=True),
            sa.Column("error_message", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("completed_at", sa.DateTime(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # Idempotent indexes (covers tables created earlier via init_artifact_tables()).
    _create_index_if_missing("ix_skill_artifacts_slug", "skill_artifacts", ["slug"])
    _create_index_if_missing("ix_skill_artifacts_status", "skill_artifacts", ["status"])
    _create_index_if_missing(
        "ix_skill_artifact_preflights_slug",
        "skill_artifact_preflights",
        ["slug"],
    )
    _create_index_if_missing(
        "ix_skill_artifact_preflights_owner_sub",
        "skill_artifact_preflights",
        ["owner_sub"],
    )
    _create_index_if_missing("ix_tool_code_samples_agent_id", "tool_code_samples", ["agent_id"])
    _create_index_if_missing("ix_tool_code_samples_created_at", "tool_code_samples", ["created_at"])
    _create_index_if_missing("ix_tool_code_samples_expires_at", "tool_code_samples", ["expires_at"])
    _create_index_if_missing(
        "ix_tool_code_samples_agent_shape_created",
        "tool_code_samples",
        ["agent_id", "shape_sha256", "created_at"],
    )
    _create_index_if_missing(
        "ix_skill_artifact_suggest_jobs_slug",
        "skill_artifact_suggest_jobs",
        ["slug"],
    )
    _create_index_if_missing(
        "ix_skill_artifact_suggest_jobs_owner_sub",
        "skill_artifact_suggest_jobs",
        ["owner_sub"],
    )
    _create_index_if_missing(
        "ix_skill_artifact_suggest_jobs_status",
        "skill_artifact_suggest_jobs",
        ["status"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    for table in (
        "skill_artifact_suggest_jobs",
        "tool_code_samples",
        "skill_artifact_preflights",
        "skill_artifacts",
    ):
        if table in insp.get_table_names():
            op.drop_table(table)
