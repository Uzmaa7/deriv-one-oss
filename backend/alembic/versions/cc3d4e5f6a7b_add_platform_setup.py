"""Add platform_setup table for first-run wizard."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "cc3d4e5f6a7b"
down_revision = "bb2c3d4e5f6a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "platform_setup",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("completed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("deployment_mode", sa.String(length=32), nullable=True),
        sa.Column("draft_json", sa.JSON(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("platform_setup")
