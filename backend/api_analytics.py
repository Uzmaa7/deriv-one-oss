"""
Admin analytics endpoint.

GET /api/admin/analytics?period=daily|weekly|monthly&days=30|90|365

All period metrics use UTC calendar windows: [start_of_day - (days-1), start_of_tomorrow).
For example, days=1 is today 00:00 UTC through now (inclusive of partial today).

Aggregates across all existing tables plus the new user_events table to
produce feature usage, user journey funnels, daily/weekly/monthly time series,
and friction signals.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from cf_access import build_auth_context
from database import (
    SessionLocal,
    Chat,
    Message,
    Automation,
    AutomationRun,
    Memory,
    MemorySyncLog,
    TokenUsageLog,
    UserEvent,
    UserSecret,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(_UTC).replace(tzinfo=None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _bucket(dt: datetime, period: str) -> str:
    if period == "weekly":
        monday = dt - timedelta(days=dt.weekday())
        return monday.strftime("%Y-%m-%d")
    if period == "monthly":
        return dt.strftime("%Y-%m")
    return dt.strftime("%Y-%m-%d")


def _fill_buckets(period: str, days: int) -> list[str]:
    buckets: list[str] = []
    seen: set[str] = set()
    for i in range(days):
        d = _now() - timedelta(days=days - 1 - i)
        b = _bucket(d, period)
        if b not in seen:
            seen.add(b)
            buckets.append(b)
    return buckets


def _delta_pct(current: int | float, previous: int | float) -> float | None:
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 1)


_SKILL_EVENTS = frozenset({
    "workspace.message_sent",
    "workspace.skill_selected",
    "workspace.chat_started",
})

# Agent @mentions in Slack count toward user activity; A2A/DM/scheduled are agent-only.
_USER_FACING_AGENT_TRIGGER = "mention"
_SKILL_USAGE_EVENT = "workspace.message_sent"


def _workspace_message_skill_id(properties: str) -> str | None:
    """Skill slug from a workspace.message_sent user_event properties JSON."""
    try:
        sid = json.loads(properties or "{}").get("skill_id")
        if sid and str(sid).lower() not in ("none", "null", ""):
            return str(sid)
    except Exception:
        pass
    return None


def _event_skill_id(event: str, properties: str) -> str | None:
    if event not in _SKILL_EVENTS:
        return None
    try:
        sid = json.loads(properties or "{}").get("skill_id")
        if sid and sid != "none":
            return str(sid)
    except Exception:
        pass
    return None


def _product_activity_scores(
    events: list[tuple[datetime, str]],
    period_start: datetime,
    period_end: datetime,
) -> dict[str, int]:
    """Per-email product activity volume in [period_start, period_end)."""
    scores: dict[str, int] = defaultdict(int)
    for created_at, email in events:
        if _in_utc_window(created_at, period_start, period_end):
            scores[email] += 1
    return scores


def _collect_active_users(
    chats: list,
    autos: list,
    agents: list,
    apps: list,
    msgs: list,
) -> set[str]:
    """
    Count distinct dashboard users by owner_sub.
    Sources: workspace chats started, automations created, agents created,
    lab apps created, and workspace messages sent (role=user).
    """
    subs: set[str] = set()
    for r in chats:
        if r.owner_sub:
            subs.add(r.owner_sub)
    for r in autos:
        if r.owner_sub:
            subs.add(r.owner_sub)
    for r in agents:
        if r.owner_sub:
            subs.add(r.owner_sub)
    for r in apps:
        if r.owner_sub:
            subs.add(r.owner_sub)
    for r in msgs:
        if r.owner_sub:
            subs.add(r.owner_sub)
    return subs


def _start_of_utc_day(ref: datetime | None = None) -> datetime:
    dt = ref or _now()
    return dt.replace(hour=0, minute=0, second=0, microsecond=0)


def _utc_calendar_window(window_days: int) -> tuple[datetime, datetime]:
    """Return [start, end) for *window_days* UTC calendar days ending today (inclusive)."""
    start = _start_of_utc_day() - timedelta(days=window_days - 1)
    end = _start_of_utc_day() + timedelta(days=1)
    return start, end


def _previous_utc_calendar_window(window_days: int) -> tuple[datetime, datetime]:
    """The immediately preceding period of the same length (for delta %)."""
    cur_start, _ = _utc_calendar_window(window_days)
    prev_end = cur_start
    prev_start = cur_start - timedelta(days=window_days)
    return prev_start, prev_end


def _in_utc_window(dt: datetime, start: datetime, end: datetime) -> bool:
    return start <= dt < end


def _rows_in_range(rows: list, start: datetime, end: datetime, attr: str = "created_at") -> list:
    return [r for r in rows if _in_utc_window(getattr(r, attr), start, end)]


def _distinct_emails_in_window(
    events: list[tuple[datetime, str]],
    start: datetime,
    end: datetime,
) -> set[str]:
    return {
        email
        for created_at, email in events
        if _in_utc_window(created_at, start, end)
    }


def _day_over_day_activity_delta(events: list[tuple[datetime, str]]) -> float | None:
    """Today vs yesterday (UTC calendar days) % change in distinct active emails."""
    today_start = _start_of_utc_day()
    tomorrow_start = today_start + timedelta(days=1)
    yesterday_start = today_start - timedelta(days=1)
    today_count = len(_distinct_emails_in_window(events, today_start, tomorrow_start))
    yesterday_count = len(_distinct_emails_in_window(events, yesterday_start, today_start))
    return _delta_pct(today_count, yesterday_count)



def _run_owner_subs(run_rows: list) -> set[str]:
    return {r.owner_sub for r in run_rows if r.owner_sub}


def _milestone_targets_for_total(total: int) -> tuple[int, int, int]:
    """Three decade markers ending at the largest multiple of 10 ≤ total.

    Examples: total 40 → (20, 30, 40); total 70 → (50, 60, 70).
    Below 30, keep the initial (10, 20, 30) rail until the 30th is reached.
    """
    if total < 30:
        return (10, 20, 30)
    end = (total // 10) * 10
    return (end - 20, end - 10, end)


def _next_milestone(total: int) -> int | None:
    """Next upcoming decade milestone (e.g. 50 when total is 43, 190 when total is 186)."""
    if total < 10:
        return 10
    return ((total // 10) + 1) * 10


def _pick_milestones(
    ranked: list[tuple[Any, ...]],
    targets: tuple[int, ...],
    reached_fields: dict[str, Any],
) -> list[dict[str, Any]]:
    """Build milestone entries for rank targets (e.g. 20th, 30th, 40th)."""
    out: list[dict[str, Any]] = []
    for n in targets:
        idx = n - 1
        if idx < len(ranked):
            out.append({"n": n, "reached": True, **reached_fields(ranked[idx], n)})
        else:
            out.append({"n": n, "reached": False})
    return out


def _slug_display_name(slug: str) -> str:
    return slug.replace("-", " ").title()


def _merge_first_activity(
    first_at: dict[str, datetime],
    emails: dict[str, str],
    owner_sub: str,
    at: datetime,
    email: str = "",
) -> None:
    if not owner_sub:
        return
    if owner_sub not in first_at or at < first_at[owner_sub]:
        first_at[owner_sub] = at
    if email:
        emails[owner_sub] = email


def _compute_user_growth_milestones(
    db: Any,
    employee_by_email: dict[str, dict[str, str]],
) -> dict[str, Any]:
    """All-time user milestones ordered by first product activity."""
    first_at: dict[str, datetime] = {}
    emails: dict[str, str] = {}

    for owner_sub, created_at in db.query(Chat.owner_sub, Chat.created_at).all():
        _merge_first_activity(first_at, emails, owner_sub, created_at)

    for owner_sub, email, created_at in db.query(
        UserEvent.owner_sub, UserEvent.email, UserEvent.created_at
    ).all():
        _merge_first_activity(first_at, emails, owner_sub, created_at, email or "")

    for created_at, owner_sub in (
        db.query(Message.created_at, Chat.owner_sub)
        .join(Chat, Message.chat_id == Chat.id)
        .filter(Message.role == "user")
        .all()
    ):
        _merge_first_activity(first_at, emails, owner_sub, created_at)

    ranked = sorted(first_at.items(), key=lambda x: x[1])
    total = len(ranked)

    def _user_fields(entry: tuple[str, datetime], _n: int) -> dict[str, Any]:
        sub, at = entry
        email = emails.get(sub, "")
        emp = employee_by_email.get(email.lower(), {}) if email else {}
        name = emp.get("name") or (email.split("@")[0] if email else sub[:12])
        return {
            "email": email,
            "name": name,
            "department": emp.get("department", ""),
            "at": at.isoformat(),
        }

    latest = None
    if ranked:
        sub, at = ranked[-1]
        fields = _user_fields((sub, at), total)
        latest = {"n": total, **fields}

    targets = _milestone_targets_for_total(total)
    return {
        "total": total,
        "next_milestone": _next_milestone(total),
        "milestones": _pick_milestones(ranked, targets, _user_fields),
        "latest": latest,
    }


def _compute_agent_growth_milestones(db: Any) -> dict[str, Any]:
    """All-time agent milestones ordered by registration (created_at)."""
    ranked: list[tuple[str, str, datetime]] = []
    try:
        from agents import Agent

        rows = (
            db.query(Agent.name, Agent.creator_email, Agent.created_at)
            .order_by(Agent.created_at.asc())
            .all()
        )
        ranked = [(r.name, r.creator_email or "", r.created_at) for r in rows]
    except Exception:
        logger.warning("growth_milestones: agent query failed", exc_info=True)

    total = len(ranked)

    def _agent_fields(entry: tuple[str, str, datetime], _n: int) -> dict[str, Any]:
        name, email, at = entry
        return {
            "name": name,
            "email": email,
            "at": at.isoformat(),
        }

    latest = None
    if ranked:
        name, email, at = ranked[-1]
        latest = {"n": total, **_agent_fields((name, email, at), total)}

    targets = _milestone_targets_for_total(total)
    return {
        "total": total,
        "next_milestone": _next_milestone(total),
        "milestones": _pick_milestones(ranked, targets, _agent_fields),
        "latest": latest,
    }


async def _employee_by_email_map() -> dict[str, dict[str, str]]:
    try:
        from org_directory import get_all_employees

        employees = await get_all_employees()
        return _employee_by_email_from_iam(employees)
    except Exception:
        logger.warning("growth_milestones: IAM employee lookup failed", exc_info=True)
        return {}


def _compute_growth_milestones(
    db: Any,
    employee_by_email: dict[str, dict[str, str]],
) -> dict[str, Any]:
    return {
        "users": _compute_user_growth_milestones(db, employee_by_email),
        "agents": _compute_agent_growth_milestones(db),
    }


def _db_email_to_owner_sub(db: Any) -> dict[str, str]:
    """Map lowercase email → owner_sub from durable product tables (not user_events)."""
    out: dict[str, str] = {}
    try:
        from apps_models import App

        for owner_sub, email in db.query(App.owner_sub, App.creator_email).all():
            if owner_sub and email:
                out[email.lower()] = owner_sub
    except Exception:
        logger.warning("db_email_map: apps query failed", exc_info=True)
    try:
        from agents import Agent

        for owner_sub, email in db.query(Agent.owner_sub, Agent.creator_email).all():
            if owner_sub and email:
                out[email.lower()] = owner_sub
    except Exception:
        logger.warning("db_email_map: agents query failed", exc_info=True)
    try:
        from skill_submission_models import SkillSubmission

        for user_id, email in db.query(SkillSubmission.user_id, SkillSubmission.user_email).all():
            if user_id and email:
                out[email.lower()] = user_id
    except Exception:
        logger.warning("db_email_map: skill_submissions query failed", exc_info=True)
    return out


def _build_adoption_funnel(
    funnel_id: str,
    title: str,
    subtitle: str,
    scope: str,
    step_defs: list[tuple[str, str]],
    step_users: dict[str, set[str]],
    cohort_count: int | None = None,
    stuck_label: str | None = None,
    stuck_count: int = 0,
) -> dict[str, Any]:
    """Unified funnel card payload for platform + feature adoption funnels."""
    steps_out: list[dict[str, Any]] = []
    prev_count: int | None = None
    first_key = step_defs[0][0] if step_defs else ""
    cohort = cohort_count if cohort_count is not None else len(step_users.get(first_key, set()))

    for i, (key, label) in enumerate(step_defs):
        if key == "eligible" and cohort_count is not None:
            count = cohort_count
        else:
            count = len(step_users.get(key, set()))
        pct_cohort: float | None = None
        if cohort > 0:
            if key == "eligible":
                pct_cohort = 100.0
            else:
                pct_cohort = round(count / cohort * 100, 1)
        step_conv = (
            round(count / prev_count * 100, 1)
            if prev_count is not None and prev_count > 0 and i > 0
            else None
        )
        steps_out.append({
            "key": key,
            "label": label,
            "count": count,
            "pct_of_cohort": pct_cohort,
            "step_conv_pct": step_conv,
        })
        if i > 0 or key != "eligible":
            prev_count = count

    last_key = step_defs[-1][0] if step_defs else ""
    last_count = len(step_users.get(last_key, set()))
    activation_rate = round(last_count / cohort * 100, 1) if cohort > 0 else None

    return {
        "id": funnel_id,
        "title": title,
        "subtitle": subtitle,
        "scope": scope,
        "cohort_count": cohort,
        "activation_rate_pct": activation_rate,
        "stuck_label": stuck_label,
        "stuck_count": stuck_count,
        "steps": steps_out,
    }


def _compute_workspace_funnel(
    chats_current: list,
    msgs_current: list,
) -> dict[str, Any]:
    """Workspace funnel from chats + messages only."""
    visited: set[str] = set()
    skill_selected: set[str] = set()
    message_sent: set[str] = set()

    for r in chats_current:
        if r.owner_sub:
            visited.add(r.owner_sub)
            if r.active_skill_id:
                skill_selected.add(r.owner_sub)
    for r in msgs_current:
        if r.owner_sub:
            message_sent.add(r.owner_sub)

    stuck = len(visited - message_sent)
    return _build_adoption_funnel(
        funnel_id="workspace",
        title="Workspace",
        subtitle="Start chat → skill on chat → send message",
        scope="period",
        step_defs=[
            ("visited", "Chat Started"),
            ("skill_selected", "Skill on Chat"),
            ("message_sent", "Message Sent"),
        ],
        step_users={
            "visited": visited,
            "skill_selected": skill_selected,
            "message_sent": message_sent,
        },
        stuck_label="started a chat but haven't sent a message" if stuck else None,
        stuck_count=stuck,
    )


def _compute_lab_funnel(
    db: Any,
    period_start: datetime,
    period_end: datetime,
    apps_current: list,
) -> dict[str, Any]:
    """Lab funnel from apps + builder chat_history + token_usage_logs only."""
    import json as _json

    app_created: set[str] = set()
    engaged: set[str] = set()
    deployed: set[str] = set()

    for r in apps_current:
        if r.owner_sub:
            app_created.add(r.owner_sub)

    try:
        from apps_models import App

        for owner_sub, chat_history, status in (
            db.query(App.owner_sub, App.chat_history, App.status)
            .filter(App.created_at >= period_start, App.created_at < period_end)
            .all()
        ):
            if not owner_sub:
                continue
            if status == "deployed":
                deployed.add(owner_sub)
            try:
                history = _json.loads(chat_history or "[]")
                user_msgs = [
                    m for m in history
                    if isinstance(m, dict) and m.get("role") == "user"
                ]
                if user_msgs:
                    engaged.add(owner_sub)
            except Exception:
                pass
    except Exception:
        logger.warning("lab_funnel: apps query failed", exc_info=True)

    email_to_sub = _db_email_to_owner_sub(db)
    for email, _created_at in (
        db.query(TokenUsageLog.user_email, TokenUsageLog.created_at)
        .filter(
            TokenUsageLog.created_at >= period_start,
            TokenUsageLog.created_at < period_end,
            TokenUsageLog.source.in_(("lab", "lab-overnight")),
            TokenUsageLog.user_email.isnot(None),
        )
        .all()
    ):
        norm = _normalize_interaction_email(email or "")
        if norm and norm in email_to_sub:
            engaged.add(email_to_sub[norm])

    stuck = len(app_created - engaged)
    return _build_adoption_funnel(
        funnel_id="lab",
        title="Lab",
        subtitle="Create app → builder activity → deployed",
        scope="period",
        step_defs=[
            ("app_created", "App Created"),
            ("engaged", "Builder Engaged"),
            ("deployed", "Deployed"),
        ],
        step_users={
            "app_created": app_created,
            "engaged": engaged,
            "deployed": deployed,
        },
        stuck_label="created an app but no builder activity yet" if stuck else None,
        stuck_count=stuck,
    )


def _compute_agents_funnel(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    """Agents funnel from agents + agent_interaction_logs only."""
    registered: set[str] = set()
    slack_ready: set[str] = set()
    first_interaction: set[str] = set()

    try:
        from agents import Agent

        for owner_sub, slack_bot, xoxb, status, total_msgs in (
            db.query(
                Agent.owner_sub,
                Agent.slack_bot_user_id,
                Agent.xoxb_secret_ref,
                Agent.status,
                Agent.total_messages,
            )
            .filter(Agent.created_at >= period_start, Agent.created_at < period_end)
            .all()
        ):
            if not owner_sub:
                continue
            registered.add(owner_sub)
            if status == "active" and (slack_bot or xoxb):
                slack_ready.add(owner_sub)
            if (total_msgs or 0) > 0:
                first_interaction.add(owner_sub)
    except Exception:
        logger.warning("agents_funnel: agents query failed", exc_info=True)

    try:
        from agents import AgentInteractionLog, Agent

        for owner_sub, in (
            db.query(Agent.owner_sub)
            .join(AgentInteractionLog, AgentInteractionLog.agent_id == Agent.id)
            .filter(
                AgentInteractionLog.created_at >= period_start,
                AgentInteractionLog.created_at < period_end,
            )
            .distinct()
            .all()
        ):
            if owner_sub:
                first_interaction.add(owner_sub)
    except Exception:
        logger.warning("agents_funnel: interaction logs query failed", exc_info=True)

    stuck = len(slack_ready - first_interaction)
    return _build_adoption_funnel(
        funnel_id="agents",
        title="Agents",
        subtitle="Register → Slack connected → first interaction",
        scope="period",
        step_defs=[
            ("registered", "Registered"),
            ("slack_ready", "Slack Connected"),
            ("first_interaction", "First Interaction"),
        ],
        step_users={
            "registered": registered,
            "slack_ready": slack_ready,
            "first_interaction": first_interaction,
        },
        stuck_label="connected to Slack but no interactions yet" if stuck else None,
        stuck_count=stuck,
    )


def _compute_automations_funnel(
    autos_current: list,
    runs_current: list,
) -> dict[str, Any]:
    """Automations funnel from automations + automation_runs only."""
    created: set[str] = set()
    first_run: set[str] = set()
    successful: set[str] = set()

    for r in autos_current:
        if r.owner_sub:
            created.add(r.owner_sub)

    for r in runs_current:
        if r.owner_sub:
            first_run.add(r.owner_sub)
            if r.status == "success":
                successful.add(r.owner_sub)

    stuck = len(created - first_run)
    return _build_adoption_funnel(
        funnel_id="automations",
        title="Automations",
        subtitle="Create → first run → successful run",
        scope="period",
        step_defs=[
            ("created", "Created"),
            ("first_run", "First Run"),
            ("successful", "Successful Run"),
        ],
        step_users={
            "created": created,
            "first_run": first_run,
            "successful": successful,
        },
        stuck_label="created but never ran" if stuck else None,
        stuck_count=stuck,
    )


def _workspace_error_bucket(properties: str) -> str:
    """Normalize workspace.error_shown properties into a stable bucket key."""
    try:
        p = json.loads(properties or "{}")
        if p.get("error_type"):
            return str(p["error_type"])
        err = p.get("error")
        if err:
            text = str(err).strip()
            if len(text) <= 80:
                return text
            return "chat_error"
    except Exception:
        pass
    return "unknown"


def _compute_friction_signals(
    db: Any,
    period_start: datetime,
    period_end: datetime,
    evts_current: list,
    runs_failed: list,
    runs_current: list,
) -> dict[str, Any]:
    """Aggregate friction from durable DB tables plus supplemental user_events."""
    errors_by_type: dict[str, int] = defaultdict(int)
    validation_failures = 0
    app_create_abandoned = 0

    for r in evts_current:
        if r.event == "workspace.error_shown":
            errors_by_type[_workspace_error_bucket(r.properties)] += 1
        elif r.event == "lab.validate_sheet.failed":
            validation_failures += 1
        elif r.event == "lab.app_create_abandoned":
            app_create_abandoned += 1

    signals: dict[str, int] = {
        "automation_run_failures": len(runs_failed),
        "gws_validation_failures": validation_failures,
        "lab_create_abandoned": app_create_abandoned,
        "lab_app_errors": 0,
        "lab_build_failures": 0,
        "skill_review_failures": 0,
        "skill_preflight_failures": 0,
        "memory_sync_failures": 0,
        "skill_access_denied": 0,
        "agent_access_denied": 0,
        "agent_scheduled_run_failures": 0,
    }

    try:
        from apps_models import App

        signals["lab_app_errors"] = (
            db.query(App.id)
            .filter(
                App.status == "error",
                App.updated_at >= period_start,
                App.updated_at < period_end,
            )
            .count()
        )
        signals["lab_build_failures"] = (
            db.query(App.id)
            .filter(
                App.build_phase == "failed",
                App.build_updated_at >= period_start,
                App.build_updated_at < period_end,
            )
            .count()
        )
    except Exception:
        logger.debug("friction: apps query failed", exc_info=True)

    try:
        from skill_submission_models import SkillSubmission, SkillPreflight

        signals["skill_review_failures"] = (
            db.query(SkillSubmission.id)
            .filter(
                SkillSubmission.review_status == "fail",
                SkillSubmission.reviewed_at >= period_start,
                SkillSubmission.reviewed_at < period_end,
            )
            .count()
        )
        signals["skill_preflight_failures"] = (
            db.query(SkillPreflight.id)
            .filter(
                SkillPreflight.review_status == "fail",
                SkillPreflight.reviewed_at >= period_start,
                SkillPreflight.reviewed_at < period_end,
            )
            .count()
        )
    except Exception:
        logger.debug("friction: skill submission query failed", exc_info=True)

    try:
        signals["memory_sync_failures"] = (
            db.query(MemorySyncLog.id)
            .filter(
                MemorySyncLog.status == "error",
                MemorySyncLog.started_at >= period_start,
                MemorySyncLog.started_at < period_end,
            )
            .count()
        )
    except Exception:
        logger.debug("friction: memory_sync_log query failed", exc_info=True)

    try:
        from access_models import CrossDepartmentGrant, AgentAccessGrant, STATUS_DENIED

        signals["skill_access_denied"] = (
            db.query(CrossDepartmentGrant.id)
            .filter(
                CrossDepartmentGrant.status == STATUS_DENIED,
                CrossDepartmentGrant.decided_at >= period_start,
                CrossDepartmentGrant.decided_at < period_end,
            )
            .count()
        )
        signals["agent_access_denied"] = (
            db.query(AgentAccessGrant.id)
            .filter(
                AgentAccessGrant.status == STATUS_DENIED,
                AgentAccessGrant.decided_at >= period_start,
                AgentAccessGrant.decided_at < period_end,
            )
            .count()
        )
    except Exception:
        logger.debug("friction: access grant query failed", exc_info=True)

    try:
        from agents import AgentScheduledTask

        signals["agent_scheduled_run_failures"] = (
            db.query(AgentScheduledTask.id)
            .filter(
                AgentScheduledTask.last_run_status == "error",
                AgentScheduledTask.last_run_at >= period_start,
                AgentScheduledTask.last_run_at < period_end,
            )
            .count()
        )
    except Exception:
        logger.debug("friction: agent scheduled task query failed", exc_info=True)

    return {
        "auto_failure_rate_pct": round(len(runs_failed) / max(len(runs_current), 1) * 100, 1),
        "signals": signals,
        "errors_by_type": dict(errors_by_type),
    }


def _friction_to_api_list(friction: dict[str, Any]) -> list[dict[str, int | str]]:
    """Flatten friction signals into sorted API rows (non-zero counts only)."""
    items: list[dict[str, int | str]] = []
    for key, count in friction["signals"].items():
        if count > 0:
            items.append({"event": key, "count": count})
    for err_type, count in friction["errors_by_type"].items():
        if count > 0:
            items.append({"event": f"error:{err_type}", "count": count})
    items.sort(key=lambda row: int(row["count"]), reverse=True)
    return items


def _compute_skills_funnel(
    db: Any,
    period_start: datetime,
    period_end: datetime,
    chats_current: list,
) -> dict[str, Any]:
    """Skills funnel from skill_submissions + workspace chats only."""
    submitted: set[str] = set()
    approved: set[str] = set()
    used: set[str] = set()
    approved_slugs_by_user: dict[str, set[str]] = defaultdict(set)

    try:
        from skill_submission_models import SkillSubmission

        for user_id, _created_at in (
            db.query(SkillSubmission.user_id, SkillSubmission.created_at)
            .filter(
                SkillSubmission.created_at >= period_start,
                SkillSubmission.created_at < period_end,
            )
            .all()
        ):
            if user_id:
                submitted.add(user_id)

        for user_id, slug in (
            db.query(SkillSubmission.user_id, SkillSubmission.proposed_slug)
            .filter(
                SkillSubmission.review_status == "pass",
                SkillSubmission.created_at >= period_start,
                SkillSubmission.created_at < period_end,
            )
            .all()
        ):
            if not user_id:
                continue
            approved.add(user_id)
            if slug:
                approved_slugs_by_user[user_id].add(slug)
    except Exception:
        logger.warning("skills_funnel: submissions query failed", exc_info=True)

    for r in chats_current:
        if not r.owner_sub or not r.active_skill_id:
            continue
        if r.active_skill_id in approved_slugs_by_user.get(r.owner_sub, ()):
            used.add(r.owner_sub)

    stuck = len(submitted - approved)
    return _build_adoption_funnel(
        funnel_id="skills",
        title="Skills",
        subtitle="Submit → approved → used in workspace",
        scope="period",
        step_defs=[
            ("submitted", "Submitted"),
            ("approved", "Approved"),
            ("used", "Used in Workspace"),
        ],
        step_users={
            "submitted": submitted,
            "approved": approved,
            "used": used,
        },
        stuck_label="submitted but not yet approved" if stuck else None,
        stuck_count=stuck,
    )


def _compute_adoption_funnels(
    db: Any,
    period_start: datetime,
    period_end: datetime,
    chats_current: list,
    msgs_current: list,
    apps_current: list,
    autos_current: list,
    runs_current: list,
) -> dict[str, Any]:
    return {
        "workspace": _compute_workspace_funnel(chats_current, msgs_current),
        "lab": _compute_lab_funnel(db, period_start, period_end, apps_current),
        "agents": _compute_agents_funnel(db, period_start, period_end),
        "automations": _compute_automations_funnel(autos_current, runs_current),
        "skills": _compute_skills_funnel(db, period_start, period_end, chats_current),
    }


_CREATIONS_TOP_N = 10
_KPI_RANKINGS_TOP_N = 50

# Departments counted in the active-user denominator. Empty means "everyone".
# Names must match what the directory provider reports.
_ROLLOUT_DEPARTMENTS: frozenset[str] = frozenset(
    d.strip() for d in (os.environ.get("ROLLOUT_DEPARTMENTS") or "").split(",") if d.strip()
)


def _user_display_fields(
    owner_sub: str,
    email: str,
    employee_by_email: dict[str, dict[str, str]],
) -> dict[str, str]:
    emp = employee_by_email.get(email.lower(), {}) if email else {}
    name = emp.get("name") or (email.split("@")[0] if email else owner_sub[:12])
    return {
        "label": name,
        "sublabel": email or owner_sub[:12],
        "email": email,
        "department": emp.get("department") or "",
    }


def _iam_department_from_record(emp: Any) -> str:
    """Canonical department label as reported by the directory provider."""
    for candidate in (getattr(emp, "department", None), getattr(emp, "team_level1", None)):
        if candidate and str(candidate).strip():
            return str(candidate).strip()
    sage = (getattr(emp, "sage_department", "") or "").strip()
    if sage:
        return sage.split(" - ")[0].strip() or "Unknown"
    return "Unknown"


def _employee_by_email_from_iam(employees: list) -> dict[str, dict[str, str]]:
    """Lowercase email → {name, department} from IAM employee records."""
    return {
        (e.email or "").lower(): {
            "name": e.name or "",
            "department": _iam_department_from_record(e),
        }
        for e in employees
        if getattr(e, "email", None)
    }


async def _enrich_employee_map_for_active_users(
    active_user_emails: set[str],
    employee_by_email: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Look up IAM per active user when bulk map lacks a department."""
    from org_directory import get_employee_by_email

    out = dict(employee_by_email)
    needs_lookup = [
        email
        for email in active_user_emails
        if not (out.get(email, {}).get("department") or "").strip()
        or out.get(email, {}).get("department") == "Unknown"
    ]
    if not needs_lookup:
        return out

    async def _fetch(email: str) -> tuple[str, Any]:
        try:
            return email, await get_employee_by_email(email)
        except Exception:
            logger.debug("analytics: IAM lookup failed for %s", email[:3], exc_info=True)
            return email, None

    for email, rec in await asyncio.gather(*(_fetch(e) for e in needs_lookup)):
        if not rec:
            continue
        key = email.lower()
        dept = _iam_department_from_record(rec)
        prev = out.get(key, {})
        out[key] = {
            "name": rec.name or prev.get("name", ""),
            "department": dept,
        }
    return out


