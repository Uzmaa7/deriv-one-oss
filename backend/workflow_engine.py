"""Workflow execution engine — vertical slice (trigger → AI → delegate → HITL → resolve)."""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from database import SessionLocal
from workflow_models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowRunEvent,
    WorkflowWaitBinding,
    _json_dumps,
)

logger = logging.getLogger(__name__)

LlmJsonCallable = Callable[
    [Any, str, str],
    Awaitable[str],
]


async def noop_llm_json(*_a: Any, **_k: Any) -> str:
    """Placeholder for advance_run's llm_json param on flow runs.

    Flows only use skill.run nodes, never ai_action/delegate_wait,
    so this is never actually invoked — it exists because advance_run's
    signature is shared with playbooks, which do need a real implementation.
    """
    return ""


def noop_post_slack(*_a: Any, **_k: Any) -> None:
    """Placeholder for advance_run's post_slack param on flow runs. See noop_llm_json."""
    return None

_TEMPLATE_RE = re.compile(r"\{\{([^}]+)\}\}")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def parse_graph(defn: WorkflowDefinition) -> dict[str, Any]:
    try:
        graph = json.loads(defn.graph_json or "{}")
    except json.JSONDecodeError:
        graph = {}
    nodes = graph.get("nodes") or []
    by_id = {n["id"]: n for n in nodes if isinstance(n, dict) and n.get("id")}
    edges = [e for e in (graph.get("edges") or []) if isinstance(e, dict)]
    return {"nodes": by_id, "edges": edges, "raw": graph}


def _next_node(graph: dict[str, Any], from_id: str, on: Optional[str] = None) -> Optional[str]:
    for edge in graph["edges"]:
        if edge.get("from") != from_id:
            continue
        edge_on = edge.get("on")
        if on is None:
            if edge_on:
                continue
            return edge.get("to")
        if edge_on == on:
            return edge.get("to")
    for edge in graph["edges"]:
        if edge.get("from") == from_id and not edge.get("on"):
            return edge.get("to")
    return None


def _get_path(ctx: dict[str, Any], path: str) -> Any:
    parts = path.strip().split(".")
    cur: Any = ctx
    for p in parts:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(p)
    return cur


def _render_template(template: str, ctx: dict[str, Any], extras: dict[str, str]) -> str:
    def repl(match: re.Match) -> str:
        key = match.group(1).strip()
        if key in extras:
            return extras[key]
        if key.startswith("context."):
            val = _get_path(ctx, key[len("context.") :])
        else:
            val = _get_path(ctx, key)
        return str(val) if val is not None else ""

    return _TEMPLATE_RE.sub(repl, template)


class FlowStepError(RuntimeError):
    """Raised by a flow step (skill.run / browser.task) to abort the run cleanly.

    Caught locally in advance_run's dispatch and turned into a manual_review
    terminal state with the message recorded on the timeline — never lets a
    typed step's failure surface as a bare stack trace.
    """


class MissingContextKeyError(FlowStepError):
    """A {{context.x}} placeholder had no value in the run context."""


def _resolve_context_key(key: str, ctx: dict[str, Any]) -> Any:
    if key.startswith("context."):
        return _get_path(ctx, key[len("context.") :])
    return _get_path(ctx, key)


def _render_deep(value: Any, ctx: dict[str, Any], extras: Optional[dict[str, str]] = None) -> Any:
    """Like _render_template, but walks dicts/lists and is type-preserving.

    _render_template returns "" for a missing key and always returns a str —
    fine for a free-text Slack message body. A typed skill.run/browser.task
    step config is not free text: a step's args built from a missing key must
    fail loudly rather than silently carry an empty string forward (a browser
    step navigating to a URL built from "" is the failure mode this exists to
    prevent). A string that is *exactly* one placeholder ("{{context.foo}}")
    resolves to the underlying value with its real type intact, so a
    non-string context value (a number, a list, a nested object) can flow
    into an artifact param without being stringified.
    """
    extras = extras or {}
    if isinstance(value, str):
        if "{{" not in value:
            return value
        sole = _TEMPLATE_RE.fullmatch(value.strip())
        if sole:
            key = sole.group(1).strip()
            resolved = extras[key] if key in extras else _resolve_context_key(key, ctx)
            if resolved is None:
                raise MissingContextKeyError(f"Missing context value for: {key}")
            return resolved

        missing: list[str] = []

        def repl(match: re.Match) -> str:
            key = match.group(1).strip()
            resolved = extras[key] if key in extras else _resolve_context_key(key, ctx)
            if resolved is None:
                missing.append(key)
                return ""
            return str(resolved)

        rendered = _TEMPLATE_RE.sub(repl, value)
        if missing:
            raise MissingContextKeyError(f"Missing context value(s) for: {', '.join(missing)}")
        return rendered
    if isinstance(value, dict):
        return {k: _render_deep(v, ctx, extras) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_deep(v, ctx, extras) for v in value]
    return value


