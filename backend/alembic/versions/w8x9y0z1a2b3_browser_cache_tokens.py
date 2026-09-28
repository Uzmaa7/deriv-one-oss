"""prompt-cache token counters for browser_message

Adds cache_read_tokens/cache_creation_tokens to browser_message so the
Computer page's Usage tab can show how much of a session's token usage was
served from Anthropic's prompt cache, matching input_tokens/output_tokens
which already exist on this table. Both columns default to 0 (same
semantics as input_tokens/output_tokens) so existing rows read as "no cache
activity recorded", which is accurate for messages persisted before cache
token tracking existed.

Revision ID: w8x9y0z1a2b3
Revises: v7w8x9y0z1a2
Create Date: 2026-08-24 16:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "w8x9y0z1a2b3"
down_revision: Union[str, None] = "v7w8x9y0z1a2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_message" in existing:
        cols = {c["name"] for c in insp.get_columns("browser_message")}
        if "cache_read_tokens" not in cols:
            op.add_column(
                "browser_message",
                sa.Column("cache_read_tokens", sa.Integer(), nullable=False, server_default="0"),
            )
        if "cache_creation_tokens" not in cols:
            op.add_column(
                "browser_message",
                sa.Column("cache_creation_tokens", sa.Integer(), nullable=False, server_default="0"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_message" in existing:
        cols = {c["name"] for c in insp.get_columns("browser_message")}
        for name in ("cache_creation_tokens", "cache_read_tokens"):
            if name in cols:
                op.drop_column("browser_message", name)