_UNKNOWN_DEPARTMENT = "Unknown"
_UNKNOWN_USERS_TOOLTIP_CAP = 50


def _unknown_department_reason(
    email: str,
    employee_by_email: dict[str, dict[str, str]],
) -> str:
    meta = employee_by_email.get(email.lower())
    if meta is None:
        return "Not found in IAM"
    dept = (meta.get("department") or "").strip()
    if not dept or dept == _UNKNOWN_DEPARTMENT:
        return "No department in directory"
    return "Unresolved"


def _department_for_active_email(
    email: str,
    employee_by_email: dict[str, dict[str, str]],
) -> str:
    dept = employee_by_email.get(email.lower(), {}).get("department") or _UNKNOWN_DEPARTMENT
    return dept.strip() or _UNKNOWN_DEPARTMENT


def _is_rollout_department(department: str) -> bool:
    # An unset ROLLOUT_DEPARTMENTS means the platform is rolled out to the whole
    # organisation, so every department counts towards the denominator. Treating
    # "unset" as "nobody" would silently report zero active users.
    if not _ROLLOUT_DEPARTMENTS:
        return True
    return department in _ROLLOUT_DEPARTMENTS


def _count_rollout_employees(all_employees: list) -> int:
    """Active headcount in the departments counted for the denominator."""
    return sum(
        1
        for emp in all_employees
        if getattr(emp, "is_active", False)
        and emp.email
        and _is_rollout_department(_iam_department_from_record(emp))
    )