def first_step_after_trigger(graph: dict[str, Any]) -> Optional[str]:
    """Find the entry node id for a graph whose first node is any trigger.* type.

    Mirrors how playbooks resolve their Slack-mention entry edge
    (_next_node(graph, "tr_slack_mention")) but generalized to any trigger id,
    since a flow's trigger.manual node can be named anything. The trigger node
    itself is never executed by advance_run — same as today's playbooks — this
    only locates the edge leading out of it.
    """
    nodes = (graph.get("raw") or {}).get("nodes") or []
    for n in nodes:
        if isinstance(n, dict) and str(n.get("type", "")).startswith("trigger."):
            return _next_node(graph, str(n.get("id") or ""))
    return None


def _append_event(
    db: Session,
    run: WorkflowRun,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
    step_id: Optional[str] = None,
) -> None:
    db.add(
        WorkflowRunEvent(
            run_id=run.id,
            event_type=event_type,
            step_id=step_id,
            payload_json=_json_dumps(payload or {}),
        )
    )


def find_published_workflow_for_channel(db: Session, agent_id: str, channel_id: str) -> Optional[WorkflowDefinition]:
    rows = (
        db.query(WorkflowDefinition)
        .filter(
            WorkflowDefinition.agent_id == agent_id,
            WorkflowDefinition.status == "published",
        )
        .order_by(WorkflowDefinition.published_at.desc())
        .all()
    )
    for row in rows:
        graph = parse_graph(row)
        trigger = graph["nodes"].get("tr_slack_mention")
        if not trigger:
            continue
        allowed = (trigger.get("config") or {}).get("channel_ids") or []
        if not allowed or channel_id in allowed:
            return row
    return None


def run_has_active_thread(db: Session, agent_id: str, channel: str, thread_ts: str) -> bool:
    rows = (
        db.query(WorkflowRun)
        .filter(
            WorkflowRun.agent_id == agent_id,
            WorkflowRun.status.in_(("active", "waiting", "pending_hitl")),
        )
        .order_by(WorkflowRun.updated_at.desc())
        .limit(50)
        .all()
    )
    for run in rows:
        slack = (run.context or {}).get("slack") or {}
        if slack.get("channel_id") == channel and slack.get("thread_ts") == thread_ts:
            return True
    return False


async def try_start_workflow_from_mention(
    agent: Any,
    *,
    channel: str,
    thread_ts: str,
    user_text: str,
    bot_user_id: str,
    llm_json: LlmJsonCallable,
    post_slack: Callable[[str, str, str], None],
    xoxb: str,
) -> bool:
    """Start a published workflow on app_mention. Returns True if handled."""
    db = SessionLocal()
    try:
        if run_has_active_thread(db, agent.id, channel, thread_ts):
            logger.info("workflow: active run exists for thread agent=%s", agent.id)
            return False

        defn = find_published_workflow_for_channel(db, agent.id, channel)
        if not defn:
            return False

        graph = parse_graph(defn)
        first = _next_node(graph, "tr_slack_mention")
        if not first:
            return False

        run = WorkflowRun(
            agent_id=agent.id,
            definition_id=defn.id,
            definition_version=defn.version,
            status="active",
            current_step=first,
            context_json=_json_dumps(
                {
                    "slack": {
                        "channel_id": channel,
                        "thread_ts": thread_ts,
                        "trigger_text": user_text,
                    },
                    "bot_user_id": bot_user_id,
                }
            ),
        )
        db.add(run)
        db.flush()
        _append_event(db, run, "run_started", {"channel": channel, "thread_ts": thread_ts})
        db.commit()

        await advance_run(
            agent,
            run.id,
            llm_json=llm_json,
            post_slack=lambda ch, ts, text: post_slack(xoxb, ch, ts, text),
        )
        return True
    except Exception:
        logger.exception("workflow try_start failed agent=%s", agent.id)
        db.rollback()
        return False
    finally:
        db.close()


