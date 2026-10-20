"""per-agent the retrieval provider knowledge bases

Revision ID: u6v7w8x9y0z1
Revises: t5u6v7w8x9y0
Create Date: 2026-08-24 12:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "u6v7w8x9y0z1"
down_revision: Union[str, None] = "t5u6v7w8x9y0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "agent_knowledge_bases" not in existing:
        op.create_table(
            "agent_knowledge_bases",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
            sa.Column("status", sa.String(length=20), nullable=False, server_default="disabled"),
            sa.Column("error_message", sa.Text(), nullable=True),
            # Both the platform's identifiers cap at 63 chars. Nullable because they are
            # only resolved once provisioning succeeds, and cleared on disable.
            sa.Column("storage_namespace", sa.String(length=63), nullable=True),
            sa.Column("data_store_id", sa.String(length=63), nullable=True),
            sa.Column(
                "data_store_location", sa.String(length=16), nullable=False, server_default="us"
            ),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("updated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            # At most one knowledge base per agent — the data store is the
            # isolation boundary, so a second one would be ambiguous at runtime.
            sa.UniqueConstraint("agent_id", name="uq_agent_knowledge_bases_agent"),
        )
        op.create_index(
            "ix_agent_knowledge_bases_agent_id", "agent_knowledge_bases", ["agent_id"]
        )
        op.create_index(
            "ix_agent_knowledge_bases_status", "agent_knowledge_bases", ["status"]
        )

    if "agent_knowledge_base_documents" not in existing:
        op.create_table(
            "agent_knowledge_base_documents",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("knowledge_base_id", sa.String(), nullable=False),
            sa.Column("uploaded_by_email", sa.String(length=320), nullable=False, server_default=""),
            sa.Column("original_filename", sa.String(length=512), nullable=False),
            sa.Column("object_path", sa.String(length=1024), nullable=False),
            sa.Column("content_hash", sa.String(length=64), nullable=False),
            sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("mime_type", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("index_document_id", sa.String(length=255), nullable=True),
            sa.Column("index_status", sa.String(length=20), nullable=False, server_default="pending"),
            sa.Column("index_error", sa.Text(), nullable=True),
            # Lease columns for the ingest worker (mirrors overnight_worker).
            sa.Column("lease_owner", sa.String(length=64), nullable=True),
            sa.Column("lease_expires_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("indexed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(
                ["knowledge_base_id"], ["agent_knowledge_bases.id"], ondelete="CASCADE"
            ),
            sa.PrimaryKeyConstraint("id"),
            # De-dup guard: identical bytes re-uploaded to the same knowledge
            # base would create a redundant the retrieval provider document and
            # dilute retrieval quality.
            sa.UniqueConstraint(
                "knowledge_base_id", "content_hash", name="uq_agent_kb_documents_hash"
            ),
        )
        op.create_index(
            "ix_agent_kb_documents_kb_id",
            "agent_knowledge_base_documents",
            ["knowledge_base_id"],
        )
        # The ingest worker's hot query: pending rows, oldest first.
        op.create_index(
            "ix_agent_kb_documents_status_created",
            "agent_knowledge_base_documents",
            ["index_status", "created_at"],
        )


    # Per-agent cost attribution for the retrieval provider. Separate from
    # token_usage_logs because those columns are token-shaped; this counts
    # queries and imported documents. See spec §9 for why the platform's billing export
    # cannot supply this per agent.
    if "kb_usage_logs" not in existing:
        op.create_table(
            "kb_usage_logs",
            sa.Column("id", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.Column("agent_id", sa.String(), nullable=False),
            sa.Column("event_type", sa.String(length=20), nullable=False),
            sa.Column("unit_count", sa.Integer(), nullable=False, server_default="1"),
            sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_kb_usage_logs_created_at", "kb_usage_logs", ["created_at"])
        op.create_index("ix_kb_usage_logs_agent_id", "kb_usage_logs", ["agent_id"])
        op.create_index("ix_kb_usage_logs_event_type", "kb_usage_logs", ["event_type"])


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    if "kb_usage_logs" in existing:
        op.drop_table("kb_usage_logs")
    if "agent_knowledge_base_documents" in existing:
        op.drop_table("agent_knowledge_base_documents")
    if "agent_knowledge_bases" in existing:
        op.drop_table("agent_knowledge_bases")
