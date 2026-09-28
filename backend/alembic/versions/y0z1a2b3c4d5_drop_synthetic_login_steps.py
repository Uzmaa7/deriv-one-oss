"""drop synthetic login steps from saved browser tasks

Saved tasks used to get a generated first step reading "Log in using the stored
credentials 'LOGIN_..._USERNAME' and 'LOGIN_..._PASSWORD'." (or the password-only
variant). Replaying it never worked: the runner boots Chrome on about:blank and
nothing navigates to the target host, so the step arrived with no page, no URL
and no field guidance, and the agent hunted for login fields that were not there
until the run stalled. The step could not self-heal either — compilation matches
steps to turns by prompt text and this prompt had no user message behind it, so
it stayed agentic on every run.

The generator is gone (browser_config.credential_plan is storage-only now) and
the login instruction rides along with every real step via build_user_turn. This
migration removes the rows already persisted and closes the gap in seq so each
task starts at 0 again with the user's own first instruction.

Matched on the exact generated shapes only, anchored at seq 0 — a step a human
typed that happens to mention logging in is left alone.

Revision ID: y0z1a2b3c4d5
Revises: x9y0z1a2b3c4
Create Date: 2026-08-25 10:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "y0z1a2b3c4d5"
down_revision: Union[str, None] = "x9y0z1a2b3c4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# Both generated variants. LIKE rather than equality because the slugs embedded
# in them are derived from each task's target host.
_GENERATED_PROMPTS = (
    "Log in using the stored credentials '%' and '%'.",
    "Log in. The password is in the stored credential '%' — click the password field%",
)


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "browser_task_step" not in set(insp.get_table_names()):
        return

    step = sa.table(
        "browser_task_step",
        sa.column("id", sa.String),
        sa.column("task_id", sa.String),
        sa.column("seq", sa.Integer),
        sa.column("prompt", sa.Text),
    )

    stale = bind.execute(
        sa.select(step.c.id, step.c.task_id).where(
            step.c.seq == 0,
            sa.or_(*[step.c.prompt.like(pattern) for pattern in _GENERATED_PROMPTS]),
        )
    ).fetchall()
    if not stale:
        return

    bind.execute(sa.delete(step).where(step.c.id.in_([row.id for row in stale])))

    # Close the hole left at seq 0. Done per task rather than in one statement
    # because the tasks that had a login step are a subset of all tasks, and
    # decrementing the rest would corrupt them.
    for task_id in {row.task_id for row in stale}:
        bind.execute(
            sa.update(step).where(step.c.task_id == task_id).values(seq=step.c.seq - 1)
        )


def downgrade() -> None:
    # The removed steps were generated, never authored, and the generator no
    # longer exists — there is nothing faithful to restore.
    pass
