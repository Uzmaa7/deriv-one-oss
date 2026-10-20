"""merge feedback and warm fleet heads

Revision ID: e6f7a8b9c0d1
Revises: d5e6f7a8b9c0, ce950c22ce5a
Create Date: 2026-07-23 15:29:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e6f7a8b9c0d1"
down_revision: Union[str, None] = ("d5e6f7a8b9c0", "ce950c22ce5a")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
