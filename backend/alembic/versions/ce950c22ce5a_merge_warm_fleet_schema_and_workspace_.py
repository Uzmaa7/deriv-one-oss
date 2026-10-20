"""merge warm fleet schema and workspace resource proposals heads

Revision ID: ce950c22ce5a
Revises: a3f7e2b1c845, c3d4e5f6a7b8
Create Date: 2026-07-22 15:37:42.247893

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'ce950c22ce5a'
down_revision: Union[str, None] = ('a3f7e2b1c845', 'c3d4e5f6a7b8')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