async def try_resume_workflow_from_message(
    agent: Any,
    *,
    channel: str,
    thread_ts: str,
    sender_id: str,
    message_text: str,
    llm_json: LlmJsonCallable,
    post_slack: Callable[[str, str, str], None],
    xoxb: str,
) -> bool:
    """Resume a waiting workflow when a thread message arrives."""
    db = SessionLocal()
    try:
        binding = (
            db.query(WorkflowWaitBinding)
            .filter(
                WorkflowWaitBinding.channel_id == channel,
                WorkflowWaitBinding.thread_ts == thread_ts,
            )
            .order_by(WorkflowWaitBinding.created_at.desc())
            .first()
        )
        if not binding:
            return False

        run = db.get(WorkflowRun, binding.run_id)
        if not run or run.agent_id != agent.id or run.status != "waiting":
            return False

        expected = json.loads(binding.expected_senders_json or "[]")
        own_bot = (getattr(agent, "slack_bot_user_id", "") or "").strip()
        if own_bot and sender_id == own_bot:
            return False
        if expected and sender_id not in expected:
            return False

        ctx = run.context
        node = parse_graph(db.get(WorkflowDefinition, run.definition_id))["nodes"].get(run.current_step) or {}
        writes = (node.get("config") or {}).get("context_writes") or ["investigator_reply"]
        key = writes[0] if writes else "investigator_reply"
        ctx[key] = message_text
        run.set_context(ctx)
        run.status = "active"
        db.delete(binding)
        _append_event(
            db,
            run,
            "delegate_reply",
            {"sender_id": sender_id, "text_len": len(message_text)},
            step_id=run.current_step,
        )
        db.commit()

        nxt = _next_node(parse_graph(db.get(WorkflowDefinition, run.definition_id)), run.current_step)
        if nxt:
            run.current_step = nxt
            db.commit()

        await advance_run(
            agent,
            run.id,
            llm_json=llm_json,
            post_slack=lambda ch, ts, text: post_slack(xoxb, ch, ts, text),
        )
        return True
    except Exception:
        logger.exception("workflow resume failed agent=%s", agent.id)
        db.rollback()
        return False
    finally:
        db.close()


