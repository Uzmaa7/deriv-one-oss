"""Per-agent usage accounting for knowledge-base operations.

Mirrors ``token_tracking.py``: fire-and-forget, never raises, so a logging
failure cannot break a user-facing search or an ingest run.

Why this exists rather than reading a provider's billing export: retrieval services bill
queries and indexed storage at project level by SKU, and ``user_labels`` on a
request are not a documented billing-export dimension. Counting app-side and
multiplying by published unit pricing is the same approach
``AGENT_COST_CALCULATION.md`` already takes for LLM token cost.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Default unit pricing, overridable without a
# deploy so a price change does not require a code release. Defaults are USD.
PRICE_PER_1K_QUERIES = float(os.environ.get("KB_PRICE_PER_1K_QUERIES", "1.50"))
PRICE_PER_GB_MONTH = float(os.environ.get("KB_PRICE_PER_GB_MONTH", "0.05"))

EVENT_SEARCH = "search"
EVENT_IMPORT = "import"


def record_kb_usage(*, agent_id: str, event_type: str, unit_count: int = 1) -> None:
    """Persist one usage row. Never raises."""
    if not agent_id or unit_count <= 0:
        return
    try:
        from agents import KnowledgeBaseUsageLog
        from database import SessionLocal

        db = SessionLocal()
        try:
            db.add(
                KnowledgeBaseUsageLog(
                    agent_id=agent_id,
                    event_type=event_type,
                    unit_count=int(unit_count),
                )
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — usage accounting must never break the caller
        logger.warning("kb_usage: failed to record %s for agent %s", event_type, agent_id)


def estimated_query_cost_usd(query_count: int) -> float:
    """Estimated cost of *query_count* retrieval searches."""
    return (max(0, query_count) / 1000.0) * PRICE_PER_1K_QUERIES


def estimated_storage_cost_usd(total_bytes: int, days: int = 30) -> float:
    """Estimated indexed-storage cost for *total_bytes* held for *days*."""
    gb = max(0, total_bytes) / float(1024 ** 3)
    return gb * PRICE_PER_GB_MONTH * (max(0, days) / 30.0)


def usage_summary_for_agent(agent_id: str, since=None) -> dict:
    """Query and import counts for one agent, with an estimated query cost.

    Storage cost is deliberately not included here: bucket labels make per-agent
    storage cost readable directly from the Cloud Billing export, which is
    authoritative, unlike this counter-derived estimate.
    """
    try:
        from sqlalchemy import func

        from agents import KnowledgeBaseUsageLog
        from database import SessionLocal

        db = SessionLocal()
        try:
            q = db.query(
                KnowledgeBaseUsageLog.event_type,
                func.coalesce(func.sum(KnowledgeBaseUsageLog.unit_count), 0),
            ).filter(KnowledgeBaseUsageLog.agent_id == agent_id)
            if since is not None:
                q = q.filter(KnowledgeBaseUsageLog.created_at >= since)
            counts = {event: int(total) for event, total in q.group_by(
                KnowledgeBaseUsageLog.event_type
            ).all()}
        finally:
            db.close()
    except Exception:  # noqa: BLE001
        logger.warning("kb_usage: summary failed for agent %s", agent_id)
        return {"searches": 0, "imported_documents": 0, "estimated_query_cost_usd": 0.0}

    searches = counts.get(EVENT_SEARCH, 0)
    return {
        "searches": searches,
        "imported_documents": counts.get(EVENT_IMPORT, 0),
        "estimated_query_cost_usd": round(estimated_query_cost_usd(searches), 6),
    }
