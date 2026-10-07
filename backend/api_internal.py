"""Internal endpoints for service-to-service communication.

These routes are NOT behind the Cloudflare Access JWT gate (service relays cannot
present one). The bundled nginx config returns 403 for ``/internal/*`` on the
public UI port; workers and relays must call the backend directly on the private
network (Docker network, VPC, or cluster-internal Service).

Current endpoints
─────────────────
GET /internal/secrets/{app_id}
    Returns the Slack signing secret for a registered Slack app.
    Used by the Cloud Run relay to verify X-Slack-Signature before forwarding
    events to POST /api/slack/events.

    Authentication: requires X-Invoke-Context header containing a HS256 JWT
    signed with INTERNAL_SIGNING_SECRET.  The relay must call
    sign_service_token("slack-relay-dmz") to obtain a token (valid 24 h by
    default) and refresh it before expiry.  Requests without a valid token
    receive HTTP 403.

    Response 200: { "signing_secret": "<value>" }
    Response 403: missing or invalid X-Invoke-Context token
    Response 404: app_id not registered or signing secret not stored

POST /internal/agents/invoke
    Agent-to-agent invocation with per-agent ACL.  See handler docstring.

Security notes
──────────────
- Exempt from CF Access JWT — protected at the network layer AND by the
  X-Invoke-Context JWT on every handler.
- Signing secret values are NEVER logged; only app IDs appear in logs.
- app_id is validated against a strict pattern to prevent injection.

DMZ relay migration
───────────────────
Relay must be updated to:
1. Obtain INTERNAL_SIGNING_SECRET from its own secret store.
2. Call sign_service_token("slack-relay-dmz") once per 23 h to get a fresh token.
3. Pass the token as: X-Invoke-Context: <token>
"""

from __future__ import annotations

import logging
import os
import re
import time

import jwt as _pyjwt
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from agents import Agent, agent_accepts_runtime_requests, load_agent_memories, loads as agent_loads, record_agent_exchange, upsert_agent_memory
from database import SessionLocal
from secret_vault import SecretVaultError, get_vault

logger = logging.getLogger(__name__)

router = APIRouter()

_SLACK_APP_ID_RE = re.compile(r"^A[A-Z0-9]{6,20}$")

# ── A2A invocation security ───────────────────────────────────────────────────

_INTERNAL_SIGNING_SECRET: str = os.environ.get("INTERNAL_SIGNING_SECRET", "")
_MAX_AGENT_DEPTH = 3