async def advance_run(
    agent: Any,
    run_id: str,
    *,
    llm_json: LlmJsonCallable,
    post_slack: Callable[[str, str, str], None],
    hitl_edge: Optional[str] = None,
    owner_sub: Optional[str] = None,
    owner_email: Optional[str] = None,
) -> None:
    """Advance until wait, HITL pause, or terminal.

    *owner_sub*/*owner_email* are only used by the skill.run/browser.task
    branches (owner-scoped flows have no agent object to carry identity on;
    playbook call sites leave both None and never reach those branches).
    """
    db = SessionLocal()
    try:
        max_steps = 20
        for _ in range(max_steps):
            run = db.get(WorkflowRun, run_id)
            if not run or run.status in ("resolved", "manual_review", "cancelled"):
                return
            if run.status == "pending_hitl" and not hitl_edge:
                return
            if run.status == "waiting":
                return

            defn = db.get(WorkflowDefinition, run.definition_id)
            if not defn:
                return
            graph = parse_graph(defn)
            node = graph["nodes"].get(run.current_step)
            if not node:
                logger.error("workflow missing node %s run=%s", run.current_step, run_id)
                return

            ntype = node.get("type", "")
            cfg = node.get("config") or {}
            ctx = run.context

            if ntype == "ai_action":
                await _step_ai_action(db, run, node, ctx, agent, llm_json)
                nxt = _next_node(graph, run.current_step)
                if not nxt:
                    break
                run.current_step = nxt
                run.updated_at = _utcnow()
                db.commit()
                continue

            if ntype == "delegate_wait":
                await _step_delegate_wait(db, run, node, ctx, agent, post_slack)
                db.commit()
                return

            if ntype == "hitl.approve_message":
                if hitl_edge:
                    nxt = _next_node(graph, run.current_step, on=hitl_edge)
                    run.set_pending_gate(None)
                    run.status = "active"
                    if hitl_edge == "approved" and nxt:
                        run.current_step = nxt
                    elif hitl_edge == "rejected":
                        reject_to = cfg.get("on_reject") or "term_manual"
                        run.current_step = reject_to
                    run.updated_at = _utcnow()
                    db.commit()
                    hitl_edge = None
                    continue
                _step_hitl(db, run, node, ctx)
                db.commit()
                return

            if ntype == "action.slack_post":
                ch = _resolve_from_cfg(cfg, "channel_from", ctx) or (ctx.get("slack") or {}).get("channel_id")
                ts = _resolve_from_cfg(cfg, "thread_ts_from", ctx) or (ctx.get("slack") or {}).get("thread_ts")
                text = _resolve_from_cfg(cfg, "text_from", ctx) or ctx.get("approved_message") or ""
                if ch and text:
                    post_slack(str(ch), str(ts or ""), str(text))
                _append_event(db, run, "slack_post", {"channel": ch}, step_id=run.current_step)
                nxt = _next_node(graph, run.current_step)
                if nxt:
                    run.current_step = nxt
                run.updated_at = _utcnow()
                db.commit()
                continue

            if ntype.startswith("terminal."):
                _step_terminal(db, run, node, cfg)
                db.commit()
                return

            if ntype == "skill.run":
                try:
                    await _step_skill_run(
                        db, run, node, ctx, owner_sub=owner_sub or "", owner_email=owner_email
                    )
                except FlowStepError as exc:
                    _fail_run(db, run, node, str(exc))
                    db.commit()
                    return
                nxt = _next_node(graph, run.current_step)
                if not nxt:
                    break
                run.current_step = nxt
                run.updated_at = _utcnow()
                db.commit()
                continue

            logger.warning("workflow unsupported node type %s", ntype)
            break
    except Exception:
        logger.exception("workflow advance_run failed run=%s", run_id)
        db.rollback()
    finally:
        db.close()


def _fail_run(db: Session, run: WorkflowRun, node: dict[str, Any], reason: str) -> None:
    """Park a run in manual_review with the failure on the timeline.

    Used for skill.run/browser.task errors — a typed step failing should be
    visible on the run's timeline, not just a rolled-back exception the caller
    never sees (the outer try/except in advance_run only logs and rolls back,
    which would otherwise leave the run silently stuck at current_step).
    """
    run.status = "manual_review"
    run.ended_at = _utcnow()
    run.updated_at = _utcnow()
    _append_event(db, run, "step_failed", {"reason": reason[:2000]}, step_id=node.get("id"))


def _resolve_from_cfg(cfg: dict[str, Any], key: str, ctx: dict[str, Any]) -> Any:
    ref = cfg.get(key) or ""
    if isinstance(ref, str) and ref.startswith("context."):
        return _get_path(ctx, ref[len("context.") :])
    return ref


async def _step_ai_action(
    db: Session,
    run: WorkflowRun,
    node: dict[str, Any],
    ctx: dict[str, Any],
    agent: Any,
    llm_json: LlmJsonCallable,
) -> None:
    cfg = node.get("config") or {}
    prompt = cfg.get("prompt_template") or "Analyze the workflow context."
    schema = cfg.get("output_schema") or {}
    user_payload = json.dumps(
        {"context": ctx, "instruction": prompt, "output_schema": schema},
        ensure_ascii=False,
    )
    system = (
        "You are a workflow step processor. Output ONLY valid JSON matching output_schema. "
        "No markdown fences."
    )
    raw = await llm_json(agent, system, user_payload)
    data = _parse_json_object(raw)
    for key in cfg.get("context_writes") or []:
        if key in data:
            ctx[key] = data[key]
    run.set_context(ctx)
    _append_event(db, run, "ai_action_done", {"keys": list(data.keys())}, step_id=node.get("id"))
    run.updated_at = _utcnow()


