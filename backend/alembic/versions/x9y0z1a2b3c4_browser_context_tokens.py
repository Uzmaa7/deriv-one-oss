"""context_tokens counter for browser_message

Adds context_tokens to browser_message: the total prompt size (input +
cache_read + cache_creation) of the single API call that produced a turn's
final response, distinct from the existing input_tokens/output_tokens/
cache_read_tokens/cache_creation_tokens which sum every step within a turn.
Powers the Computer page's Cursor-style context-usage widget. Defaults to 0
(same semantics as the other token columns) so existing rows read as "not
recorded", which is accurate for messages persisted before this existed.

Revision ID: x9y0z1a2b3c4
Revises: w8x9y0z1a2b3
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "x9y0z1a2b3c4"
down_revision: Union[str, None] = "w8x9y0z1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_message" in existing:
        cols = {c["name"] for c in insp.get_columns("browser_message")}
        if "context_tokens" not in cols:
            op.add_column(
                "browser_message",
                sa.Column("context_tokens", sa.Integer(), nullable=False, server_default="0"),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    if "browser_message" in existing:
        cols = {c["name"] for c in insp.get_columns("browser_message")}
        if "context_tokens" in cols:
            op.drop_column("browser_message", "context_tokens")
