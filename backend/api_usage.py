"""
Admin endpoint: GET /api/admin/usage?days=30

Returns aggregated LLM token usage with breakdowns by source, agent, model,
and a daily time series. Cost is calculated per-model using each model's
actual pricing so the totals are accurate when multiple models are in use.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Request

from cf_access import build_auth_context
from database import SessionLocal, TokenUsageLog
from agents import Agent
from model_pricing import cost_for_model, pricing_for_model, refresh_pricing_cache

logger = logging.getLogger(__name__)

router = APIRouter()


def _row_cost(r) -> float:
    """Cost for a single TokenUsageLog row using its model's pricing."""
    return cost_for_model(
        r.model or "",
        r.input_tokens or 0,
        r.output_tokens or 0,
        r.cache_read_tokens or 0,
        r.cache_creation_tokens or 0,
    )


def _row_cache_savings(r) -> float:
    """$ saved by this row's cache reads vs. paying full input price for them.

    A cache read is billed at ``cache_read_per_m`` instead of the model's
    full ``input_per_m`` rate — the delta, applied to ``cache_read_tokens``,
    is what caching actually saved on this call. Cache *writes*
    (``cache_creation_tokens``) cost slightly more than a normal input token
    and are intentionally excluded here — they're the (small) up-front cost
    that makes later reads cheap, not a saving.
    """
    p = pricing_for_model(r.model or "")
    cr = r.cache_read_tokens or 0
    return cr / 1_000_000 * max(0.0, p.input_per_m - p.cache_read_per_m)


def _agg(rows) -> dict:
    inp  = sum(r.input_tokens          or 0 for r in rows)
    out  = sum(r.output_tokens         or 0 for r in rows)
    cr   = sum(r.cache_read_tokens     or 0 for r in rows)
    cc   = sum(r.cache_creation_tokens or 0 for r in rows)
    cost = sum(_row_cost(r) for r in rows)
    savings = sum(_row_cache_savings(r) for r in rows)
    return {
        "input_tokens":          inp,
        "output_tokens":         out,
        "cache_read_tokens":     cr,
        "cache_creation_tokens": cc,
        "total_tokens":          inp + out,
        "cost_usd":              round(cost, 4),
        "cache_savings_usd":     round(savings, 4),
    }


_SOURCE_LABELS = {
    "chat":               "Hub Chat",
    "slack":              "Slack Agents",
    "automation":         "Automations",
    "lab":                "Lab Builder",
    "a2a":                "Agent-to-Agent",
    "skill_create":       "Skill Create",
    "skill_publish":      "Skill Publish",
    "skill_ai_edit":      "Skill AI Edit",
    "memory":             "Memory Mining",
    "memory_consolidation": "Memory Consolidation",
}


@router.get("/api/admin/usage")
async def get_usage(request: Request, days: int = 30) -> dict[str, Any]:
    ctx = build_auth_context(request.headers)
    if not ctx.is_admin:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin access required.")

    days = max(1, min(days, 365))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)

    # Warm the model gateway pricing cache once up front so every `_row_cost` call
    # below (across source/model/agent/user/day breakdowns) reads live rates
    # instead of racing a per-row network fetch.
    await refresh_pricing_cache()

    with SessionLocal() as db:
        rows = (
            db.query(TokenUsageLog)
            .filter(TokenUsageLog.created_at >= cutoff)
            .all()
        )

        # ── Total ─────────────────────────────────────────────────────────────
        total = _agg(rows)
        total["calls"] = len(rows)

        # ── By source ─────────────────────────────────────────────────────────
        source_map: dict[str, list] = {}
        for r in rows:
            source_map.setdefault(r.source, []).append(r)
        by_source = {
            src: {
                "label": _SOURCE_LABELS.get(src, src),
                "calls": len(lst),
                **_agg(lst),
            }
            for src, lst in sorted(source_map.items())
        }

        # ── By model ──────────────────────────────────────────────────────────
        model_map: dict[str, list] = {}
        for r in rows:
            model_map.setdefault(r.model, []).append(r)
        by_model = {
            mdl: {"calls": len(lst), **_agg(lst)}
            for mdl, lst in sorted(model_map.items())
        }

        # ── By agent ──────────────────────────────────────────────────────────
        agent_map: dict[str, list] = {}
        for r in rows:
            if r.agent_id:
                agent_map.setdefault(r.agent_id, []).append(r)

        agent_names: dict[str, str] = {}
        if agent_map:
            agent_rows = db.query(Agent.id, Agent.name).filter(
                Agent.id.in_(list(agent_map.keys()))
            ).all()
            agent_names = {a.id: a.name for a in agent_rows}

        by_agent = sorted(
            [
                {
                    "agent_id":   aid,
                    "agent_name": agent_names.get(aid, aid[:8]),
                    "calls":      len(lst),
                    **_agg(lst),
                }
                for aid, lst in agent_map.items()
            ],
            key=lambda x: x["cost_usd"],
            reverse=True,
        )

        # ── By user ───────────────────────────────────────────────────────────
        user_map: dict[str, list] = {}
        for r in rows:
            email = (r.user_email or "").strip().lower()
            if not email:
                email = "(system)"
            user_map.setdefault(email, []).append(r)

        by_user = sorted(
            [
                {
                    "user_email": email,
                    "calls":      len(lst),
                    **_agg(lst),
                }
                for email, lst in user_map.items()
            ],
            key=lambda x: x["cost_usd"],
            reverse=True,
        )

        # ── Daily time series ─────────────────────────────────────────────────
        day_map: dict[str, list] = {}
        for r in rows:
            day = r.created_at.strftime("%Y-%m-%d")
            day_map.setdefault(day, []).append(r)

        # Fill in every day in the range (even days with zero calls)
        by_day = []
        for i in range(days):
            d = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
            lst = day_map.get(d, [])
            by_day.append({"date": d, "calls": len(lst), **_agg(lst)})

    return {
        "days":     days,
        "total":    total,
        "by_source": by_source,
        "by_model":  by_model,
        "by_agent":  by_agent,
        "by_user":   by_user,
        "by_day":    by_day,
    }


def usage_for_agent(db, agent_id: str, days: int) -> dict[str, Any]:
    """Aggregate token usage for a single agent. Caller must authorize."""
    days = max(1, min(days, 365))
    cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)
    rows = (
        db.query(TokenUsageLog)
        .filter(
            TokenUsageLog.created_at >= cutoff,
            TokenUsageLog.agent_id == agent_id,
        )
        .all()
    )

    total = _agg(rows)
    total["calls"] = len(rows)

    source_map: dict[str, list] = {}
    for r in rows:
        source_map.setdefault(r.source, []).append(r)
    by_source = {
        src: {
            "label": _SOURCE_LABELS.get(src, src),
            "calls": len(lst),
            **_agg(lst),
        }
        for src, lst in sorted(source_map.items())
    }

    model_map: dict[str, list] = {}
    for r in rows:
        model_map.setdefault(r.model, []).append(r)
    by_model = {
        mdl: {"calls": len(lst), **_agg(lst)}
        for mdl, lst in sorted(model_map.items())
    }

    day_map: dict[str, list] = {}
    for r in rows:
        day = r.created_at.strftime("%Y-%m-%d")
        day_map.setdefault(day, []).append(r)

    by_day = []
    for i in range(days):
        d = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        lst = day_map.get(d, [])
        by_day.append({"date": d, "calls": len(lst), **_agg(lst)})

    return {
        "days": days,
        "agent_id": agent_id,
        "total": total,
        "by_source": by_source,
        "by_model": by_model,
        "by_day": by_day,
    }