async def _step_delegate_wait(
    db: Session,
    run: WorkflowRun,
    node: dict[str, Any],
    ctx: dict[str, Any],
    agent: Any,
    post_slack: Callable[[str, str, str], None],
) -> None:
    cfg = node.get("config") or {}
    delegate = str(cfg.get("delegate_user_id") or "")
    bot_uid = str(ctx.get("bot_user_id") or "")
    extras = {"delegate_user_id": delegate, "bot_user_id": bot_uid}
    template = cfg.get("message_template") or ""
    text = _render_template(template, ctx, extras)
    slack = ctx.get("slack") or {}
    channel = str(slack.get("channel_id") or "")
    thread_ts = str(slack.get("thread_ts") or "")
    if channel and text:
        post_slack(channel, thread_ts, text)

    wait_cfg = cfg.get("wait") or {}
    timeout_h = int(wait_cfg.get("timeout_hours") or 24)
    expected = [delegate] if delegate.startswith("U") else []
    binding = WorkflowWaitBinding(
        run_id=run.id,
        wait_id=str(uuid4()),
        kind="slack_thread",
        channel_id=channel,
        thread_ts=thread_ts,
        expected_senders_json=_json_dumps(expected),
        expires_at=_utcnow() + timedelta(hours=timeout_h),
    )
    db.add(binding)
    run.status = "waiting"
    _append_event(db, run, "delegate_sent", {"delegate": delegate}, step_id=node.get("id"))
    run.updated_at = _utcnow()


def _step_hitl(db: Session, run: WorkflowRun, node: dict[str, Any], ctx: dict[str, Any]) -> None:
    cfg = node.get("config") or {}
    payload_from = (cfg.get("payload_from") or "context.draft_message").replace("context.", "", 1)
    payload = _get_path(ctx, payload_from) or ctx.get("draft_message") or ""
    gate_id = str(uuid4())
    run.set_pending_gate(
        {
            "id": gate_id,
            "type": "approve_message",
            "title": cfg.get("title") or "Approval required",
            "payload": payload,
            "assignees": cfg.get("assignees") or [],
            "step_id": node.get("id"),
            "created_at": _utcnow().isoformat(),
        }
    )
    run.status = "pending_hitl"
    _append_event(db, run, "hitl_pending", {"gate_id": gate_id}, step_id=node.get("id"))
    run.updated_at = _utcnow()


def _step_terminal(db: Session, run: WorkflowRun, node: dict[str, Any], cfg: dict[str, Any]) -> None:
    status = cfg.get("status") or ("resolved" if "resolve" in node.get("type", "") else "manual_review")
    run.status = "resolved" if status == "resolved" else "manual_review"
    run.ended_at = _utcnow()
    run.updated_at = _utcnow()
    _append_event(
        db,
        run,
        "terminal",
        {"resolution_owner": cfg.get("resolution_owner"), "status": run.status},
        step_id=node.get("id"),
    )


