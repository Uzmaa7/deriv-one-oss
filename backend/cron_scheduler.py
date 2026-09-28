"""Gate for APScheduler cron registration.

Only the dedicated ``scheduler_worker`` process should register and fire
recurring jobs (automations, agent scheduled tasks, memory mining).  API pods
set ``ENABLE_CRON_SCHEDULER=false`` so horizontally scaled replicas do not
duplicate cron executions.
"""

from __future__ import annotations

import os


def cron_scheduler_enabled() -> bool:
    return os.environ.get("ENABLE_CRON_SCHEDULER", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