_ALL_TIME_ACTIVITY_SINCE = datetime(2024, 1, 1)


def _query_user_message_rows(db: Any, since: datetime) -> list:
    """Workspace user messages only — excludes assistant replies and empty sends."""
    return (
        db.query(Message.chat_id, Message.role, Message.content, Message.created_at, Chat.owner_sub)
        .join(Chat, Message.chat_id == Chat.id)
        .filter(
            Message.created_at >= since,
            Message.role == "user",
            Message.content.isnot(None),
            Message.content != "",
        )
        .all()
    )


def _query_product_activity_source_rows(
    db: Any,
    since: datetime,
) -> tuple[list, list, list, list]:
    """Rows fed into _load_product_activity_events (no chats — chat start is not user activity)."""
    msg_rows = _query_user_message_rows(db, since)
    auto_rows = db.query(Automation.owner_sub, Automation.created_at, Automation.skill_id).filter(
        Automation.created_at >= since,
    ).all()
    app_rows: list = []
    try:
        from apps_models import App

        app_rows = db.query(App.creator_email, App.created_at, App.mode, App.owner_sub).filter(
            App.created_at >= since,
        ).all()
    except Exception:
        app_rows = []
    agent_rows: list = []
    try:
        from agents import Agent

        agent_rows = db.query(
            Agent.creator_email, Agent.created_at, Agent.status, Agent.owner_sub,
        ).filter(Agent.created_at >= since).all()
    except Exception:
        agent_rows = []
    return msg_rows, auto_rows, agent_rows, app_rows


