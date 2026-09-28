"""Agent scheduled-task runtime and APScheduler registration.

Cron-driven prompts stored in ``agent_scheduled_tasks`` are executed in an
isolated subprocess (see ``agent_worker.spawn_agent_scheduled_task``) so each
run loads only that agent's secrets.  Runs use the agent creator's skill ACL,
the agent's soul/skills/guardrails, and the bot token to post to
``output_channel``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone

from apscheduler.triggers.cron import CronTrigger

from agents import Agent, AgentScheduledTask, loads, record_agent_exchange
from cron_scheduler import cron_scheduler_enabled
from database import SessionLocal

logger = logging.getLogger(__name__)

AGENT_TASK_JOB_PREFIX = "agent-task:"

# When report formatting is disabled, the agent acts via tools only — no channel post.
_SCHEDULED_TASK_ACTION_ONLY = """\
---

## Scheduled Task — Actions Only
You are running as a scheduled automation. Perform all required work using
``run_python`` (API calls, Slack posts, queries, etc.). The platform will NOT
post your final reply text to the output channel.

Use ``run_python`` for every Slack message or API action. When finished, reply
with a single short line confirming completion (for internal logging only).
Do NOT write a summary report, recap, or multi-paragraph explanation.
"""

# When report formatting is enabled, the first LLM call only collects data.
_SCHEDULED_TASK_DATA_COLLECTION = """\
---

