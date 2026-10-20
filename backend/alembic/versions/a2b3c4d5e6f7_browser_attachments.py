"""files the sandbox browser downloaded, persisted to the blob store

A download used to die with the pod: Chrome wrote it to a disk nobody read, and
the sandbox has no credentials to push it anywhere itself. The backend
now sweeps the pod's pickup directory at the end of each turn, copies each file
into the generated-artifacts bucket and records it here, so the transcript can
offer it back as a download long after the sandbox is gone.

Only the object path is stored. Signed URLs are minted per request: one written
into the row would expire, and would keep working for anyone who could read the
transcript afterwards.

Revision ID: a2b3c4d5e6f7
Revises: z1a2b3c4d5e6
Create Date: 2026-08-25 15:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a2b3c4d5e6f7"
down_revision: Union[str, None] = "z1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "browser_attachment" in set(insp.get_table_names()):
        return

    op.create_table(
        "browser_attachment",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("owner_sub", sa.String(), nullable=False),
        sa.Column("session_id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=True),
        # Set once the turn's assistant message exists; a file swept at teardown
        # has no message to attach to.
        sa.Column("message_id", sa.String(), nullable=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=True),
        sa.Column("size_bytes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sha256", sa.String(), nullable=False),
        sa.Column("object_key", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        # Attachments belong to a session's transcript and go with it.
        sa.ForeignKeyConstraint(["session_id"], ["browser_session.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["job_id"], ["browser_job.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["message_id"], ["browser_message.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_browser_attachment_owner_sub", "browser_attachment", ["owner_sub"])
    op.create_index("ix_browser_attachment_session_id", "browser_attachment", ["session_id"])
    op.create_index("ix_browser_attachment_message_id", "browser_attachment", ["message_id"])
    # The de-dup guard: the same bytes downloaded twice in one session are one file.
    op.create_index(
        "ix_browser_attachment_session_sha",
        "browser_attachment",
        ["session_id", "sha256"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "browser_attachment" not in set(insp.get_table_names()):
        return
    op.drop_index("ix_browser_attachment_session_sha", table_name="browser_attachment")
    op.drop_index("ix_browser_attachment_message_id", table_name="browser_attachment")
    op.drop_index("ix_browser_attachment_session_id", table_name="browser_attachment")
    op.drop_index("ix_browser_attachment_owner_sub", table_name="browser_attachment")
    op.drop_table("browser_attachment")
