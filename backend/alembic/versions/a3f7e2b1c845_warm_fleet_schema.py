"""warm fleet schema: agent_sessions, reply_outbox, turn_idempotency

Revision ID: a3f7e2b1c845
Revises: 7f42a0046e65
Create Date: 2026-07-14

Adds three tables required by the warm async fleet architecture:

  agent_sessions   — durable OCC-versioned session state (one row per agent × thread)
  reply_outbox     — transactional outbox co-committed with every state write
  turn_idempotency — per-event idempotency + audit record for crash-recovery

Design notes
------------
* agent_sessions uses fillfactor=70 so that Postgres can do HOT (Heap-Only Tuple)
  updates when version/state/updated_at change on every turn.  With ~2000 hot
  sessions each taking multiple turns, this avoids page splits and keeps the table
  lean.

* reply_outbox carries a partial index on (created_at) WHERE status = 'pending'.
  At scale the overwhelming majority of rows are 'delivered'; restricting the index
  to pending rows means the outbox sweeper query is always a tiny scan regardless of
  total table size.

* turn_idempotency intentionally has no FK to agent_sessions.  The idempotency
  record must survive session deletion so a the queue redeliver after cascade-delete
  is still recognised as already-processed and skipped.

* All timestamps are TIMESTAMPTZ (timezone-aware) so the sweeper TTL arithmetic
  works correctly across any deployment timezone.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a3f7e2b1c845'
down_revision: Union[str, None] = '7f42a0046e65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    if 'agent_sessions' in inspector.get_table_names():
        # Already created — either by a previous successful run of this
        # migration, or because the database was bootstrapped directly from
        # the current ORM models (see the baseline migration's fresh-schema
        # branch, which deliberately excludes these three tables so this
        # migration remains the one source of truth for creating them).
        return

    # ------------------------------------------------------------------
    # agent_sessions
    #
    # PK on session_id: all OCC reads/writes are
    #   SELECT state, version WHERE session_id = :id        (~2 ms)
    #   UPDATE … WHERE session_id = :id AND version = :orig (~2 ms)
    # Both are single PK b-tree lookups — no additional index needed for
    # the hot path.
    #
    # ix_agent_sessions_agent_id: supports agent-level admin queries
    # (list sessions for an agent, cascade validation, etc.).
    #
    # fillfactor=70: leaves 30 % of each heap page free so that the
    # frequent (session_id, version, state, updated_at) update stays
    # on the same page as a HOT chain rather than spilling to a new page.
    # ------------------------------------------------------------------
    op.create_table(
        'agent_sessions',
        sa.Column('session_id',     sa.Text(),    nullable=False),
        sa.Column('agent_id',       sa.Text(),    nullable=False),
        sa.Column('state',
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('version',        sa.Integer(), nullable=False, server_default=sa.text('0')),
        sa.Column('summary',        sa.Text(),    nullable=True),
        sa.Column('history_cursor', sa.Text(),    nullable=True),
        sa.Column('working_state',
                  postgresql.JSONB(astext_type=sa.Text()),
                  nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column('created_at',
                  sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('updated_at',
                  sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.text('NOW()')),
        sa.ForeignKeyConstraint(['agent_id'], ['agents.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id'),
    )
    # SQLAlchemy's postgresql dialect has no Table-level dialect kwarg for
    # arbitrary storage parameters (postgresql_with is Index-only) — set
    # fillfactor via a plain ALTER TABLE instead.
    op.execute("ALTER TABLE agent_sessions SET (fillfactor = 70)")
    op.create_index('ix_agent_sessions_agent_id', 'agent_sessions', ['agent_id'])

    # ------------------------------------------------------------------
    # reply_outbox
    #
    # uq_reply_outbox_event_id: the idempotency guarantee — INSERT fails
    # on duplicate event_id so a concurrent retry can never double-post.
    #
    # ck_reply_outbox_status: DB-enforced enum so invalid status values
    # are rejected at the storage layer, not only at the application layer.
    #
    # ix_reply_outbox_pending_created (PARTIAL, WHERE status = 'pending'):
    # The outbox sweeper runs:
    #   SELECT … WHERE status = 'pending' AND created_at < NOW() - interval
    # At 1000 users × 2 agents with fast turns, delivered rows accumulate
    # quickly.  A full index on (status, created_at) would bloat as the
    # table grows.  The partial index is physically small and stays fast
    # regardless of how many delivered rows exist.
    # ------------------------------------------------------------------
    op.create_table(
        'reply_outbox',
        sa.Column('id',           sa.Text(), nullable=False),
        sa.Column('event_id',     sa.Text(), nullable=False),
        sa.Column('session_id',   sa.Text(), nullable=False),
        sa.Column('payload',      sa.Text(), nullable=False),
        sa.Column('status',       sa.Text(), nullable=False, server_default=sa.text("'pending'")),
        sa.Column('slack_ts',     sa.Text(), nullable=True),
        sa.Column('created_at',
                  sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.text('NOW()')),
        sa.Column('delivered_at',
                  sa.DateTime(timezone=True),
                  nullable=True),
        sa.CheckConstraint("status IN ('pending', 'delivered')", name='ck_reply_outbox_status'),
        sa.ForeignKeyConstraint(
            ['session_id'], ['agent_sessions.session_id'], ondelete='CASCADE'
        ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_id', name='uq_reply_outbox_event_id'),
    )
    op.create_index(
        'ix_reply_outbox_pending_created',
        'reply_outbox',
        ['created_at'],
        postgresql_where=sa.text("status = 'pending'"),
    )

    # ------------------------------------------------------------------
    # turn_idempotency
    #
    # PK on event_id: the crash-recovery check is always
    #   SELECT 1 FROM turn_idempotency WHERE event_id = :id
    # which is a single PK b-tree lookup.
    #
    # ix_turn_idempotency_committed_at: supports the TTL cleanup job
    #   DELETE FROM turn_idempotency WHERE committed_at < NOW() - interval
    # Old records (beyond the max the queue ack deadline) are safe to purge.
    # ------------------------------------------------------------------
    op.create_table(
        'turn_idempotency',
        sa.Column('event_id',     sa.Text(), nullable=False),
        sa.Column('session_id',   sa.Text(), nullable=False),
        sa.Column('committed_at',
                  sa.DateTime(timezone=True),
                  nullable=False,
                  server_default=sa.text('NOW()')),
        sa.PrimaryKeyConstraint('event_id'),
    )
    op.create_index('ix_turn_idempotency_committed_at', 'turn_idempotency', ['committed_at'])


def downgrade() -> None:
    # Drop in reverse dependency order.
    op.drop_index('ix_turn_idempotency_committed_at', table_name='turn_idempotency')
    op.drop_table('turn_idempotency')

    op.drop_index('ix_reply_outbox_pending_created', table_name='reply_outbox')
    op.drop_table('reply_outbox')

    op.drop_index('ix_agent_sessions_agent_id', table_name='agent_sessions')
    op.drop_table('agent_sessions')