def sign_invocation_context(
    sender_email: str,
    source_agent_id: str,
    ttl_seconds: int = 300,
) -> str:
    """Return a signed JWT encoding the invocation context.

    The JWT embeds ``sender_email`` and ``source_agent_id`` so the
    ``/internal/agents/invoke`` endpoint can verify identity without trusting
    caller-supplied request body fields.  The signing secret is backend-only
    and is never injected into skill subprocesses.
    """
    if not _INTERNAL_SIGNING_SECRET:
        raise RuntimeError(
            "INTERNAL_SIGNING_SECRET is not set — A2A invocation is disabled."
        )
    now = int(time.time())
    payload = {
        "sender_email": sender_email,
        "source_agent_id": source_agent_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _pyjwt.encode(payload, _INTERNAL_SIGNING_SECRET, algorithm="HS256")


def sign_service_token(service_id: str, ttl_seconds: int = 86_400) -> str:
    """Return a signed JWT for a non-agent service (e.g. the Slack relay DMZ).

    Unlike sign_invocation_context, this token carries a ``service`` claim
    instead of ``sender_email`` + ``source_agent_id``.  Used by the
    /internal/secrets/ endpoint to authenticate the Cloud Run relay.

    Default TTL is 24 h — callers should refresh every ~23 h.
    """
    if not _INTERNAL_SIGNING_SECRET:
        raise RuntimeError(
            "INTERNAL_SIGNING_SECRET is not set — service tokens are disabled."
        )
    now = int(time.time())
    payload = {
        "service": service_id,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    return _pyjwt.encode(payload, _INTERNAL_SIGNING_SECRET, algorithm="HS256")


_SECRETS_SERVICE_ALLOWLIST = frozenset({"slack-relay-dmz"})


def _verify_invocation_context(token: str) -> dict:
    """Verify and decode the invocation context JWT.

    Raises ``jwt.InvalidTokenError`` (or subclass) on any failure.
    Returns the decoded claims dict on success.
    """
    return _pyjwt.decode(
        token,
        _INTERNAL_SIGNING_SECRET,
        algorithms=["HS256"],
    )


def _verify_secrets_service_token(token: str) -> dict:
    """Verify a relay-only service token for /internal/secrets."""
    claims = _verify_invocation_context(token)
    service = claims.get("service")
    if not isinstance(service, str) or not service.strip():
        raise _pyjwt.InvalidTokenError("service claim required")
    if service.strip() not in _SECRETS_SERVICE_ALLOWLIST:
        raise _pyjwt.InvalidTokenError("token service is not authorized for secrets access")
    if claims.get("sender_email") or claims.get("source_agent_id"):
        raise _pyjwt.InvalidTokenError("agent invocation tokens cannot access secrets")
    return claims


@router.get("/internal/secrets/{app_id}")
async def get_signing_secret(app_id: str, request: Request) -> JSONResponse:
    """Return the Slack signing secret for ``app_id``.

    Authentication
    ──────────────
    Caller must supply a valid HS256 service JWT in the ``X-Invoke-Context``
    header, signed with ``INTERNAL_SIGNING_SECRET`` and created by
    ``sign_service_token`` for an allowlisted relay service. Agent invocation
    tokens are rejected. Requests without a valid, non-expired token receive HTTP 403.

    Looks up the Agent row by ``slack_app_id``, then resolves the
    ``signing_secret_ref`` from the vault.
    """
    # ── Token verification ──────────────────────────────────────────────────
    ctx_header = request.headers.get("x-invoke-context", "")
    if not ctx_header:
        logger.warning("Secrets request for app_id=%s rejected: missing X-Invoke-Context", app_id)
        raise HTTPException(status_code=403, detail="X-Invoke-Context header required.")
    if not _INTERNAL_SIGNING_SECRET:
        logger.error("INTERNAL_SIGNING_SECRET not set — secrets endpoint rejected")
        raise HTTPException(status_code=503, detail="Service not configured.")
    try:
        _verify_secrets_service_token(ctx_header)
    except _pyjwt.ExpiredSignatureError:
        logger.warning("Secrets request for app_id=%s rejected: token expired", app_id)
        raise HTTPException(status_code=403, detail="Invocation token expired.")
    except _pyjwt.InvalidTokenError as exc:
        logger.warning("Secrets request for app_id=%s rejected: %s", app_id, exc)
        raise HTTPException(status_code=403, detail="Invalid invocation context token.")

    if not _SLACK_APP_ID_RE.match(app_id):
        raise HTTPException(status_code=400, detail="Invalid app_id format.")

    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.slack_app_id == app_id).first()
    finally:
        db.close()

    if not agent:
        logger.info("Signing secret requested for unknown app_id=%s", app_id)
        raise HTTPException(status_code=404, detail="App not registered.")

    ref = getattr(agent, "signing_secret_ref", None)
    if not ref:
        logger.warning("Agent %s (app=%s) has no signing_secret_ref stored", agent.id, app_id)
        raise HTTPException(status_code=404, detail="Signing secret not available for this app.")

    try:
        secret = get_vault().read(ref)
    except SecretVaultError:
        logger.exception("Failed to read signing secret for app_id=%s", app_id)
        raise HTTPException(status_code=503, detail="Could not retrieve signing secret.")

    return JSONResponse(content={"signing_secret": secret})


# ── Agent-to-Agent invocation ─────────────────────────────────────────────────

class AgentInvokeRequest(BaseModel):
    target_agent_id: str
    message: str


@router.post("/internal/agents/invoke")
async def invoke_agent(request: Request, body: AgentInvokeRequest) -> JSONResponse:
    """Invoke a target agent on behalf of a verified sender.

    Authentication
    ──────────────
    Callers must supply a signed per-invocation JWT in the
    ``X-Invoke-Context`` header.  The JWT is issued by the backend at skill
    execution time and encodes ``sender_email`` and ``source_agent_id``.
    These values are extracted from the verified JWT and are NOT trusted from
    the request body — this prevents skill code from forging a different
    sender identity.

    ACL
    ───
    1. source.can_invoke must contain target.id (if source_agent_id is present)
    2. target.can_be_invoked_by must contain source.id or ``"*"``
    3. sender must have access to all skills attached to the target agent
    4. Call depth (``X-Agent-Depth`` header) must be < _MAX_AGENT_DEPTH

    Returns
    ───────
    200: {reply, agent_id, duration_ms}
    403: {error: "access_denied", detail: "..."}
    404: {error: "agent_not_found"}
    429: {error: "max_depth"}
    """
    # ── Token verification ──────────────────────────────────────────────────
    ctx_header = request.headers.get("x-invoke-context", "")
    if not ctx_header:
        raise HTTPException(
            status_code=403,
            detail="Missing X-Invoke-Context header.",
        )
    if not _INTERNAL_SIGNING_SECRET:
        logger.error("INTERNAL_SIGNING_SECRET not set — A2A invocation rejected")
        raise HTTPException(
            status_code=503,
            detail="A2A invocation not configured on this instance.",
        )
    try:
        claims = _verify_invocation_context(ctx_header)
    except _pyjwt.ExpiredSignatureError:
        raise HTTPException(status_code=403, detail="Invocation token expired.")
    except _pyjwt.InvalidTokenError as exc:
        logger.warning("Invalid invocation context token: %s", exc)
        raise HTTPException(status_code=403, detail="Invalid invocation context token.")

    sender_email: str = str(claims.get("sender_email") or "")
    source_agent_id: str = str(claims.get("source_agent_id") or "")

    if not sender_email:
        raise HTTPException(status_code=403, detail="Invocation token missing sender_email.")

    # ── Depth guard ─────────────────────────────────────────────────────────
    try:
        depth = int(request.headers.get("x-agent-depth", "0"))
    except (ValueError, TypeError):
        depth = 0
    if depth >= _MAX_AGENT_DEPTH:
        return JSONResponse(
            status_code=429,
            content={"error": "max_depth", "detail": f"Maximum agent call depth ({_MAX_AGENT_DEPTH}) reached."},
        )

    # ── Input validation ────────────────────────────────────────────────────
    target_id = body.target_agent_id.strip()
    message = body.message.strip()
    if not target_id or len(target_id) > 200:
        raise HTTPException(status_code=422, detail="target_agent_id is required.")
    if not message or len(message) > 8000:
        raise HTTPException(status_code=422, detail="message is required and must be ≤ 8000 chars.")

    # ── Agent lookup ────────────────────────────────────────────────────────
    db = SessionLocal()
    try:
        # Accept UUID or exact name
        target_agent = (
            db.query(Agent).filter(Agent.id == target_id).first()
            or db.query(Agent).filter(Agent.name == target_id).first()
        )
    finally:
        db.close()

    if not target_agent:
        return JSONResponse(
            status_code=404,
            content={"error": "agent_not_found", "detail": f"No agent found for id/name: {target_id!r}"},
        )

    if not agent_accepts_runtime_requests(target_agent.status):
        logger.info(
            "A2A invoke rejected: target=%s status=%s",
            target_agent.id, target_agent.status,
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "agent_unavailable",
                "detail": f"Target agent is {target_agent.status} and cannot be invoked.",
            },
        )

    # ── can_invoke / can_be_invoked_by ACL ──────────────────────────────────
    if source_agent_id:
        source_can_invoke = agent_loads(
            db.query(Agent.can_invoke).filter(Agent.id == source_agent_id).scalar()
            if source_agent_id else None
        )
        if source_can_invoke is not None and target_agent.id not in source_can_invoke:
            logger.warning(
                "A2A ACL: source=%s not permitted to invoke target=%s",
                source_agent_id, target_agent.id,
            )
            return JSONResponse(
                status_code=403,
                content={"error": "access_denied", "detail": "Source agent is not permitted to invoke this target."},
            )

    invoked_by = agent_loads(target_agent.can_be_invoked_by or "[]")
    if invoked_by and "*" not in invoked_by and source_agent_id not in invoked_by:
        logger.warning(
            "A2A ACL: target=%s does not accept calls from source=%s",
            target_agent.id, source_agent_id,
        )
        return JSONResponse(
            status_code=403,
            content={"error": "access_denied", "detail": "Target agent does not accept calls from this source."},
        )

    # ── Skill access check for sender ───────────────────────────────────────
    from api_slack_events import (
        _build_memory_context,
        _call_llm,
        _check_agent_skill_access,
        _extract_and_store_memories,
        _load_agent_skills,
        _load_skill_secrets_for_agent,
        _sanitize_agent_reply_for_slack,
        _sign_invocation_context as _re_sign,
    )

    missing_slugs = await _check_agent_skill_access(sender_email, target_agent)
    if missing_slugs:
        missing_str = ", ".join(missing_slugs)
        logger.info(
            "A2A ACL: sender=%s lacks skill access on target=%s missing=%s",
            sender_email, target_agent.id, missing_str,
        )
        return JSONResponse(
            status_code=403,
            content={
                "error": "access_denied",
                "detail": f"Sender lacks access to skills required by target agent: {missing_str}",
            },
        )

    # ── Build system prompt for target agent ────────────────────────────────
    skill_context = _load_agent_skills(target_agent)
    soul = (target_agent.soul_text or "").strip()
    identity = (
        f"You are {target_agent.name}, a specialised assistant invoked by another agent. "
        "You have full access to your assigned tools and skills — never claim you lack access. "
        "FORMATTING RULES: use plain text or Slack mrkdwn. *bold* with single asterisks, "
        "• for bullets. Never use markdown tables. Keep replies focused and concise.\n\n"
        f"The original requesting human is: <{sender_email}>\n\n"
    )
    system_prompt = identity
    if soul:
        system_prompt += soul + "\n\n"

    # Inject active guardrails right after soul / before skills.
    active_guardrails = [g for g in (target_agent.guardrails or []) if g.get("enabled")]
    if active_guardrails:
        from api_agents import _resolve_guardrail_text
        rules = "\n".join(
            f"- {_resolve_guardrail_text(g)}"
            for g in active_guardrails
            if _resolve_guardrail_text(g)
        )
        if rules:
            system_prompt += "## Guardrails\nYou must follow these rules at all times:\n" + rules + "\n\n"

    if skill_context:
        system_prompt += skill_context

    # Inject persisted memories (agent-scope + user-scope) into the system prompt.
    mem_ctx = _build_memory_context(str(target_agent.id), sender_email)
    if mem_ctx:
        system_prompt += "\n\n" + mem_ctx

    # ── Build skill secrets env — re-sign context for any nested calls ───────
    skill_secrets = _load_skill_secrets_for_agent(target_agent)
    skill_secrets["SENDER_EMAIL"] = sender_email
    skill_secrets["AGENT_ID"] = str(target_agent.id)
    from platform_urls import platform_internal_base_url

    internal_base = platform_internal_base_url()
    skill_secrets["PLATFORM_INTERNAL_BASE_URL"] = internal_base
    skill_secrets["DERIV_ONE_BASE_URL"] = internal_base
    if _INTERNAL_SIGNING_SECRET:
        try:
            nested_token = _re_sign(
                sender_email=sender_email,
                source_agent_id=str(target_agent.id),
            )
            skill_secrets["DERIV_ONE_CTX_TOKEN"] = nested_token
        except Exception:
            logger.warning("Could not sign nested invocation context for agent %s", target_agent.id)

    # ── Execute LLM call ────────────────────────────────────────────────────
    t0 = time.time()
    llm_outcome = await _call_llm(
        system_prompt=system_prompt,
        user_content=message,
        agent=target_agent,
        history=[],
        skill_secrets=skill_secrets,
    )
    if llm_outcome.failure_kind:
        from agent_failure_alerts import notify_agent_failure

        await notify_agent_failure(
            target_agent,
            llm_outcome,
            source="agent_invoke",
        )
    reply = _sanitize_agent_reply_for_slack(llm_outcome.text)
    duration_ms = int((time.time() - t0) * 1000)

    source_agent_name: str | None = None
    if source_agent_id:
        _db = SessionLocal()
        try:
            src = _db.get(Agent, source_agent_id)
            if src:
                source_agent_name = src.name
        finally:
            _db.close()

    record_agent_exchange(
        agent_id=str(target_agent.id),
        user_message=message,
        assistant_message=reply,
        invoker_email=sender_email,
        trigger_type="a2a",
        source_agent_id=source_agent_id or None,
        source_agent_name=source_agent_name,
        duration_ms=duration_ms,
    )

    # Await memory extraction so subprocess-isolated invocations persist facts.
    await _extract_and_store_memories(
        agent_id=str(target_agent.id),
        user_email=sender_email,
        user_msg=message,
        agent_msg=reply,
    )

    logger.info(
        "A2A invoke: source=%s target=%s sender=%s depth=%d duration_ms=%d",
        source_agent_id, target_agent.id, sender_email, depth, duration_ms,
    )

    return JSONResponse(content={
        "reply": reply,
        "agent_id": str(target_agent.id),
        "agent_name": target_agent.name,
        "duration_ms": duration_ms,
    })