## Scheduled Task — Data Collection
You are running as a scheduled automation. Use ``run_python`` to gather data
(API calls, queries, etc.). Return ONLY the raw findings and tool output —
do NOT format a final report. The platform will format your results in a
separate step and post to the output channel automatically.
Do NOT call ``chat_postMessage`` or post to Slack yourself.
"""


def agent_task_job_id(task_id: str) -> str:
    return f"{AGENT_TASK_JOB_PREFIX}{task_id}"


async def _generate_automation_report(
    *,
    agent: Agent,
    task_prompt: str,
    findings: str,
    report_system_prompt: str,
) -> str:
    """Second LLM step: format raw scheduled-task output into a report.

    Transport matches the agent reply path: Auto → OpenAI chat/completions;
    all other models → Anthropic messages.
    """
    import anthropic
    from openai import AsyncOpenAI

    from agent_llm import (
        resolve_agent_gateway_api_key,
        resolve_agent_model,
        uses_openai_agent_transport,
    )
    from api_slack_events import (
        _MODEL_GATEWAY_API_KEY,
        _MODEL_GATEWAY_URL,
        _MODEL_GATEWAY_URL_RAW,
        _read_secret,
    )
    from model_catalog import gateway_openai_base_url
    from token_tracking import record_token_usage as _record_usage

    api_key, _src = resolve_agent_gateway_api_key(
        agent,
        platform_key=_MODEL_GATEWAY_API_KEY,
        read_secret=_read_secret,
    )
    if not api_key:
        logger.error("No gateway API key — cannot generate automation report")
        return findings

    model = resolve_agent_model(agent)
    report_user_prompt = (
        f"Task: {task_prompt}\n\n"
        f"Query results:\n\n{findings}\n\n"
        "Write a concise markdown report summarising these results."
    )

    if uses_openai_agent_transport(model):
        client = AsyncOpenAI(
            api_key=api_key,
            base_url=gateway_openai_base_url(_MODEL_GATEWAY_URL_RAW),
        )
        try:
            response = await client.chat.completions.create(
                model=model,
                max_tokens=4096,
                messages=[
                    {"role": "system", "content": report_system_prompt},
                    {"role": "user", "content": report_user_prompt},
                ],
            )
        finally:
            await client.close()

        usage = response.usage
        if usage is not None:
            class _Usage:
                pass
            u = _Usage()
            u.input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
            u.output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
            _record_usage("automation", model, u, agent_id=str(agent.id))
        text = ""
        if response.choices and response.choices[0].message.content:
            text = response.choices[0].message.content.strip()
        return text or findings

    from agent_llm import cached_system

    client = anthropic.AsyncAnthropic(api_key=api_key, base_url=_MODEL_GATEWAY_URL)
    response = await client.messages.create(
        model=model,
        max_tokens=4096,
        system=cached_system(report_system_prompt, model=model),
        messages=[{"role": "user", "content": report_user_prompt}],
    )
    _record_usage("automation", model, response.usage, agent_id=str(agent.id))
    return "".join(
        b.text for b in response.content if getattr(b, "type", None) == "text"
    ) or findings


_MAX_RUN_ERROR = 500


def _friendly_slack_error(code: str) -> str:
    code = (code or "").strip().lower()
    mapping = {
        "not_in_channel": (
            "This agent is not in that Slack channel yet. "
            "Invite it with /invite @the-agent-name, then try again."
        ),
        "channel_not_found": (
            "That Slack channel was not found. Check the name and invite the agent first."
        ),
        "is_archived": "That Slack channel is archived. Choose an active channel.",
        "invalid_auth": "The agent's Slack connection expired. Reconnect Slack from the agent page.",
        "not_authed": "The agent's Slack connection expired. Reconnect Slack from the agent page.",
        "token_revoked": "The agent's Slack connection was revoked. Reconnect Slack from the agent page.",
        "account_inactive": "The Slack workspace is no longer active for this agent.",
        "missing_scope": "This agent is missing Slack permission to post in channels.",
    }
    return mapping.get(
        code,
        "Could not post to Slack. Invite the agent to the channel and try again.",
    )


def _friendly_run_error(exc: BaseException) -> str:
    text = str(exc).lower()
    for code in (
        "not_in_channel",
        "channel_not_found",
        "is_archived",
        "invalid_auth",
        "not_authed",
        "token_revoked",
        "missing_scope",
    ):
        if code in text:
            return _friendly_slack_error(code)
    return "The run did not finish. Check that the agent is active and invited to the channel."


def _post_scheduled_slack(xoxb: str, channel: str, text: str) -> str | None:
    """Post to Slack. Returns a user-facing error string, or None on success."""
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    try:
        WebClient(token=xoxb).chat_postMessage(channel=channel, text=text)
        return None
    except SlackApiError as exc:
        code = ""
        try:
            code = str((exc.response or {}).get("error") or "")
        except Exception:
            code = ""
        logger.warning("Scheduled Slack post failed channel=%s error=%s", channel, code)
        return _friendly_slack_error(code)
    except Exception:
        logger.exception("Scheduled Slack post failed channel=%s", channel)
        return "Could not post to Slack. Invite the agent to the channel and try again."


def _mark_task_run(task_id: str, status: str, error: str | None = None) -> None:
    db = SessionLocal()
    try:
        task = db.get(AgentScheduledTask, task_id)
        if not task:
            return
        task.last_run_at = datetime.now(timezone.utc)
        task.last_run_status = status
        if hasattr(task, "last_run_error"):
            msg = (error or "").strip()
            task.last_run_error = msg[:_MAX_RUN_ERROR] if status == "error" and msg else None
        task.run_count = (task.run_count or 0) + 1
        db.commit()
    except Exception:
        logger.exception("Failed to update scheduled task run status task_id=%s", task_id)
    finally:
        db.close()


def _run_agent_scheduled_task(agent_id: str, task_id: str) -> None:
    """APScheduler entry point — runs the async dispatch loop."""
    try:
        asyncio.run(dispatch_scheduled_task(agent_id, task_id))
    except Exception as exc:
        logger.exception(
            "Agent scheduled task failed agent_id=%s task_id=%s", agent_id, task_id
        )
        _mark_task_run(task_id, "error", _friendly_run_error(exc))


async def dispatch_scheduled_task(agent_id: str, task_id: str, *, manual: bool = False) -> None:
    """Load agent + task, run the LLM loop, and post the result to Slack."""
    from api_slack_events import (
        _DERIV_ONE_BASE_URL,
        _INTERNAL_SIGNING_SECRET,
        _SLACK_QUERY_SKILL,
        _build_memory_context,
        _call_llm,
        _filter_accessible_skills,
        _load_agent_skills,
        _load_skill_secrets_for_agent,
        _read_secret,
        _sign_invocation_context,
    )
    from api_agents import _resolve_guardrail_text
    from automation_prompts import resolve_automation_report_prompt

    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        task = db.get(AgentScheduledTask, task_id)
        if not agent or not task or task.agent_id != agent_id:
            logger.warning(
                "Scheduled task skipped — agent=%s task=%s not found", agent_id, task_id
            )
            return
        if not task.is_active and not manual:
            return
        if agent.status != "active":
            logger.info(
                "Scheduled task %s skipped — agent %s status=%s",
                task_id, agent_id, agent.status,
            )
            _mark_task_run(
                task_id,
                "error",
                "This agent is not active. Activate it first, then run the automation.",
            )
            return
        prompt = (task.prompt or "").strip()
        output_channel = (task.output_channel or "").strip()
        creator_email = (agent.creator_email or "").strip()
        if not prompt or not output_channel:
            logger.warning("Scheduled task %s missing prompt or output_channel", task_id)
            _mark_task_run(
                task_id,
                "error",
                "This automation is missing instructions or a Slack channel.",
            )
            return
        if not creator_email:
            logger.warning(
                "Scheduled task %s skipped — agent %s has no creator_email", task_id, agent_id
            )
            _mark_task_run(task_id, "error", "This agent has no owner email, so the run cannot start.")
            return
    finally:
        db.close()

    xoxb = _read_secret(agent.xoxb_secret_ref)
    if not xoxb:
        logger.error("Scheduled task %s — agent %s has no bot token", task_id, agent_id)
        _mark_task_run(
            task_id,
            "error",
            "This agent is not connected to Slack yet. Finish Slack setup, then try again.",
        )
        return

    try:
        skill_ids = loads(agent.skill_ids)
    except Exception:
        skill_ids = []

    accessible_skill_slugs = await _filter_accessible_skills(creator_email, agent, skill_override=skill_ids)
    restricted_skill_slugs = [s for s in skill_ids if s not in accessible_skill_slugs]

    display_name = agent.name
    soul = (agent.soul_text or "").strip()
    guardrails = agent.guardrails or []

    post_report = bool(getattr(task, "report_enabled", False))
    if post_report:
        identity = (
            f"You are {display_name}, a Slack bot assistant running a scheduled task. "
            "You have full access to your assigned tools and skills — never claim you lack access. "
            "Produce a complete, self-contained report — your reply text will be posted "
            "to the output channel automatically. "
            "Do NOT say you are an AI or disclaim Slack integration.\n\n"
            "FORMATTING RULES — follow these exactly:\n"
            "- Use Slack mrkdwn, NOT standard markdown.\n"
            "- Bold: *text* (single asterisk). Never use **double asterisks**.\n"
            "- Italic: _text_ (underscore).\n"
            "- Inline code: `code`.\n"
            "- Bullet lists: start each item with • or - on its own line.\n"
            "- Section headers: *Header* on its own line, followed by a blank line.\n"
            "- DO NOT use markdown tables — use bullet or numbered lists instead.\n"
            "- Keep the report concise and actionable.\n\n"
            f"This is an automated scheduled run on behalf of the agent owner ({creator_email}).\n\n"
        )
    else:
        identity = (
            f"You are {display_name}, a Slack bot assistant running a scheduled task. "
            "You have full access to your assigned tools and skills — never claim you lack access. "
            "Perform all work through your tools — the platform will not post your final reply "
            "to the output channel. "
            "Do NOT say you are an AI or disclaim Slack integration.\n\n"
            f"This is an automated scheduled run on behalf of the agent owner ({creator_email}).\n\n"
        )

    skill_context = _load_agent_skills(agent, skill_slugs=accessible_skill_slugs)
    loop = asyncio.get_running_loop()
    skill_secrets = await loop.run_in_executor(
        None, _load_skill_secrets_for_agent, agent, accessible_skill_slugs
    )

    system_prompt = identity
    if soul:
        system_prompt += soul + "\n\n"

    active_guardrails = [g for g in guardrails if g.get("enabled")]
    if active_guardrails:
        rules = "\n".join(
            f"- {_resolve_guardrail_text(g)}"
            for g in active_guardrails
            if _resolve_guardrail_text(g)
        )
        if rules:
            system_prompt += "## Guardrails\nYou must follow these rules at all times:\n" + rules + "\n\n"

    if skill_context:
        system_prompt += skill_context

    if restricted_skill_slugs:
        from skills_acl import get_skill_acl

        restricted_lines: list[str] = []
        for slug in restricted_skill_slugs:
            acl = get_skill_acl(slug)
            desc = acl.description if acl else slug
            restricted_lines.append(f"- **{slug}**: {desc}")
        system_prompt += (
            "\n\n## Skills Required But Not Accessible\n\n"
            "The following skills are attached to this agent but the owner does not currently "
            "have access to them at this scheduled run. Do NOT call run_python for these skills.\n\n"
            + "\n".join(restricted_lines)
        )

    mem_ctx = _build_memory_context(str(agent.id), creator_email, channel_id=None)
    if mem_ctx:
        system_prompt += "\n\n" + mem_ctx

    merged_secrets = dict(skill_secrets or {})
    merged_secrets["SENDER_EMAIL"] = creator_email
    merged_secrets["SLACK_BOT_TOKEN"] = xoxb
    merged_secrets["AGENT_ID"] = str(agent.id)
    merged_secrets["PLATFORM_INTERNAL_BASE_URL"] = _DERIV_ONE_BASE_URL
    merged_secrets["DERIV_ONE_BASE_URL"] = _DERIV_ONE_BASE_URL
    if _INTERNAL_SIGNING_SECRET:
        try:
            merged_secrets["DERIV_ONE_CTX_TOKEN"] = _sign_invocation_context(
                sender_email=creator_email,
                source_agent_id=str(agent.id),
            )
        except Exception:
            logger.warning("Could not sign invocation context for scheduled task %s", task_id)

    report_system_prompt = resolve_automation_report_prompt(task)
    if report_system_prompt:
        system_prompt += _SCHEDULED_TASK_DATA_COLLECTION
    else:
        system_prompt += _SCHEDULED_TASK_ACTION_ONLY
    system_prompt += _SLACK_QUERY_SKILL

    llm_started = time.time()
    tool_audit: list[dict] = []
    try:
        llm_outcome = await _call_llm(
            system_prompt=system_prompt,
            user_content=prompt,
            agent=agent,
            history=None,
            skill_secrets=merged_secrets,
            tool_audit=tool_audit,
        )
        if llm_outcome.failure_kind:
            from agent_failure_alerts import notify_agent_failure

            await notify_agent_failure(
                agent,
                llm_outcome,
                source="scheduled_task",
                channel_id=output_channel,
                scheduled_task_id=task_id,
            )
        raw_reply = llm_outcome.text
        if report_system_prompt:
            reply_text = await _generate_automation_report(
                agent=agent,
                task_prompt=prompt,
                findings=raw_reply,
                report_system_prompt=report_system_prompt,
            )
            post_err = await loop.run_in_executor(
                None, _post_scheduled_slack, xoxb, output_channel, reply_text
            )
            if post_err:
                status = "error"
                run_error = post_err
            else:
                status = "success"
                run_error = None
        else:
            reply_text = raw_reply
            status = "success"
            run_error = None
    except Exception as exc:
        logger.exception("Scheduled task LLM/Slack failed task_id=%s", task_id)
        status = "error"
        run_error = _friendly_run_error(exc)
        reply_text = ""
        try:
            await loop.run_in_executor(
                None,
                _post_scheduled_slack,
                xoxb,
                output_channel,
                (
                    "*Scheduled task failed*\n"
                    "The agent could not complete its scheduled run. "
                    "Please check the agent configuration and try again."
                ),
            )
        except Exception:
            logger.exception("Failed to post scheduled-task error to channel=%s", output_channel)

    _mark_task_run(task_id, status, run_error)
    llm_duration_ms = int((time.time() - llm_started) * 1000)
    if reply_text:
        await loop.run_in_executor(
            None,
            lambda: record_agent_exchange(
                agent_id=agent.id,
                user_message=prompt,
                assistant_message=reply_text,
                invoker_email=creator_email,
                trigger_type="scheduled",
                channel_id=output_channel,
                duration_ms=llm_duration_ms,
                secrets_loaded=sorted(merged_secrets.keys()),
                tools_called=tool_audit or None,
            ),
        )
        from api_slack_events import _extract_and_store_memories
        await _extract_and_store_memories(
            agent_id=str(agent.id),
            user_email=creator_email,
            user_msg=prompt,
            agent_msg=reply_text,
            channel_id=output_channel,
        )


def schedule_agent_task(task: AgentScheduledTask) -> None:
    """Register one active agent scheduled task with the shared APScheduler."""
    if not cron_scheduler_enabled():
        return
    if not task.is_active:
        return
    parts = (task.schedule or "").strip().split()
    if len(parts) != 5:
        logger.warning("Invalid cron '%s' for agent task %s", task.schedule, task.id)
        return
    try:
        trigger = CronTrigger(
            minute=parts[0],
            hour=parts[1],
            day=parts[2],
            month=parts[3],
            day_of_week=parts[4],
            timezone=task.timezone or "Asia/Dubai",
        )
        from main import scheduler
        from cron_worker_pool import CRON_EXECUTOR

        scheduler.add_job(
            _run_agent_scheduled_task,
            trigger=trigger,
            id=agent_task_job_id(task.id),
            replace_existing=True,
            args=[task.agent_id, task.id],
            executor=CRON_EXECUTOR,
        )
        logger.info(
            "Scheduled agent task %s for agent %s cron=%s tz=%s",
            task.id, task.agent_id, task.schedule, task.timezone,
        )
    except Exception:
        logger.exception("Failed to schedule agent task %s", task.id)


def unschedule_agent_task(task_id: str) -> None:
    if not cron_scheduler_enabled():
        return
    from main import scheduler

    try:
        scheduler.remove_job(agent_task_job_id(task_id))
    except Exception:
        pass


def reschedule_agent_tasks_for_agent(agent_id: str) -> int:
    db = SessionLocal()
    try:
        tasks = (
            db.query(AgentScheduledTask)
            .filter(
                AgentScheduledTask.agent_id == agent_id,
                AgentScheduledTask.is_active.is_(True),
            )
            .all()
        )
        for task in tasks:
            schedule_agent_task(task)
        return len(tasks)
    finally:
        db.close()


def unschedule_agent_tasks_for_agent(agent_id: str) -> None:
    db = SessionLocal()
    try:
        task_ids = [
            row[0]
            for row in db.query(AgentScheduledTask.id)
            .filter(AgentScheduledTask.agent_id == agent_id)
            .all()
        ]
    finally:
        db.close()
    for task_id in task_ids:
        unschedule_agent_task(task_id)


def load_and_schedule_agent_tasks() -> int:
    """Load all active agent scheduled tasks and register them with APScheduler."""
    if not cron_scheduler_enabled():
        return 0
    db = SessionLocal()
    try:
        active = db.query(AgentScheduledTask).filter(AgentScheduledTask.is_active.is_(True)).all()
        scheduled = 0
        for task in active:
            agent = db.get(Agent, task.agent_id)
            if not agent or agent.status != "active":
                continue
            schedule_agent_task(task)
            scheduled += 1
        logger.info(
            "Loaded %d active agent scheduled task(s) (%d registered)",
            len(active),
            scheduled,
        )
        return scheduled
    finally:
        db.close()


def trigger_agent_scheduled_task(agent_id: str, task_id: str) -> None:
    """Run one agent scheduled task immediately in a background subprocess."""
    import threading

    from agent_worker import spawn_agent_scheduled_task

    def _run() -> None:
        try:
            spawn_agent_scheduled_task(agent_id, task_id, manual=True)
        except Exception:
            logger.exception(
                "Manual agent scheduled task failed agent_id=%s task_id=%s",
                agent_id,
                task_id,
            )

    threading.Thread(target=_run, daemon=True, name=f"agent-task-run-{task_id[:8]}").start()
    logger.info("Triggered manual run for agent task %s (agent %s)", task_id, agent_id)