def _distinct_product_activity_emails(
    db: Any,
    since: datetime,
    owner_emails: dict[str, str],
) -> set[str]:
    msg_rows, auto_rows, agent_rows, app_rows = _query_product_activity_source_rows(db, since)
    events = _load_product_activity_events(
        db, since, msg_rows, auto_rows, agent_rows, app_rows, owner_emails,
    )
    return {email for _, email in events}


async def _compute_inventory_totals(db: Any, owner_emails: dict[str, str]) -> dict[str, int]:
    """Platform inventory totals — independent of the analytics period filter.

    agents: count of rows in agents where status='active'.
    automations: count of rows in automations where is_active=true.
    skills: count of distinct proposed_slug in skill_submissions where review_status='pass'.
    Period-scoped activity uses agent_interaction_logs / automation_runs / user_events + agent
    skills_loaded, plus automation.skill_id on runs and apps.skill_ids on lab builds.
    """
    total_active_users = len(
        _distinct_product_activity_emails(db, _ALL_TIME_ACTIVITY_SINCE, owner_emails)
    )

    try:
        from agents import Agent as _Agent

        total_agents = db.query(_Agent.id).filter(_Agent.status == "active").count()
    except Exception:
        total_agents = 0

    total_automations = (
        db.query(Automation.id).filter(Automation.is_active == True).count()
    )

    try:
        from skill_submission_models import SkillSubmission as _SS

        total_skills = (
            db.query(_SS.proposed_slug)
            .filter(_SS.review_status == "pass")
            .distinct()
            .count()
        )
    except Exception:
        total_skills = 0

    return {
        "users": total_active_users,
        "agents": total_agents,
        "automations": total_automations,
        "skills": total_skills,
    }


