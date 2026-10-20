"""Scheduled agent memory retention (expiry + quota enforcement)."""

from __future__ import annotations

import logging

from database import SessionLocal
from memory_store.maintenance import memory_maintenance_metrics_snapshot, run_agent_memory_maintenance

logger = logging.getLogger(__name__)


def run_agent_memory_maintenance_job() -> None:
    """Entry point for APScheduler — safe to call from scheduler_worker only."""
    db = SessionLocal()
    try:
        result = run_agent_memory_maintenance(db)
        metrics = memory_maintenance_metrics_snapshot(db)
        logger.info(
            "agent_memory_maintenance_job: result=%s metrics=%s",
            {
                "expired_deleted": result.expired_deleted,
                "quota_trimmed": result.quota_trimmed,
                "agents_touched": result.agents_touched,
            },
            metrics,
        )
    except Exception:
        logger.exception("agent_memory_maintenance_job failed")
    finally:
        db.close()