def _parse_json_object(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if "```" in text:
            text = text.rsplit("```", 1)[0]
    try:
        val = json.loads(text)
        return val if isinstance(val, dict) else {}
    except json.JSONDecodeError:
        logger.warning("workflow ai_action invalid json")
        return {}


# ── skill.run — Flows (backend/api_flows.py) ──────────────────────────────────
#
# Owner-scoped step, unlike ai_action (agent-scoped). Access is re-checked
# against the flow owner on every run, never trusted from the graph.


async def _step_skill_run(
    db: Session,
    run: WorkflowRun,
    node: dict[str, Any],
    ctx: dict[str, Any],
    *,
    owner_sub: str,
    owner_email: Optional[str],
) -> None:
    """Artifact mode calls a typed SkillArtifact; agentic mode asks the
    skill's own instructions to answer, coerced to output_schema.
    """
    from skill_router import resolve_user_skill_access

    cfg = node.get("config") or {}
    slug = str(cfg.get("skill_slug") or "").strip()
    if not slug:
        raise FlowStepError("skill.run step has no skill_slug configured")
    if not owner_sub:
        raise FlowStepError("skill.run requires a flow owner")

    _, _, accessible_slugs = await resolve_user_skill_access(owner_sub, owner_email)
    if slug not in accessible_slugs:
        raise FlowStepError(f"You do not have access to skill '{slug}'")

    mode = cfg.get("mode") or "agentic"
    if mode == "artifact":
        data = await _run_skill_artifact(slug, cfg, ctx)
    else:
        data = await _run_skill_agentic(slug, cfg, ctx)

    writes = cfg.get("context_writes") or []
    for key in writes:
        if key in data:
            ctx[key] = data[key]
    run.set_context(ctx)
    _append_event(
        db,
        run,
        "skill_run_done",
        {"slug": slug, "mode": mode, "keys": [k for k in writes if k in data]},
        step_id=node.get("id"),
    )
    run.updated_at = _utcnow()


async def _run_skill_artifact(slug: str, cfg: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Deterministic path: validated params -> sandboxed entrypoint -> JSON stdout.

    Calls artifact_runtime directly rather than
    api_slack_events._execute_call_artifact, which has four live call sites
    across every Slack agent and both workspace chat paths — not worth
    coupling a new feature to that hot path.
    """
    from artifact_models import SkillArtifact
    from artifact_params import ParamsValidationError
    from artifact_runtime import (
        build_call_artifact_code,
        prepare_call,
        record_run,
        resolve_artifact,
        scoped_secrets,
    )
    from executor import execute_in_sandbox

    artifact_name = str(cfg.get("artifact_name") or "").strip()
    if not artifact_name:
        raise FlowStepError("artifact mode requires config.artifact_name")

    raw_params = _render_deep(cfg.get("params") or {}, ctx)
    if not isinstance(raw_params, dict):
        raise FlowStepError("artifact mode requires config.params to be an object")

    art_db = SessionLocal()
    try:
        art = resolve_artifact(art_db, [slug], artifact_name)
        if art is None:
            raise FlowStepError(f"No approved artifact '{artifact_name}' on skill '{slug}'")
        try:
            bound, declared_keys = prepare_call(art, raw_params)
        except (ParamsValidationError, ValueError) as exc:
            raise FlowStepError(f"Invalid artifact params: {exc}") from exc
        code = build_call_artifact_code(art, bound)
        module = art.name
        source = art.code
        artifact_id = art.id
    finally:
        art_db.close()

    skill_secrets = _load_skill_secrets_sync(slug)
    env = scoped_secrets(skill_secrets, declared_keys)

    result = await execute_in_sandbox(code, "/tmp", env or None, artifacts={module: source})
    stdout = (result.get("stdout") or "").strip()
    stderr = (result.get("stderr") or "").strip()
    ok = result.get("returncode") == 0 and not stderr

    record_db = SessionLocal()
    try:
        row = record_db.get(SkillArtifact, artifact_id)
        if row:
            record_run(record_db, row, ok)
    finally:
        record_db.close()

    if not ok:
        raise FlowStepError(f"Artifact '{artifact_name}' failed: {(stderr or stdout)[:500]}")
    try:
        data = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise FlowStepError(f"Artifact '{artifact_name}' did not return JSON: {stdout[:200]}") from exc
    return data if isinstance(data, dict) else {"result": data}


def _load_skill_secrets_sync(slug: str) -> dict[str, str]:
    from main import _load_skill_secrets

    return _load_skill_secrets(slug)


async def _run_skill_agentic(slug: str, cfg: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Fallback path for skills with no artifact: the skill's own markdown
    drives one prompt-only LLM turn, coerced to output_schema.

    Deliberately no run_python/sandbox here — v1 proves the chaining contract
    (output_schema -> context_writes), not full agentic tool use inside a flow
    step. A skill whose instructions require code execution should ship an
    artifact and use artifact mode instead.
    """
    import anthropic
    from main import MODEL_GATEWAY_API_KEY, MODEL_GATEWAY_URL, MODEL, _load_skill

    instructions = _render_deep(cfg.get("instructions") or "", ctx)
    if not isinstance(instructions, str) or not instructions.strip():
        raise FlowStepError("agentic mode requires config.instructions")

    output_schema = cfg.get("output_schema") or {}
    skill_body = _load_skill(slug)
    system = (
        "You are executing one step of an automated flow on behalf of the flow owner. "
        "Follow this skill's instructions to answer the request below.\n\n"
        f"## Skill: {slug}\n\n{skill_body}\n\n"
        "Respond with ONLY a single JSON object matching this schema — no markdown "
        "fences, no commentary:\n" + json.dumps(output_schema)
    )

    client = anthropic.AsyncAnthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)
    try:
        response = await client.messages.create(
            model=MODEL,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": instructions}],
        )
    except Exception as exc:
        raise FlowStepError(f"Skill '{slug}' LLM call failed: {exc}") from exc

    raw = "".join(block.text for block in response.content if getattr(block, "type", "") == "text")
    data = _parse_json_object(raw)
    if not data:
        raise FlowStepError(f"Skill '{slug}' did not return valid JSON output: {raw[:200]}")
    return data




def default_vertical_slice_graph(
    *,
    intake_channel: str,
    delegate_user_id: str,
) -> dict[str, Any]:
    """Template graph from design doc — parameterize channel and delegate bot."""
    return {
        "id": "slice-intake-delegate-hitl-resolve",
        "version": 1,
        "nodes": [
            {
                "id": "tr_slack_mention",
                "type": "trigger.slack.mention",
                "config": {"channel_ids": [intake_channel] if intake_channel else []},
            },
            {
                "id": "ai_intake",
                "type": "ai_action",
                "config": {
                    "prompt_template": "Extract client_id and summary from context.slack.trigger_text.",
                    "output_schema": {
                        "type": "object",
                        "required": ["client_id", "summary"],
                        "properties": {
                            "client_id": {"type": "string"},
                            "summary": {"type": "string"},
                        },
                    },
                    "context_writes": ["client_id", "summary"],
                },
            },
            {
                "id": "del_investigator",
                "type": "delegate_wait",
                "config": {
                    "message_template": (
                        "<@{{delegate_user_id}}> please check client {{context.client_id}}: "
                        "{{context.summary}}. Tag <@{{bot_user_id}}> when done."
                    ),
                    "delegate_user_id": delegate_user_id,
                    "wait": {"kind": "slack_thread_reply", "timeout_hours": 24},
                    "context_writes": ["investigator_reply"],
                },
            },
            {
                "id": "ai_draft_resolution",
                "type": "ai_action",
                "config": {
                    "prompt_template": (
                        "Draft a short resolution message using investigator_reply and summary."
                    ),
                    "output_schema": {
                        "type": "object",
                        "required": ["draft_message"],
                        "properties": {"draft_message": {"type": "string"}},
                    },
                    "context_writes": ["draft_message"],
                },
            },
            {
                "id": "hitl_approve_reply",
                "type": "hitl.approve_message",
                "config": {
                    "title": "Approve reply to requester",
                    "payload_from": "context.draft_message",
                    "assignees": [],
                    "on_reject": "term_manual",
                },
            },
            {
                "id": "act_post_approved",
                "type": "action.slack_post",
                "config": {
                    "channel_from": "context.slack.channel_id",
                    "thread_ts_from": "context.slack.thread_ts",
                    "text_from": "context.approved_message",
                },
            },
            {
                "id": "term_resolved",
                "type": "terminal.resolve",
                "config": {"resolution_owner": "hybrid", "status": "resolved"},
            },
            {
                "id": "term_manual",
                "type": "terminal.escalate",
                "config": {"resolution_owner": "human", "status": "manual_review"},
            },
        ],
        "edges": [
            {"from": "tr_slack_mention", "to": "ai_intake"},
            {"from": "ai_intake", "to": "del_investigator"},
            {"from": "del_investigator", "to": "ai_draft_resolution"},
            {"from": "ai_draft_resolution", "to": "hitl_approve_reply"},
            {"from": "hitl_approve_reply", "to": "act_post_approved", "on": "approved"},
            {"from": "hitl_approve_reply", "to": "term_manual", "on": "rejected"},
            {"from": "act_post_approved", "to": "term_resolved"},
        ],
    }


def approve_gate(
    run_id: str,
    gate_id: str,
    *,
    approved_message: str,
    approved: bool,
) -> None:
    db = SessionLocal()
    try:
        run = db.get(WorkflowRun, run_id)
        if not run or run.status != "pending_hitl":
            raise ValueError("Run is not awaiting approval")
        gate = run.pending_gate
        if not gate or gate.get("id") != gate_id:
            raise ValueError("Unknown approval gate")
        ctx = run.context
        if approved:
            ctx["approved_message"] = approved_message
            run.set_context(ctx)
        db.commit()
    finally:
        db.close()