def _count_distinct_agents_with_interactions(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> int:
    """Distinct agents with ≥1 row in agent_interaction_logs during [period_start, period_end)."""
    try:
        from agents import AgentInteractionLog

        return (
            db.query(AgentInteractionLog.agent_id)
            .filter(
                AgentInteractionLog.created_at >= period_start,
                AgentInteractionLog.created_at < period_end,
            )
            .distinct()
            .count()
        )
    except Exception:
        logger.debug("analytics: distinct active agents query failed", exc_info=True)
        return 0


def _agent_interaction_usage(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, dict[str, Any]]:
    """Per-agent interaction log counts in [period_start, period_end)."""
    counts: dict[str, int] = defaultdict(int)
    meta: dict[str, dict[str, str]] = {}
    try:
        from agents import AgentInteractionLog, Agent

        for agent_id, name, creator_email, creator_department in (
            db.query(
                AgentInteractionLog.agent_id,
                Agent.name,
                Agent.creator_email,
                Agent.creator_department,
            )
            .join(Agent, Agent.id == AgentInteractionLog.agent_id)
            .filter(
                AgentInteractionLog.created_at >= period_start,
                AgentInteractionLog.created_at < period_end,
            )
            .all()
        ):
            counts[agent_id] += 1
            if agent_id not in meta:
                meta[agent_id] = {
                    "name": name,
                    "creator_email": creator_email or "",
                    "creator_department": creator_department or "",
                }
    except Exception:
        logger.debug("analytics: agent interaction usage query failed", exc_info=True)

    return {
        agent_id: {**meta[agent_id], "count": count}
        for agent_id, count in counts.items()
    }


def _count_distinct_automations_with_runs(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> int:
    """Distinct automations with ≥1 row in automation_runs during [period_start, period_end)."""
    try:
        return (
            db.query(AutomationRun.automation_id)
            .filter(
                AutomationRun.started_at >= period_start,
                AutomationRun.started_at < period_end,
            )
            .distinct()
            .count()
        )
    except Exception:
        logger.debug("analytics: distinct active automations query failed", exc_info=True)
        return 0


def _automation_run_usage(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, dict[str, Any]]:
    """Per-automation run counts in [period_start, period_end)."""
    counts: dict[str, int] = defaultdict(int)
    meta: dict[str, dict[str, str]] = {}
    try:
        for automation_id, name, skill_id, owner_sub in (
            db.query(
                AutomationRun.automation_id,
                Automation.name,
                Automation.skill_id,
                Automation.owner_sub,
            )
            .join(Automation, Automation.id == AutomationRun.automation_id)
            .filter(
                AutomationRun.started_at >= period_start,
                AutomationRun.started_at < period_end,
            )
            .all()
        ):
            counts[automation_id] += 1
            if automation_id not in meta:
                meta[automation_id] = {
                    "name": name,
                    "skill_id": skill_id or "",
                    "owner_sub": owner_sub or "",
                }
    except Exception:
        logger.debug("analytics: automation run usage query failed", exc_info=True)

    return {
        automation_id: {**meta[automation_id], "count": count}
        for automation_id, count in counts.items()
    }


def _parse_skill_slug_list(raw: str | None) -> list[str]:
    """Parse a JSON array of skill slugs (from agent.skill_ids or skills_loaded)."""
    if not raw:
        return []
    try:
        data = json.loads(raw)
        if not isinstance(data, list):
            return []
        return [
            str(slug)
            for slug in data
            if slug and str(slug).lower() not in ("none", "null", "")
        ]
    except Exception:
        return []


def _load_skill_usage_events(db: Any, since: datetime) -> list[tuple[datetime, str]]:
    """(created_at, skill_slug) for each skill usage event on or after *since*."""
    events: list[tuple[datetime, str]] = []

    try:
        for created_at, properties in (
            db.query(UserEvent.created_at, UserEvent.properties)
            .filter(
                UserEvent.event == _SKILL_USAGE_EVENT,
                UserEvent.created_at >= since,
            )
            .all()
        ):
            slug = _workspace_message_skill_id(properties)
            if slug:
                events.append((created_at, slug))
    except Exception:
        logger.debug("analytics: skill workspace events query failed", exc_info=True)

    try:
        from agents import AgentInteractionLog, Agent

        for created_at, skills_loaded, agent_skill_ids in (
            db.query(
                AgentInteractionLog.created_at,
                AgentInteractionLog.skills_loaded,
                Agent.skill_ids,
            )
            .join(Agent, Agent.id == AgentInteractionLog.agent_id)
            .filter(AgentInteractionLog.created_at >= since)
            .all()
        ):
            slugs = _parse_skill_slug_list(skills_loaded)
            if not slugs:
                slugs = _parse_skill_slug_list(agent_skill_ids)
            for slug in slugs:
                events.append((created_at, slug))
    except Exception:
        logger.debug("analytics: skill agent events query failed", exc_info=True)

    try:
        for started_at, skill_id in (
            db.query(AutomationRun.started_at, Automation.skill_id)
            .join(Automation, AutomationRun.automation_id == Automation.id)
            .filter(
                AutomationRun.started_at >= since,
                Automation.skill_id.isnot(None),
                Automation.skill_id != "",
            )
            .all()
        ):
            slug = str(skill_id).strip()
            if slug and slug.lower() not in ("none", "null"):
                events.append((started_at, slug))
    except Exception:
        logger.debug("analytics: skill automation events query failed", exc_info=True)

    try:
        from apps_models import App

        for build_updated_at, skill_ids_raw in (
            db.query(App.build_updated_at, App.skill_ids)
            .filter(
                App.build_updated_at.isnot(None),
                App.build_updated_at >= since,
            )
            .all()
        ):
            for slug in _parse_skill_slug_list(skill_ids_raw):
                events.append((build_updated_at, slug))
    except Exception:
        logger.debug("analytics: skill lab build events query failed", exc_info=True)

    return events


def _skill_usage(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, dict[str, Any]]:
    """Per-skill usage counts in [period_start, period_end).

    Sources: see _load_skill_usage_events().
    """
    counts: dict[str, int] = defaultdict(int)
    for created_at, slug in _load_skill_usage_events(db, period_start):
        if _in_utc_window(created_at, period_start, period_end):
            counts[slug] += 1

    return {
        slug: {"name": _slug_display_name(slug), "count": count}
        for slug, count in counts.items()
    }


def _count_distinct_skills_with_usage(
    db: Any,
    period_start: datetime,
    period_end: datetime,
) -> int:
    """Distinct skills with ≥1 usage event in [period_start, period_end)."""
    return len(_skill_usage(db, period_start, period_end))


def _compute_kpi_rankings(
    db: Any,
    active_user_emails: set[str],
    period_start: datetime,
    period_end: datetime,
    product_activity_events: list[tuple[datetime, str]],
    agent_interaction_usage: dict[str, dict[str, Any]],
    automation_run_usage: dict[str, dict[str, Any]],
    skill_usage: dict[str, dict[str, Any]],
    employee_by_email: dict[str, dict[str, str]],
    owner_emails: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Rankings shown when admin clicks a KPI stat card."""
    email_scores = _product_activity_scores(product_activity_events, period_start, period_end)

    active_users: list[dict[str, Any]] = []
    for email in active_user_emails:
        fields = _user_display_fields("", email, employee_by_email)
        active_users.append({
            **fields,
            "count": email_scores.get(email, 0),
        })
    active_users.sort(key=lambda x: x["count"], reverse=True)

    active_agents: list[dict[str, Any]] = []
    for _agent_id, row in sorted(
        agent_interaction_usage.items(),
        key=lambda item: (-item[1]["count"], item[1]["name"]),
    ):
        active_agents.append({
            "label": row["name"],
            "sublabel": row["creator_email"] or "—",
            "email": row["creator_email"],
            "department": row["creator_department"],
            "count": row["count"],
        })

    active_automations: list[dict[str, Any]] = []
    for _auto_id, row in sorted(
        automation_run_usage.items(),
        key=lambda item: (-item[1]["count"], item[1]["name"]),
    ):
        email = owner_emails.get(row["owner_sub"], "")
        emp = employee_by_email.get(email.lower(), {}) if email else {}
        active_automations.append({
            "label": row["name"],
            "sublabel": row["skill_id"] or "—",
            "email": email,
            "department": emp.get("department") or "",
            "count": row["count"],
        })

    active_skills: list[dict[str, Any]] = []
    for slug, row in sorted(
        skill_usage.items(),
        key=lambda item: (-item[1]["count"], item[0]),
    ):
        active_skills.append({
            "label": slug,
            "sublabel": row["name"],
            "count": row["count"],
        })

    cap = _KPI_RANKINGS_TOP_N
    return {
        "active_users": active_users[:cap],
        "active_agents": active_agents[:cap],
        "active_automations": active_automations[:cap],
        "active_skills": active_skills[:cap],
    }


def _owner_sub_email_map(db: Any) -> dict[str, str]:
    out: dict[str, str] = {}
    for owner_sub, email in db.query(UserEvent.owner_sub, UserEvent.email).all():
        if email:
            out[owner_sub] = email
    return out


def _empty_creation_counts() -> dict[str, int]:
    return {"skills": 0, "agents": 0, "automations": 0}


def _compute_creations_breakdown(
    db: Any,
    period_start: datetime,
    period_end: datetime,
    employee_by_email: dict[str, dict[str, str]],
    owner_emails: dict[str, str],
) -> dict[str, list[dict[str, Any]]]:
    """Skills, agents, and automations created in the selected period."""
    dept_data: dict[str, dict[str, int]] = defaultdict(_empty_creation_counts)
    user_data: dict[str, dict[str, Any]] = {}

    def ensure_user(email: str) -> dict[str, Any]:
        key = email.lower()
        if key not in user_data:
            emp = employee_by_email.get(key, {})
            user_data[key] = {
                "email": email,
                "name": emp.get("name") or email.split("@")[0],
                "department": emp.get("department") or "Unknown",
                "skills": 0,
                "agents": 0,
                "automations": 0,
            }
        return user_data[key]

    def bump(email: str, field: str) -> None:
        if not email or "@" not in email:
            return
        key = email.lower()
        dept = employee_by_email.get(key, {}).get("department") or "Unknown"
        dept_data[dept][field] += 1
        ensure_user(email)[field] += 1

    try:
        from skill_submission_models import SkillSubmission

        skill_rows = (
            db.query(
                SkillSubmission.proposed_slug,
                SkillSubmission.user_email,
                SkillSubmission.created_at,
            )
            .filter(
                SkillSubmission.review_status == "pass",
                SkillSubmission.created_at >= period_start,
                SkillSubmission.created_at < period_end,
            )
            .all()
        )
        seen_slugs: set[str] = set()
        for slug, user_email, _created_at in sorted(skill_rows, key=lambda r: r[2]):
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)
            bump(user_email, "skills")
    except Exception:
        logger.warning("creations_breakdown: skills query failed", exc_info=True)

    try:
        from agents import Agent

        for creator_email, _created_at in (
            db.query(Agent.creator_email, Agent.created_at)
            .filter(
                Agent.status == "active",
                Agent.created_at >= period_start,
                Agent.created_at < period_end,
            )
            .all()
        ):
            if creator_email:
                bump(creator_email, "agents")
    except Exception:
        logger.warning("creations_breakdown: agents query failed", exc_info=True)

    for owner_sub, _created_at in (
        db.query(Automation.owner_sub, Automation.created_at)
        .filter(
            Automation.created_at >= period_start,
            Automation.created_at < period_end,
        )
        .all()
    ):
        bump(owner_emails.get(owner_sub, ""), "automations")

    dept_list: list[dict[str, Any]] = []
    for dept, counts in dept_data.items():
        total = counts["skills"] + counts["agents"] + counts["automations"]
        if total == 0:
            continue
        dept_list.append({
            "department": dept,
            "label": dept if len(dept) <= 18 else f"{dept[:16]}…",
            "skills": counts["skills"],
            "agents": counts["agents"],
            "automations": counts["automations"],
            "total": total,
        })
    dept_list.sort(key=lambda x: x["total"], reverse=True)

    user_list: list[dict[str, Any]] = []
    for row in user_data.values():
        total = row["skills"] + row["agents"] + row["automations"]
        if total == 0:
            continue
        display = row["name"] or row["email"]
        user_list.append({
            **row,
            "label": display if len(display) <= 16 else f"{display[:14]}…",
            "total": total,
        })
    user_list.sort(key=lambda x: x["total"], reverse=True)

    return {
        "by_department": dept_list[:_CREATIONS_TOP_N],
        "by_user": user_list[:_CREATIONS_TOP_N],
    }


def _department_headcount(all_employees: list) -> dict[str, int]:
    """Active people per department."""
    counts: dict[str, int] = defaultdict(int)
    for emp in all_employees:
        if not getattr(emp, "is_active", False) or not emp.email:
            continue
        dept = _iam_department_from_record(emp)
        counts[dept] += 1
    return counts


def _compute_active_users_by_department(
    active_user_emails: set[str],
    employee_by_email: dict[str, dict[str, str]],
    dept_headcount: dict[str, int],
) -> list[dict[str, Any]]:
    """Distinct active users in the period (product_activity_events), grouped by IAM department."""
    dept_users: dict[str, set[str]] = defaultdict(set)
    for email in active_user_emails:
        dept = _department_for_active_email(email, employee_by_email)
        dept_users[dept].add(email)

    rows: list[dict[str, Any]] = []
    for dept, emails in dept_users.items():
        active_count = len(emails)
        if active_count == 0:
            continue
        headcount = dept_headcount.get(dept, 0)
        row: dict[str, Any] = {
            "department": dept,
            "label": dept if len(dept) <= 18 else f"{dept[:16]}…",
            "active_users": active_count,
            "headcount": headcount if headcount > 0 else None,
            "penetration_pct": (
                round(active_count / headcount * 100, 1) if headcount > 0 else None
            ),
        }
        if dept == _UNKNOWN_DEPARTMENT:
            unknown_list = sorted(emails)
            row["unknown_users"] = [
                {
                    "email": email,
                    "name": employee_by_email.get(email.lower(), {}).get("name") or "",
                    "reason": _unknown_department_reason(email, employee_by_email),
                }
                for email in unknown_list[:_UNKNOWN_USERS_TOOLTIP_CAP]
            ]
            if len(unknown_list) > _UNKNOWN_USERS_TOOLTIP_CAP:
                row["unknown_users_truncated"] = len(unknown_list) - _UNKNOWN_USERS_TOOLTIP_CAP
        rows.append(row)
    rows.sort(key=lambda x: x["active_users"], reverse=True)
    # Return every department so row sums match summary.active_users (no top-N cap).
    return rows


def _day_label(date_str: str) -> str:
    try:
        d = datetime.strptime(date_str, "%Y-%m-%d")
        return d.strftime("%d %b")
    except Exception:
        return date_str


def _build_activity_timeline(
    days: int,
    product_activity_events: list[tuple[datetime, str]],
    agent_interaction_events: list[tuple[datetime, str]],
    automation_run_events: list[tuple[datetime, str]],
    skill_usage_events: list[tuple[datetime, str]],
    period_totals: dict[str, int],
) -> dict[str, Any]:
    """Daily buckets aligned with KPI summary tiles (active users/agents/automations/skills).

    Uses the same UTC calendar window as the page period filter (*days*).
    Header totals are taken from *period_totals* so they match summary KPIs exactly.
    """
    period_start, period_end = _utc_calendar_window(days)
    buckets = _fill_buckets("daily", days)

    series_map: dict[str, dict[str, Any]] = {
        b: {
            "date": b,
            "label": _day_label(b),
            "active_users": set(),
            "active_agents": set(),
            "active_automations": set(),
            "active_skills": set(),
        }
        for b in buckets
    }

    def _bkt(dt: datetime) -> str:
        return _bucket(dt, "daily")

    for created_at, email in product_activity_events:
        if not _in_utc_window(created_at, period_start, period_end):
            continue
        b = _bkt(created_at)
        if b in series_map:
            series_map[b]["active_users"].add(email)

    for created_at, agent_id in agent_interaction_events:
        if not agent_id or not _in_utc_window(created_at, period_start, period_end):
            continue
        b = _bkt(created_at)
        if b in series_map:
            series_map[b]["active_agents"].add(agent_id)

    for started_at, automation_id in automation_run_events:
        if not automation_id or not _in_utc_window(started_at, period_start, period_end):
            continue
        b = _bkt(started_at)
        if b in series_map:
            series_map[b]["active_automations"].add(automation_id)

    for created_at, slug in skill_usage_events:
        if not slug or not _in_utc_window(created_at, period_start, period_end):
            continue
        b = _bkt(created_at)
        if b in series_map:
            series_map[b]["active_skills"].add(slug)

    series: list[dict[str, Any]] = []
    for b in buckets:
        row = series_map[b]
        series.append({
            "date": b,
            "label": row["label"],
            "active_users": len(row["active_users"]),
            "active_agents": len(row["active_agents"]),
            "active_automations": len(row["active_automations"]),
            "active_skills": len(row["active_skills"]),
        })

    return {
        "days": days,
        "start_date": buckets[0] if buckets else "",
        "end_date": buckets[-1] if buckets else "",
        "totals": {
            "active_users": period_totals.get("active_users", 0),
            "active_agents": period_totals.get("active_agents", 0),
            "active_automations": period_totals.get("active_automations", 0),
            "active_skills": period_totals.get("active_skills", 0),
        },
        "series": series,
    }


def _partition_window(rows: list, ts_attr: str, window_days: int) -> tuple[list, list]:
    """Split rows into current and previous UTC calendar windows of equal length."""
    cur_start, cur_end = _utc_calendar_window(window_days)
    prev_start, prev_end = _previous_utc_calendar_window(window_days)
    current: list = []
    previous: list = []
    for r in rows:
        dt = getattr(r, ts_attr)
        if _in_utc_window(dt, cur_start, cur_end):
            current.append(r)
        elif _in_utc_window(dt, prev_start, prev_end):
            previous.append(r)
    return current, previous


def _normalize_interaction_email(email: str) -> str | None:
    normalized = (email or "").strip().lower()
    return normalized if "@" in normalized else None


def _email_from_owner_sub(owner_sub: str, owner_emails: dict[str, str]) -> str | None:
    return _normalize_interaction_email(owner_emails.get(owner_sub, ""))


def _load_product_activity_events(
    db: Any,
    since: datetime,
    msg_rows: list,
    auto_rows: list,
    agent_rows: list,
    app_rows: list,
    owner_emails: dict[str, str],
) -> list[tuple[datetime, str]]:
    """(created_at, email) for active-user metrics — durable product actions only.

    Sources: workspace user messages, automations/agents/apps created, agent
    @mention interactions, memory entries (approved or pending), and integration
    credentials stored (user_secrets rows).
    """
    events: list[tuple[datetime, str]] = []

    for r in msg_rows:
        if r.created_at < since:
            continue
        if not (getattr(r, "content", "") or "").strip():
            continue
        email = _email_from_owner_sub(r.owner_sub, owner_emails)
        if email:
            events.append((r.created_at, email))

    for r in auto_rows:
        if r.created_at < since:
            continue
        email = _email_from_owner_sub(r.owner_sub, owner_emails)
        if email:
            events.append((r.created_at, email))

    for r in agent_rows:
        if r.created_at < since:
            continue
        email = _normalize_interaction_email(getattr(r, "creator_email", "") or "")
        if email:
            events.append((r.created_at, email))

    for r in app_rows:
        if r.created_at < since:
            continue
        email = _normalize_interaction_email(getattr(r, "creator_email", "") or "")
        if not email:
            email = _email_from_owner_sub(getattr(r, "owner_sub", ""), owner_emails)
        if email:
            events.append((r.created_at, email))

    events.extend(_load_interaction_events(db, since))
    events.extend(_load_memory_activity_events(db, since, owner_emails))
    events.extend(_load_user_secrets_activity_events(db, since, owner_emails))
    return events


def _load_user_secrets_activity_events(
    db: Any,
    since: datetime,
    owner_emails: dict[str, str],
) -> list[tuple[datetime, str]]:
    """(created_at, email) when a user stores an integration credential (user_secrets insert)."""
    events: list[tuple[datetime, str]] = []
    try:
        for owner_sub, created_at in (
            db.query(UserSecret.owner_sub, UserSecret.created_at)
            .filter(UserSecret.created_at >= since)
            .all()
        ):
            if not created_at or created_at < since:
                continue
            email = _email_from_owner_sub(owner_sub, owner_emails)
            if email:
                events.append((created_at, email))
    except Exception:
        logger.debug("analytics: user_secrets activity query failed", exc_info=True)
    return events


def _load_memory_activity_events(
    db: Any,
    since: datetime,
    owner_emails: dict[str, str],
) -> list[tuple[datetime, str]]:
    """(at, email) for memory rows with status approved or pending.

    pending uses created_at; approved uses updated_at (typically when the user approved).
    """
    events: list[tuple[datetime, str]] = []
    try:
        for owner_sub, status, created_at, updated_at in (
            db.query(
                Memory.owner_sub,
                Memory.status,
                Memory.created_at,
                Memory.updated_at,
            )
            .filter(Memory.status.in_(("approved", "pending")))
            .all()
        ):
            at = updated_at if status == "approved" else created_at
            if not at or at < since:
                continue
            email = _email_from_owner_sub(owner_sub, owner_emails)
            if email:
                events.append((at, email))
    except Exception:
        logger.debug("analytics: memory activity query failed", exc_info=True)
    return events


def _load_interaction_events(db: Any, since: datetime) -> list[tuple[datetime, str]]:
    """(created_at, email) from Slack @mention agent interactions with a real user message."""
    events: list[tuple[datetime, str]] = []

    try:
        from agents import AgentInteractionLog

        for email, created_at, user_message in (
            db.query(
                AgentInteractionLog.invoker_email,
                AgentInteractionLog.created_at,
                AgentInteractionLog.user_message,
            )
            .filter(
                AgentInteractionLog.created_at >= since,
                AgentInteractionLog.trigger_type == _USER_FACING_AGENT_TRIGGER,
                AgentInteractionLog.user_message.isnot(None),
                AgentInteractionLog.user_message != "",
                AgentInteractionLog.user_message != "(empty)",
            )
            .all()
        ):
            if not (user_message or "").strip() or (user_message or "").strip() == "(empty)":
                continue
            norm = _normalize_interaction_email(email)
            if norm:
                events.append((created_at, norm))
    except Exception:
        logger.warning("interaction_users: agent interaction logs query failed", exc_info=True)

    return events


def _partition_calendar_activity_users(
    events: list[tuple[datetime, str]],
    window_days: int,
) -> tuple[set[str], set[str]]:
    """Distinct user emails in the current vs previous UTC calendar window."""
    cur_start, cur_end = _utc_calendar_window(window_days)
    prev_start, prev_end = _previous_utc_calendar_window(window_days)
    current: set[str] = set()
    previous: set[str] = set()
    for created_at, email in events:
        if cur_start <= created_at < cur_end:
            current.add(email)
        elif prev_start <= created_at < prev_end:
            previous.add(email)
    return current, previous


def _compute_calendar_activity_rankings(
    events: list[tuple[datetime, str]],
    employee_by_email: dict[str, dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    """Rank users by product activity volume within each DAU/WAU/MAU calendar window."""
    cap = _KPI_RANKINGS_TOP_N
    rankings: dict[str, list[dict[str, Any]]] = {}
    for key, window_days in _ROLLING_ACTIVE_WINDOWS:
        cur_start, cur_end = _utc_calendar_window(window_days)
        counts: dict[str, int] = defaultdict(int)
        for created_at, email in events:
            if cur_start <= created_at < cur_end:
                counts[email] += 1
        rows: list[dict[str, Any]] = []
        for email, count in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
            emp = employee_by_email.get(email, {})
            name = emp.get("name") or email.split("@")[0]
            rows.append({
                "label": name,
                "sublabel": email,
                "email": email,
                "department": emp.get("department", ""),
                "count": count,
            })
        rankings[key] = rows[:cap]
    return rankings


_ROLLING_ACTIVE_WINDOWS: tuple[tuple[str, int], ...] = (
    ("dau", 1),
    ("wau", 7),
    ("mau", 30),
)


def _rolling_active_summary(
    users: set[str],
    prev_users: set[str],
    rollout_employees: int,
) -> dict[str, Any]:
    """Active user count is company-wide; % penetration uses rollout dept headcount only."""
    count = len(users)
    prev_count = len(prev_users)
    return {
        "value": count,
        "delta_pct": _delta_pct(count, prev_count),
        "total_employees": rollout_employees,
        "pct": round(count / rollout_employees * 100, 1) if rollout_employees else None,
    }


# ── Main endpoint ─────────────────────────────────────────────────────────────

@router.get("/api/admin/analytics")
async def get_analytics(
    request: Request,
    period: str = "daily",
    days: int = 30,
) -> dict[str, Any]:
    ctx = build_auth_context(request.headers)
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")

    if period not in ("daily", "weekly", "monthly"):
        period = "daily"
    days = max(1, min(days, 365))

    cur_start, cur_end = _utc_calendar_window(days)
    prev_start, prev_end = _previous_utc_calendar_window(days)
    # Load enough history for DAU/WAU/MAU calendar windows and prior-period deltas.
    mau_prev_start, _ = _previous_utc_calendar_window(30)
    query_cut = min(prev_start, mau_prev_start)

    db = SessionLocal()
    try:

        # ── Apps (Lab) ────────────────────────────────────────────────────────
        try:
            from apps_models import App
            app_rows = db.query(App.creator_email, App.created_at, App.mode, App.owner_sub).filter(
                App.created_at >= query_cut
            ).all()
        except Exception:
            app_rows = []

        apps_current  = _rows_in_range(app_rows, cur_start, cur_end)
        apps_prev     = _rows_in_range(app_rows, prev_start, prev_end)

        # ── Workspace chats & messages ────────────────────────────────────────
        chat_rows = db.query(Chat.owner_sub, Chat.created_at, Chat.active_skill_id).filter(
            Chat.created_at >= query_cut
        ).all()
        chats_current = _rows_in_range(chat_rows, cur_start, cur_end)
        msg_rows = _query_user_message_rows(db, query_cut)
        msgs_current  = _rows_in_range(msg_rows, cur_start, cur_end)
        msgs_prev     = _rows_in_range(msg_rows, prev_start, prev_end)

        # ── Automations ───────────────────────────────────────────────────────
        auto_rows = db.query(Automation.owner_sub, Automation.created_at, Automation.skill_id).filter(
            Automation.created_at >= query_cut
        ).all()
        autos_current = _rows_in_range(auto_rows, cur_start, cur_end)
        autos_prev    = _rows_in_range(auto_rows, prev_start, prev_end)

        run_rows = (
            db.query(
                AutomationRun.automation_id,
                AutomationRun.started_at,
                AutomationRun.status,
                AutomationRun.duration_secs,
                Automation.owner_sub,
                Automation.skill_id,
            )
            .join(Automation, AutomationRun.automation_id == Automation.id)
            .filter(AutomationRun.started_at >= query_cut)
            .all()
        )
        runs_current = _rows_in_range(run_rows, cur_start, cur_end, "started_at")
        runs_failed  = [r for r in runs_current if r.status == "error"]

        # ── Agents registered ───────────────────────────────────────────────
        try:
            from agents import Agent
            agent_rows = db.query(
                Agent.creator_email, Agent.created_at, Agent.status, Agent.owner_sub
            ).filter(
                Agent.created_at >= query_cut,
            ).all()
            agents_current = [
                r for r in agent_rows
                if _in_utc_window(r.created_at, cur_start, cur_end) and r.status == "active"
            ]
            agents_prev = [
                r for r in agent_rows
                if _in_utc_window(r.created_at, prev_start, prev_end) and r.status == "active"
            ]
            agents_activity = _rows_in_range(agent_rows, cur_start, cur_end)
            agents_activity_prev = _rows_in_range(agent_rows, prev_start, prev_end)
        except Exception:
            agent_rows = []
            agents_current = agents_prev = []
            agents_activity = agents_activity_prev = []

        # ── Skills published (distinct approved slugs) ────────────────────────
        try:
            from skill_submission_models import SkillSubmission
            # One row per unique slug whose latest submission passed review
            skill_rows = db.query(SkillSubmission.proposed_slug, SkillSubmission.created_at, SkillSubmission.review_status).filter(
                SkillSubmission.review_status == "pass",
                SkillSubmission.created_at >= query_cut,
            ).all()
            # Deduplicate by slug — keep earliest passing submission per slug
            seen_slugs_current: set[str] = set()
            seen_slugs_prev: set[str] = set()
            skills_current = []
            skills_prev = []
            for r in skill_rows:
                if _in_utc_window(r.created_at, cur_start, cur_end) and r.proposed_slug not in seen_slugs_current:
                    seen_slugs_current.add(r.proposed_slug)
                    skills_current.append(r)
                elif _in_utc_window(r.created_at, prev_start, prev_end) and r.proposed_slug not in seen_slugs_prev:
                    seen_slugs_prev.add(r.proposed_slug)
                    skills_prev.append(r)
            skills_approved = skills_current
        except Exception:
            skills_current = skills_prev = skills_approved = []

        # ── User events (behavioral) ──────────────────────────────────────────
        evt_rows = db.query(UserEvent.owner_sub, UserEvent.email, UserEvent.event, UserEvent.created_at, UserEvent.properties).filter(
            UserEvent.created_at >= query_cut
        ).all()
        evts_current = _rows_in_range(evt_rows, cur_start, cur_end)
        evts_prev    = _rows_in_range(evt_rows, prev_start, prev_end)

        owner_emails = _owner_sub_email_map(db)
        for r in evts_current:
            if r.email and r.owner_sub:
                owner_emails[r.owner_sub] = r.email
        product_activity_events = _load_product_activity_events(
            db,
            query_cut,
            msg_rows,
            auto_rows,
            agent_rows,
            app_rows,
            owner_emails,
        )
        rolling_active: dict[str, tuple[set[str], set[str]]] = {
            key: _partition_calendar_activity_users(product_activity_events, window_days)
            for key, window_days in _ROLLING_ACTIVE_WINDOWS
        }

        # ── KPI: active entities in selected period ───────────────────────────
        # Same distinct-email definition as DAU/WAU/MAU (product_activity_events).
        active_user_emails, prev_active_user_emails = _partition_calendar_activity_users(
            product_activity_events, days,
        )
        active_users_delta_day = _day_over_day_activity_delta(product_activity_events)

        active_automation_run_usage = _automation_run_usage(db, cur_start, cur_end)
        active_automations_count = _count_distinct_automations_with_runs(db, cur_start, cur_end)
        prev_active_automations_count = _count_distinct_automations_with_runs(
            db, prev_start, prev_end,
        )

        skill_usage = _skill_usage(db, cur_start, cur_end)
        active_skills_count = len(skill_usage)
        prev_active_skills_count = _count_distinct_skills_with_usage(
            db, prev_start, prev_end,
        )

        agent_interaction_events: list[tuple[datetime, str]] = []
        try:
            from agents import AgentInteractionLog

            agent_interaction_events = [
                (created_at, agent_id)
                for created_at, agent_id in (
                    db.query(AgentInteractionLog.created_at, AgentInteractionLog.agent_id)
                    .filter(AgentInteractionLog.created_at >= query_cut)
                    .all()
                )
                if agent_id
            ]
        except Exception:
            logger.debug("analytics: agent interaction timeline events failed", exc_info=True)

        automation_run_events = [
            (r.started_at, r.automation_id)
            for r in run_rows
            if getattr(r, "started_at", None) and getattr(r, "automation_id", None)
        ]
        skill_usage_events = _load_skill_usage_events(db, query_cut)

        agent_interaction_usage = _agent_interaction_usage(db, cur_start, cur_end)
        active_agents_count = _count_distinct_agents_with_interactions(db, cur_start, cur_end)
        prev_active_agents_count = _count_distinct_agents_with_interactions(
            db, prev_start, prev_end,
        )

        # ── Top agents by interaction log volume ──────────────────────────────
        top_agents = sorted(
            [
                {
                    "agent_id": agent_id,
                    "name": row["name"],
                    "interaction_count": row["count"],
                }
                for agent_id, row in agent_interaction_usage.items()
            ],
            key=lambda x: x["interaction_count"],
            reverse=True,
        )[:10]

        # ── Top users by product_activity_events volume ───────────────────────
        user_activity_counts = _product_activity_scores(
            product_activity_events, cur_start, cur_end,
        )
        top_users = sorted(
            [{"email": email, "actions": cnt}
             for email, cnt in user_activity_counts.items()],
            key=lambda x: (-x["actions"], x["email"]),
        )[:10]

        # ── Friction signals ──────────────────────────────────────────────────
        mode_gws = 0
        mode_skills = 0
        for r in evts_current:
            if r.event == "lab.mode_selected":
                try:
                    p = json.loads(r.properties or "{}")
                    if p.get("mode") == "gws":
                        mode_gws += 1
                    else:
                        mode_skills += 1
                except Exception:
                    pass

        friction = _compute_friction_signals(
            db, cur_start, cur_end, evts_current, runs_failed, runs_current,
        )

        # ── Time series ──────────────────────────────────────────────────────
        buckets = _fill_buckets(period, days)
        series = _build_time_series(
            buckets,
            period,
            period_start=cur_start,
            period_end=cur_end,
            product_activity_events=product_activity_events,
            chats_current=chats_current,
            msgs_current=msgs_current,
            apps_current=apps_current,
            autos_current=autos_current,
            runs_current=runs_current,
            agents_current=agents_current,
            skills_current=skills_current,
        )

        # ── Feature usage breakdown (total actions per feature) ───────────────
        feature_usage = {
            "workspace_chats":       len(chats_current),
            "workspace_messages":    len(msgs_current),
            "lab_apps_created":      len(apps_current),
            "automations_created":   len(autos_current),
            "automation_runs":       len(runs_current),
            "agents_registered":     len(agents_current),
            "skills_submitted":      len(skills_current),
            "skills_approved":       len(skills_approved),
        }

        def _item(value: int, delta: float | None, delta_day: float | None = None) -> dict:
            out: dict[str, Any] = {"value": value, "delta_pct": delta}
            if delta_day is not None:
                out["delta_pct_day"] = delta_day
            return out

        try:
            from org_directory import get_all_employees
            all_employees = await get_all_employees()
        except Exception:
            all_employees = []

        employee_by_email = _employee_by_email_from_iam(all_employees)
        employee_by_email = await _enrich_employee_map_for_active_users(
            active_user_emails, employee_by_email,
        )
        rollout_employee_count = _count_rollout_employees(all_employees)
        dept_headcount = _department_headcount(all_employees)
        inventory = await _compute_inventory_totals(db, owner_emails)
        growth_milestones = _compute_growth_milestones(db, employee_by_email)
        activity_timeline = _build_activity_timeline(
            days,
            product_activity_events=product_activity_events,
            agent_interaction_events=agent_interaction_events,
            automation_run_events=automation_run_events,
            skill_usage_events=skill_usage_events,
            period_totals={
                "active_users": len(active_user_emails),
                "active_agents": active_agents_count,
                "active_automations": active_automations_count,
                "active_skills": active_skills_count,
            },
        )
        adoption_funnels = _compute_adoption_funnels(
            db,
            cur_start,
            cur_end,
            chats_current,
            msgs_current,
            apps_current,
            autos_current,
            runs_current,
        )
        creations_breakdown = _compute_creations_breakdown(
            db, cur_start, cur_end, employee_by_email, owner_emails,
        )
        kpi_rankings = _compute_kpi_rankings(
            db,
            active_user_emails=active_user_emails,
            period_start=cur_start,
            period_end=cur_end,
            product_activity_events=product_activity_events,
            agent_interaction_usage=agent_interaction_usage,
            automation_run_usage=active_automation_run_usage,
            skill_usage=skill_usage,
            employee_by_email=employee_by_email,
            owner_emails=owner_emails,
        )
        kpi_rankings.update(
            _compute_calendar_activity_rankings(product_activity_events, employee_by_email),
        )
        active_users_by_department = _compute_active_users_by_department(
            active_user_emails,
            employee_by_email,
            dept_headcount,
        )

        return {
            "period": period,
            "days":   days,
            "inventory": inventory,
            "summary": {
                "active_users":        _item(
                    len(active_user_emails),
                    _delta_pct(len(active_user_emails), len(prev_active_user_emails)),
                    active_users_delta_day,
                ),
                "active_agents":       _item(
                    active_agents_count,
                    _delta_pct(active_agents_count, prev_active_agents_count),
                ),
                "active_automations":  _item(
                    active_automations_count,
                    _delta_pct(active_automations_count, prev_active_automations_count),
                ),
                "active_skills":       _item(
                    active_skills_count,
                    _delta_pct(active_skills_count, prev_active_skills_count),
                ),
                "dau": _rolling_active_summary(*rolling_active["dau"], rollout_employee_count),
                "wau": _rolling_active_summary(*rolling_active["wau"], rollout_employee_count),
                "mau": _rolling_active_summary(*rolling_active["mau"], rollout_employee_count),
            },
            "time_series":     series,
            "feature_usage":   [{"feature": k, "count": v} for k, v in feature_usage.items()],
            "top_agents":      [{"name": a["name"], "count": a["interaction_count"]} for a in top_agents],
            "top_users":       [{"email": u["email"], "count": u["actions"]} for u in top_users],
            "adoption_funnels": adoption_funnels,
            "friction":        _friction_to_api_list(friction),
            "mode_split": {"gws": mode_gws, "skills": mode_skills},
            "growth_milestones": growth_milestones,
            "activity_timeline": activity_timeline,
            "creations_by_department": creations_breakdown["by_department"],
            "creations_by_user": creations_breakdown["by_user"],
            "active_users_by_department": active_users_by_department,
            "kpi_rankings": kpi_rankings,
        }

    finally:
        db.close()


def _compute_funnel(events: list, steps: list[str]) -> list[dict]:
    step_users: dict[str, set[str]] = {s: set() for s in steps}
    for r in events:
        if r.event in step_users:
            step_users[r.event].add(r.owner_sub)
    result = []
    for i, step in enumerate(steps):
        count = len(step_users[step])
        prev  = len(step_users[steps[i - 1]]) if i > 0 else count
        result.append({
            "step":         step,
            "label":        step.split(".")[-1].replace("_", " ").title(),
            "count":        count,
            "drop_pct":     round((1 - count / max(prev, 1)) * 100, 1) if i > 0 else 0,
        })
    return result


def _build_time_series(
    buckets: list[str],
    period: str,
    product_activity_events: list[tuple[datetime, str]],
    period_start: datetime,
    period_end: datetime,
    **kwargs: list,
) -> list[dict]:
    chats_current  = kwargs["chats_current"]
    msgs_current   = kwargs["msgs_current"]
    apps_current   = kwargs["apps_current"]
    autos_current  = kwargs["autos_current"]
    runs_current   = kwargs["runs_current"]
    agents_current = kwargs["agents_current"]
    skills_current = kwargs["skills_current"]

    def _bkt(dt): return _bucket(dt, period)

    series_map: dict[str, dict] = {b: {
        "date":               b,
        "workspace_chats":    0,
        "workspace_messages": 0,
        "lab_apps":           0,
        "automations":        0,
        "automation_runs":    0,
        "agents":             0,
        "skills":             0,
        "active_users":       set(),
    } for b in buckets}

    for created_at, email in product_activity_events:
        if not _in_utc_window(created_at, period_start, period_end):
            continue
        b = _bkt(created_at)
        if b in series_map:
            series_map[b]["active_users"].add(email)

    for r in chats_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["workspace_chats"] += 1

    for r in msgs_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["workspace_messages"] += 1

    for r in apps_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["lab_apps"] += 1

    for r in autos_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["automations"] += 1

    for r in runs_current:
        b = _bkt(r.started_at)
        if b in series_map:
            series_map[b]["automation_runs"] += 1

    for r in agents_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["agents"] += 1

    for r in skills_current:
        b = _bkt(r.created_at)
        if b in series_map:
            series_map[b]["skills"] += 1

    return [
        {**{k: v for k, v in row.items() if k != "active_users"},
         "active_users": len(row["active_users"])}
        for row in [series_map[b] for b in buckets]
    ]
