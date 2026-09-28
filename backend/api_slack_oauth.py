"""HTTP API for Slack App auto-creation via App Configuration Token + App Manifest API.

Endpoints
─────────
POST /api/slack-oauth/analyze-scopes
    Run AI scope inference over an agent draft (description/soul/skills) and
    return a report: the locked base scopes + AI-proposed conditional scopes
    (with reasons/confidence). Creates a SlackScopeReport row whose id is the
    ``report_id`` used by the review UI and the final ``/start`` call.
POST /api/slack-oauth/analyze-scopes/request
    Request an additional scope not in the proposal. AI adjudicates allow/deny
    (default deny; catalog is the hard allowlist). Returns the updated approved
    set with per-scope reason.
POST /api/slack-oauth/start
    Accepts an agent draft (no tokens), creates a SlackOAuthSession, calls
    apps.manifest.create using the platform App Configuration Token, and
    returns the Slack workspace-install URL directly.  No per-user OAuth
    consent screen for app creation.  When ``report_id`` + ``approved_scopes``
    are supplied, only those (AI-reviewed, user-approved) scopes are written
    into the manifest and install URL; the base set is force-included.

GET  /api/slack-oauth/install-callback  (Slack → browser → us)
    Exchanges the install auth code for a bot token (xoxb), persists it to
    the vault, and creates the Agent row.  If Slack returns
    error=access_denied the session is marked pending_approval instead.

GET  /api/slack-oauth/session/{session_id}
    Status poll endpoint called by the frontend pending page.

POST /api/slack-oauth/session/{session_id}/retry-install
    Re-issues the Slack install URL for pending-approval sessions so users can
    complete setup after admin approval without restarting the whole flow.

Security
────────
- All endpoints are behind the existing Cloudflare Access JWT middleware.
- The ``state`` parameter in the install OAuth leg equals the session UUID;
  validated on the callback to prevent CSRF and cross-user injection.
- No token value is logged; only app IDs, error codes, and safe refs appear.
- Sessions expire after 30 minutes; stale rows are pruned at startup.
"""

from __future__ import annotations

from app_config import APP_NAME

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from agents import (
    MAX_DESCRIPTION,
    MAX_USECASE_DESCRIPTION,
    MAX_NAME,
    MAX_SOUL,
    SLACK_OAUTH_SESSION_TTL_MINUTES,
    SLACK_SCOPE_REPORT_TTL_MINUTES,
    TRIGGER_MODES,
    Agent,
    SlackOAuthSession,
    SlackScopeReport,
    dumps as json_dumps,
    new_oauth_session,
    sanitize_channels,
    sanitize_skill_ids,
    validate_agent_description,
    validate_usecase_description,
    validate_agent_name,
    validate_slack_agent_name,
    validate_slack_long_description,
)
from agent_llm import normalize_agent_llm_model, validate_gateway_api_key
from api_agents import _delete_avatar, _sanitise_guardrails
from database import SessionLocal
from slack_app_registry import _sync_slack_apps
from secret_vault import (
    SecretVaultError,
    SecretVaultMisconfiguredError,
    get_vault,
    vault_path_for_agent,
)
from slack_oauth import (
    SlackOAuthError,
    apply_framed_icon_to_slack_app,
    build_install_auth_url,
    create_slack_app,
    decode_agent_avatar_base64,
    exchange_install_code,
    generate_manifest,
    load_and_rotate_config_token,
    user_message_for_slack_oauth_error,
)
from slack_scope_catalog import (
    BASE_SCOPES,
    enforce_approved_set,
    is_base_scope,
    is_known_scope,
)
from scope_router import (
    ScopeProposal,
    adjudicate_scope_request,
    infer_scopes_for_agent,
)
from tenancy import request_owner_sub


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime for SQLite comparisons.

    SQLite strips timezone info on write and returns naive datetimes on read.
    Using an offset-aware datetime in comparisons raises TypeError.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


logger = logging.getLogger(__name__)
router = APIRouter()


def _app_base_url() -> str:
    return os.environ.get("APP_BASE_URL", "").rstrip("/")


def _install_redirect_uri(base_url: str) -> str:
    return f"{base_url}/api/slack-oauth/install-callback"


def _frontend_pending_url(base_url: str, session_id: str) -> str:
    return f"{base_url}/agents/oauth-pending?session={session_id}"


def _frontend_agent_url(base_url: str, agent_id: str) -> str:
    return f"{base_url}/agents/{agent_id}"


def _vault_store_error_detail(exc: Exception, *, secret_label: str) -> str:
    """User-facing detail when vault writes fail during auto-create."""
    if isinstance(exc, SecretVaultMisconfiguredError):
        return (
            f"Could not store your {secret_label}: the platform secret store is not configured. "
            "Contact support."
        )
    if isinstance(exc, SecretVaultError):
        return (
            f"Could not store your {secret_label}: the secret store rejected the write. "
            "Try again in a few minutes or contact support."
        )
    return (
        f"Could not securely store your {secret_label}. "
        "Try again in a few minutes or contact support."
    )


def _agent_record_error_detail(exc: Exception) -> str:
    """User-facing detail when the pending_oauth Agent row cannot be saved."""
    text = str(exc).lower()
    if "unique" in text or "duplicate" in text:
        return (
            "An agent with this name or Slack app may already exist. "
            "Check your agents list or try a more distinctive name."
        )
    if "foreign key" in text or "violates" in text:
        return "Could not save the agent record because related data is missing or invalid."
    return "Could not save the agent record to the database. Try again or contact support."


# ── Auth dependency ───────────────────────────────────────────────────────────

def _owner_sub(request: Request) -> str:
    return request_owner_sub(request)


# ── Session helpers ───────────────────────────────────────────────────────────

def _get_session_for_owner(
    db,
    session_id: str,
    owner_sub: str,
) -> SlackOAuthSession:
    """Load session; raise 404 if missing, expired, or owned by another user."""
    sess = db.query(SlackOAuthSession).filter(SlackOAuthSession.id == session_id).first()
    if not sess or sess.owner_sub != owner_sub:
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    # expires_at is stored as a naive datetime (TIMESTAMP WITHOUT TIME ZONE).
    # Always compare naive-to-naive to avoid TypeError on Python 3.
    expires = sess.expires_at.replace(tzinfo=None) if sess.expires_at.tzinfo else sess.expires_at
    if expires < _utcnow():
        raise HTTPException(status_code=404, detail="Session not found or expired.")
    return sess


# ── LLM (model gateway) client for scope inference ──────────────────────────

_MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
# normalize_gateway_base_url is defined in skill_router (not model_catalog).
# It strips a trailing /v1 so the Anthropic SDK appends /v1/messages exactly
# once; without it, MODEL_GATEWAY_URL=.../v1 produces a double /v1/v1 path that
# the gateway 404s.
try:
    from skill_router import normalize_gateway_base_url
    _MODEL_GATEWAY_URL: str = normalize_gateway_base_url(
        os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000")
    )
except Exception:  # pragma: no cover
    # Last-resort fallback: strip a trailing /v1 ourselves.
    _raw = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").strip().rstrip("/")
    _MODEL_GATEWAY_URL = _raw[:-3].rstrip("/") if _raw.lower().endswith("/v1") else _raw


def _scope_llm_client():  # -> anthropic.AsyncAnthropic | None
    """Return an Async Anthropic client pointed at the model gateway, or None."""
    if not _MODEL_GATEWAY_API_KEY:
        return None
    import anthropic
    return anthropic.AsyncAnthropic(api_key=_MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)


# ── Scope report helpers ──────────────────────────────────────────────────────

def _new_scope_report_row(owner_sub: str, agent_context: dict, proposals: list[ScopeProposal], notes: str) -> SlackScopeReport:
    expires = _utcnow() + timedelta(minutes=SLACK_SCOPE_REPORT_TTL_MINUTES)
    return SlackScopeReport(
        owner_sub=owner_sub,
        agent_context=json.dumps(agent_context),
        proposals=json.dumps([p.as_dict() for p in proposals]),
        approved_scopes=json.dumps([p.scope for p in proposals if not p.locked]),
        notes=(notes or "")[:512] or None,
        expires_at=expires,
    )


def _load_report_for_owner(db, report_id: str, owner_sub: str) -> SlackScopeReport:
    """Load a scope report; 404 if missing/expired/owned by another user."""
    rep = db.query(SlackScopeReport).filter(SlackScopeReport.id == report_id).first()
    if not rep or rep.owner_sub != owner_sub:
        raise HTTPException(status_code=404, detail="Scope report not found or expired.")
    expires = rep.expires_at.replace(tzinfo=None) if rep.expires_at.tzinfo else rep.expires_at
    if expires < _utcnow():
        raise HTTPException(status_code=404, detail="Scope report not found or expired.")
    return rep


def _report_agent_context(rep: SlackScopeReport) -> dict:
    try:
        return json.loads(rep.agent_context or "{}")
    except json.JSONDecodeError:
        return {}


def _report_proposals(rep: SlackScopeReport) -> list[dict]:
    try:
        return json.loads(rep.proposals or "[]")
    except json.JSONDecodeError:
        return []


def _report_approved(rep: SlackScopeReport) -> list[str]:
    try:
        return json.loads(rep.approved_scopes or "[]")
    except json.JSONDecodeError:
        return []


def _serialize_report(rep: SlackScopeReport) -> dict:
    """Public shape consumed by the review UI."""
    final, _ = enforce_approved_set(_report_approved(rep))
    return {
        "report_id": rep.id,
        "base": [s for s in final if is_base_scope(s)],
        "approved": final,
        "proposals": _report_proposals(rep),
        "notes": rep.notes,
        "expires_at": rep.expires_at.isoformat(),
    }


def _store_new_app_client_secret(agent_id: str, secret: str) -> Optional[str]:
    """Store the new app's client_secret in the vault; return the ref or None."""
    try:
        ref = get_vault().write(f"derivone-agents-{agent_id}-client-secret", secret)
        return ref
    except Exception:
        logger.exception("Could not store client_secret for agent_id=%s", agent_id)
        return None


# ── Input model ───────────────────────────────────────────────────────────────

class AgentDraft(BaseModel):
    name: str = Field(..., max_length=MAX_NAME)
    description: str = Field(..., min_length=1, max_length=MAX_DESCRIPTION)
    usecase_description: str = Field(..., min_length=1, max_length=MAX_USECASE_DESCRIPTION)
    skill_ids: list[str] = Field(default_factory=list)
    soul_text: Optional[str] = Field(None, max_length=MAX_SOUL)
    channel_ids: list[str] = Field(default_factory=list)
    trigger_mode: str = Field("mention")
    guardrails: list[dict] = Field(default_factory=list)
    # AI-reviewed, user-approved scope set (from the scope review step).
    # When present alongside report_id, only these scopes are written to the
    # app manifest. Omitted entirely by the legacy/manual path.
    approved_scopes: Optional[list[str]] = None
    report_id: Optional[str] = None
    llm_model: Optional[str] = Field(None, max_length=80)
    # Required BYOK gateway key for new agents — write-only; never persisted in draft JSON.
    gateway_api_key: str = Field(..., min_length=8, max_length=512)
    # Optional agent photo (raw base64 or data-URL). Write-only; never persisted
    # in the OAuth session draft. Max ~2 MB decoded / ~2.7 MB encoded.
    avatar_base64: Optional[str] = Field(default=None, max_length=3_000_000)


def _validate_draft(draft: AgentDraft) -> str:
    slack_name_err = validate_slack_agent_name(draft.name)
    if slack_name_err:
        raise HTTPException(status_code=422, detail=slack_name_err)
    desc_err = validate_agent_description(draft.description)
    if desc_err:
        raise HTTPException(status_code=422, detail=desc_err)
    slack_desc_err = validate_slack_long_description(draft.description)
    if slack_desc_err:
        raise HTTPException(status_code=422, detail=slack_desc_err)
    usecase_err = validate_usecase_description(draft.usecase_description)
    if usecase_err:
        raise HTTPException(status_code=422, detail=usecase_err)
    if draft.trigger_mode not in TRIGGER_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"trigger_mode must be one of: {', '.join(TRIGGER_MODES)}",
        )
    try:
        llm_model = normalize_agent_llm_model(draft.llm_model)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    # Intentionally omit gateway_api_key — secrets never land in agent_draft JSON.
    return json.dumps({
        "name": draft.name.strip(),
        "description": draft.description.strip(),
        "usecase_description": draft.usecase_description.strip(),
        "skill_ids": sanitize_skill_ids(draft.skill_ids),
        "soul_text": draft.soul_text or None,
        "channel_ids": sanitize_channels(draft.channel_ids),
        "trigger_mode": draft.trigger_mode,
        "guardrails": _sanitise_guardrails(draft.guardrails),
        "llm_model": llm_model,
    })


def _store_new_app_client_secret(agent_id: str, client_secret: str) -> Optional[str]:
    """Write a Slack app client_secret to the vault under the agent's path.

    Returns vault reference (str) on success, None on failure.
    This is called at agent creation time so the secret is accessible even
    after the 30-minute SlackOAuthSession TTL expires.
    """
    if not client_secret:
        return None
    try:
        return get_vault().write(
            vault_path_for_agent(agent_id, "client_secret"), client_secret
        )
    except Exception:
        logger.warning(
            "Could not pre-store client_secret for pending_oauth agent %s", agent_id
        )
        return None


# ── POST /api/slack-oauth/analyze-scopes ─────────────────────────────────────

class AnalyzeScopesRequest(BaseModel):
    name: str = Field(..., max_length=MAX_NAME)
    description: Optional[str] = Field(None, max_length=MAX_DESCRIPTION)
    usecase_description: Optional[str] = Field(None, max_length=MAX_USECASE_DESCRIPTION)
    soul_text: Optional[str] = Field(None, max_length=MAX_SOUL)
    skill_ids: list[str] = Field(default_factory=list)
    trigger_mode: Optional[str] = Field(None)


@router.post("/api/slack-oauth/analyze-scopes")
async def analyze_scopes(
    body: AnalyzeScopesRequest,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Run AI scope inference over an agent draft and return a scope report.

    The report carries the locked base scopes + AI-proposed conditional scopes
    (each with reason + confidence) and a ``report_id`` used by the review UI
    and the final ``/start`` call. Never fails the request on LLM errors —
    returns a base-only report instead.
    """
    name = (body.name or "").strip()
    name_error = validate_agent_name(name)
    if name_error:
        raise HTTPException(status_code=422, detail=name_error)
    description = (body.description or "").strip()
    usecase_description = (body.usecase_description or "").strip()
    soul_text = (body.soul_text or "").strip()
    skill_ids = sanitize_skill_ids(body.skill_ids)
    trigger_mode = (body.trigger_mode or "").strip() or "mention"
    if trigger_mode not in TRIGGER_MODES:
        raise HTTPException(
            status_code=422,
            detail=f"trigger_mode must be one of: {', '.join(TRIGGER_MODES)}",
        )
    usecase_err = validate_usecase_description(usecase_description)
    if usecase_err:
        raise HTTPException(status_code=422, detail=usecase_err)
    agent_context = {
        "name": name,
        "description": description or None,
        "usecase_description": usecase_description or None,
        "soul_text": soul_text or None,
        "skill_ids": skill_ids,
        "trigger_mode": trigger_mode,
    }

    client = _scope_llm_client()
    if client is None:
        # LLM unavailable — base-only report so the user can still proceed.
        proposals, notes = [], "AI analysis unavailable — only required base scopes will be requested."
    else:
        proposals, notes = await infer_scopes_for_agent(
            name,
            description,
            usecase_description,
            soul_text,
            skill_ids,
            trigger_mode,
            client,
        )

    db = SessionLocal()
    try:
        rep = _new_scope_report_row(owner_sub, agent_context, proposals, notes)
        db.add(rep)
        db.commit()
        db.refresh(rep)
        report_id = rep.id
        # ``approved`` defaults to the AI-proposed conditional scopes (locked
        # base is force-included at finalize time, not stored here).
        approved = [p.scope for p in proposals if not p.locked]
    except Exception:
        db.rollback()
        logger.exception("analyze-scopes: could not persist report")
        raise HTTPException(status_code=500, detail="Could not persist scope report.")
    finally:
        db.close()

    final, _ = enforce_approved_set(approved)
    return {
        "report_id": report_id,
        "proposals": [p.as_dict() for p in proposals],
        "base": list(BASE_SCOPES),
        "approved": final,
        "notes": notes,
    }


# ── POST /api/slack-oauth/analyze-scopes/request ─────────────────────────────

class ScopeRequestItem(BaseModel):
    scope: str = Field(..., max_length=80)


class ScopeRequestBody(BaseModel):
    report_id: str
    requested_scopes: list[str] = Field(default_factory=list)


@router.post("/api/slack-oauth/analyze-scopes/request")
async def request_additional_scopes(
    body: ScopeRequestBody,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Adjudicate user-requested extra scopes against the stored report.

    For each requested scope the AI decides allow/deny (default deny); only
    catalog scopes can ever be allowed. Returns the updated approved set and a
    per-scope verdict. The user may call this repeatedly.
    """
    db = SessionLocal()
    try:
        rep = _load_report_for_owner(db, body.report_id, owner_sub)
        ctx = _report_agent_context(rep)
        already_approved = set(_report_approved(rep))
    finally:
        db.close()

    name = ctx.get("name", "")
    description = ctx.get("description") or ""
    usecase_description = ctx.get("usecase_description") or ""
    soul_text = ctx.get("soul_text") or ""
    skill_ids = ctx.get("skill_ids") or []
    trigger_mode = ctx.get("trigger_mode") or "mention"

    client = _scope_llm_client()

    verdicts: list[dict] = []
    newly_allowed: list[str] = []
    for raw in body.requested_scopes or []:
        scope = (raw or "").strip()
        if not scope:
            continue
        if scope in already_approved or is_base_scope(scope):
            verdicts.append({
                "scope": scope, "allowed": True,
                "reason": "Already in the approved set." if scope in already_approved
                else "Required base scope — always granted.",
                "confidence": 1.0,
            })
            continue
        if not is_known_scope(scope):
            verdicts.append({
                "scope": scope, "allowed": False,
                "reason": f"{scope} is not an allowed scope.",
                "confidence": 0.0,
            })
            continue
        if client is None:
            verdicts.append({
                "scope": scope, "allowed": False,
                "reason": "AI adjudication unavailable — request could not be evaluated.",
                "confidence": 0.0,
            })
            continue
        allowed, reason, confidence = await adjudicate_scope_request(
            scope,
            name,
            description,
            usecase_description,
            soul_text,
            skill_ids,
            trigger_mode,
            client,
        )
        verdicts.append({
            "scope": scope, "allowed": allowed,
            "reason": reason, "confidence": round(confidence, 3),
        })
        if allowed:
            newly_allowed.append(scope)

    db = SessionLocal()
    try:
        rep = _load_report_for_owner(db, body.report_id, owner_sub)
        merged = list(_report_approved(rep)) + newly_allowed
        rep.approved_scopes = json.dumps(merged)
        db.commit()
        approved = merged
    except Exception:
        db.rollback()
        logger.exception("analyze-scopes/request: could not update report")
        raise HTTPException(status_code=500, detail="Could not update scope report.")
    finally:
        db.close()

    final, _ = enforce_approved_set(approved)
    return {
        "report_id": body.report_id,
        "verdicts": verdicts,
        "approved": final,
    }


# ── POST /api/slack-oauth/start ───────────────────────────────────────────────

@router.post("/api/slack-oauth/start")
async def start_oauth(
    draft: AgentDraft,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Create a Slack app via App Configuration Token and return the install URL.

    No per-user OAuth consent screen — the platform config token is used
    directly to call apps.manifest.create.  The returned redirect_url sends
    the user straight to the Slack workspace-install screen.
    """
    if not validate_gateway_api_key(draft.gateway_api_key):
        raise HTTPException(
            status_code=422,
            detail="gateway_api_key is required and must be a valid key",
        )
    # Hold the key in a local only — never write it into agent_draft JSON / session.
    byok_key = draft.gateway_api_key.strip()
    avatar_b64 = (draft.avatar_base64 or "").strip()
    try:
        object.__setattr__(draft, "gateway_api_key", "")
        object.__setattr__(draft, "avatar_base64", None)
    except Exception:
        pass

    base_url = _app_base_url()
    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="APP_BASE_URL is not configured — cannot build OAuth redirect URIs.",
        )

    draft_json = _validate_draft(draft)

    if avatar_b64:
        try:
            decode_agent_avatar_base64(avatar_b64)
        except SlackOAuthError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Resolve scopes before create so a duplicate start can reuse the same install URL.
    bot_scopes = _resolve_bot_scopes_for_start(draft, owner_sub)
    reused = _reuse_in_flight_oauth_start(
        owner_sub=owner_sub,
        agent_name=draft.name.strip(),
        base_url=base_url,
        bot_scopes=bot_scopes,
    )
    if reused:
        logger.info(
            "Reusing in-flight Slack auto-create session=%s app_id=%s (duplicate start)",
            reused.get("session_id"), reused.get("slack_app_id"),
        )
        return {
            "session_id": reused["session_id"],
            "redirect_url": reused["redirect_url"],
            "agent_id": reused.get("agent_id"),
            "icon_set": False,
        }

    db = SessionLocal()
    try:
        sess = new_oauth_session(owner_sub=owner_sub, agent_draft_json=draft_json)
        db.add(sess)
        db.commit()
        db.refresh(sess)
        session_id = sess.id
    finally:
        db.close()

    # Load the platform App Configuration Token (rotates automatically if expired).
    try:
        config_token = await load_and_rotate_config_token()
    except SlackOAuthError as exc:
        _fail_session(session_id, str(exc))
        raise HTTPException(
            status_code=503,
            detail=user_message_for_slack_oauth_error(exc, context="config_token"),
        )

    manifest = _build_manifest_from_draft_json(draft_json, bot_scopes=bot_scopes)
    try:
        app_info = await create_slack_app(config_token, manifest)
    except SlackOAuthError as exc:
        _fail_session(session_id, str(exc))
        detail = str(exc).strip() or user_message_for_slack_oauth_error(exc, context="create_app")
        raise HTTPException(status_code=422, detail=detail) from exc

    _advance_session_to_install(
        session_id=session_id,
        slack_app_id=app_info["app_id"],
        client_id=app_info["client_id"],
        client_secret=app_info["client_secret"],
        signing_secret=app_info.get("signing_secret", ""),
    )

    icon_set = await _set_auto_create_icon(app_info["app_id"], avatar_b64)
    avatar_b64 = ""

    # Create a preliminary agent row immediately so the user has a persistent
    # record even if they navigate away or the session expires before install.
    # Status is "pending_oauth" — upgraded to "registered" by install_callback.
    # We also store client_id (non-secret) and a vault ref for client_secret so
    # the install can be resumed even after the 30-min session TTL expires.
    draft_data = json.loads(draft_json or "{}")
    import uuid as _uuid_mod
    pre_agent_id = str(_uuid_mod.uuid4())

    # Persist app secrets to vault under the agent's path at creation time.
    signing_secret_ref_preview = None
    raw_signing = app_info.get("signing_secret", "")
    if raw_signing:
        try:
            signing_secret_ref_preview = get_vault().write(
                vault_path_for_agent(pre_agent_id, "signing_secret"), raw_signing
            )
        except Exception:
            logger.warning("Could not pre-store signing_secret for pending_oauth agent %s", pre_agent_id)

    # Store client_secret under agent path so resume-agent can use it even
    # after the session row is pruned.
    pre_client_secret_ref = _store_new_app_client_secret(pre_agent_id, app_info.get("client_secret", ""))

    # Required BYOK gateway key for new agents (existing agents keep platform key).
    try:
        gateway_ref = get_vault().write(
            vault_path_for_agent(pre_agent_id, "gateway"), byok_key
        )
    except Exception as exc:
        logger.exception("Failed to store gateway key for pending_oauth agent %s", pre_agent_id)
        _fail_session(session_id, "Could not securely store gateway API key.")
        raise HTTPException(
            status_code=502,
            detail=_vault_store_error_detail(exc, secret_label="gateway API key"),
        ) from exc
    byok_key = ""  # drop local copy after vault write

    creator_email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db2 = SessionLocal()
    try:
        pre_agent = Agent(
            id=pre_agent_id,
            name=draft_data.get("name", "Unnamed Agent"),
            description=draft_data.get("description"),
            usecase_description=draft_data.get("usecase_description"),
            owner_sub=owner_sub,
            creator_email=creator_email,
            slack_app_id=app_info["app_id"],
            slack_client_id=app_info.get("client_id", ""),   # non-secret; needed to rebuild install URL
            skill_ids=json_dumps(draft_data.get("skill_ids", [])),
            soul_text=draft_data.get("soul_text"),
            channel_ids=json_dumps(draft_data.get("channel_ids", [])),
            trigger_mode=draft_data.get("trigger_mode", "mention"),
            guardrails=_sanitise_guardrails(draft_data.get("guardrails", [])),
            llm_model=draft_data.get("llm_model"),
            gateway_api_key_ref=gateway_ref,
            agent_type="interactive",
            creation_method="auto_oauth",
            bot_scopes=json_dumps(bot_scopes) if bot_scopes else None,
            signing_secret_ref=signing_secret_ref_preview,
            client_secret_ref=pre_client_secret_ref,
            status="pending_oauth",
        )
        db2.add(pre_agent)
        db2.flush()
        # Store agent_id on the session so install_callback can update it
        sess2 = db2.query(SlackOAuthSession).filter(SlackOAuthSession.id == session_id).first()
        if sess2:
            sess2.agent_id = pre_agent_id
        db2.commit()
    except Exception as exc:
        logger.exception("Failed to create pending_oauth agent for session=%s", session_id)
        try:
            get_vault().delete(gateway_ref)
        except Exception:
            pass
        _fail_session(session_id, "Failed to create agent record.")
        raise HTTPException(
            status_code=502,
            detail=_agent_record_error_detail(exc),
        ) from exc
    finally:
        db2.close()

    install_scopes = manifest.get("oauth_config", {}).get("scopes", {}).get("bot")
    install_url = build_install_auth_url(
        new_app_client_id=app_info["client_id"],
        redirect_uri=_install_redirect_uri(base_url),
        state=session_id,
        bot_scopes=install_scopes,
    )
    return {
        "session_id": session_id,
        "redirect_url": install_url,
        "agent_id": pre_agent_id,
        "icon_set": icon_set,
    }


def _reuse_in_flight_oauth_start(
    owner_sub: str,
    agent_name: str,
    base_url: str,
    bot_scopes: Optional[list[str]],
) -> Optional[dict]:
    """Return an existing in-flight auto-create session for the same owner+name.

    A double-click on Approve creates two ``apps.manifest.create`` calls and
    two Slack apps. If a session already created the app, reuse it.
    """
    if not agent_name:
        return None
    db = SessionLocal()
    try:
        sessions = (
            db.query(SlackOAuthSession)
            .filter(
                SlackOAuthSession.owner_sub == owner_sub,
                SlackOAuthSession.step.in_(("install", "pending_approval")),
                SlackOAuthSession.expires_at > _utcnow(),
                SlackOAuthSession.slack_app_id.isnot(None),
                SlackOAuthSession.client_id.isnot(None),
            )
            .order_by(SlackOAuthSession.created_at.desc())
            .all()
        )
        for sess in sessions:
            try:
                draft = json.loads(sess.agent_draft or "{}")
            except Exception:
                continue
            if (draft.get("name") or "").strip() != agent_name:
                continue
            install_url = build_install_auth_url(
                new_app_client_id=sess.client_id,
                redirect_uri=_install_redirect_uri(base_url),
                state=sess.id,
                bot_scopes=bot_scopes,
            )
            return {
                "session_id": sess.id,
                "redirect_url": install_url,
                "agent_id": sess.agent_id,
                "slack_app_id": sess.slack_app_id,
            }
        return None
    finally:
        db.close()


async def _set_auto_create_icon(app_id: str, avatar_b64: str) -> bool:
    """Apply the framed agent photo to a newly created Slack app. Never raises."""
    if not avatar_b64 or not app_id:
        return False
    try:
        raw = decode_agent_avatar_base64(avatar_b64)
        await apply_framed_icon_to_slack_app(app_id, raw)
        return True
    except SlackOAuthError as exc:
        logger.warning(
            "Could not set Slack app icon during auto-create app_id=%s: %s",
            app_id, exc.slack_error or exc,
        )
        return False
    except Exception:
        logger.exception("Could not set Slack app icon during auto-create app_id=%s", app_id)
        return False


def _resolve_bot_scopes_for_start(draft: AgentDraft, owner_sub: str) -> Optional[list[str]]:
    """Resolve the per-agent bot scope set from the AI review report, if any.

    Returns None when no report is supplied (legacy/manual path → default scopes).
    When a ``report_id`` is supplied, loads the report (owner-checked), takes the
    user's ``approved_scopes`` selection, enforces that it is a SUBSET of the
    report's allowed set (user can only REMOVE — never add), force-includes the
    base scopes, and drops anything outside the catalog. Raises 422/409 on misuse.
    """
    if not draft.report_id:
        return None

    db = SessionLocal()
    try:
        rep = _load_report_for_owner(db, draft.report_id, owner_sub)
        ceiling = set(_report_approved(rep)) | set(BASE_SCOPES)
    finally:
        db.close()

    # Default to the report's approved set if the draft omits an explicit selection.
    selection = draft.approved_scopes if draft.approved_scopes is not None else _report_approved(rep)
    # The user may only REMOVE from the report's allowed set — never add.
    surplus = [s for s in selection if s not in ceiling]
    if surplus:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot add scopes that were not approved: {', '.join(surplus)}. "
                   "You can only remove scopes; to add one, request it via the scope review step.",
        )
    final, dropped = enforce_approved_set(selection)
    if dropped:
        logger.warning("start_oauth: dropped unknown scopes from report=%s: %s", draft.report_id, dropped)
    return final


def _build_manifest_from_draft_json(
    draft_json: str,
    bot_scopes: Optional[list[str]] = None,
) -> dict:
    d = json.loads(draft_json)
    return generate_manifest(
        agent_name=d.get("name", f"{APP_NAME} Agent"),
        description=d.get("description", ""),
        bot_scopes=bot_scopes,
    )


def _advance_session_to_install(
    session_id: str,
    slack_app_id: str,
    client_id: str,
    client_secret: str,
    signing_secret: str = "",
) -> None:
    """Update session step=install and store new app credentials."""
    secret_ref = _store_session_client_secret(session_id, client_secret)
    signing_secret_ref = _store_session_signing_secret(session_id, signing_secret) if signing_secret else None
    db = SessionLocal()
    try:
        sess = db.query(SlackOAuthSession).filter(SlackOAuthSession.id == session_id).first()
        if not sess:
            return
        sess.step = "install"
        sess.slack_app_id = slack_app_id
        sess.client_id = client_id
        sess.client_secret_ref = secret_ref  # may be None if vault unavailable
        sess.signing_secret_ref = signing_secret_ref
        db.commit()
    finally:
        db.close()


def _store_session_client_secret(session_id: str, client_secret: str) -> Optional[str]:
    """Write the client_secret to vault; return the ref or None on failure."""
    try:
        return get_vault().write(f"derivone-session-{session_id}-client-secret", client_secret)
    except Exception:
        logger.exception("Failed to store session client_secret for session=%s", session_id)
        return None


def _read_session_client_secret_by_ref(ref: str) -> Optional[str]:
    """Read the client_secret using the exact vault ref stored on the session."""
    try:
        return get_vault().read(ref)
    except Exception:
        logger.exception("Failed to read session client_secret ref=%s", ref)
        return None


def _delete_session_client_secret_by_ref(ref: Optional[str]) -> None:
    if not ref:
        return
    try:
        get_vault().delete(ref)
    except Exception:
        logger.warning("Could not delete session client_secret ref=%s", ref)


def _store_session_signing_secret(session_id: str, signing_secret: str) -> Optional[str]:
    """Write the signing_secret to vault; return the ref or None on failure."""
    try:
        return get_vault().write(f"derivone-session-{session_id}-signing-secret", signing_secret)
    except Exception:
        logger.exception("Failed to store session signing_secret for session=%s", session_id)
        return None


def _read_session_signing_secret_by_ref(ref: str) -> Optional[str]:
    """Read the signing_secret using the exact vault ref stored on the session."""
    try:
        return get_vault().read(ref)
    except Exception:
        logger.exception("Failed to read session signing_secret ref=%s", ref)
        return None


def _delete_session_signing_secret_by_ref(ref: Optional[str]) -> None:
    if not ref:
        return
    try:
        get_vault().delete(ref)
    except Exception:
        logger.warning("Could not delete session signing_secret ref=%s", ref)


def _gateway_ref_from_start_oauth(agent_id: str) -> Optional[str]:
    """Return BYOK ref when start_oauth stored a gateway secret for this agent id."""
    try:
        vault = get_vault()
        name = vault_path_for_agent(agent_id, "gateway")
        ref = vault.latest_version_ref(name)
        vault.read(ref)
        return ref
    except Exception:
        return None


def _fail_session(session_id: str, error_msg: str) -> None:
    db = SessionLocal()
    try:
        sess = db.query(SlackOAuthSession).filter(SlackOAuthSession.id == session_id).first()
        if sess:
            sess.step = "error"
            sess.error_msg = error_msg[:512]
            db.commit()
    finally:
        db.close()


# ── GET /api/slack-oauth/install-callback ────────────────────────────────────

@router.get("/api/slack-oauth/install-callback")
async def install_callback(
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Leg-2 callback: exchange install code → store xoxb → create Agent row."""
    params = request.query_params
    state = params.get("state", "")
    code = params.get("code", "")
    error = params.get("error", "")
    base_url = _app_base_url()

    if not state:
        raise HTTPException(status_code=400, detail="Missing state parameter.")

    db = SessionLocal()
    try:
        sess = _get_session_for_owner(db, state, owner_sub)
        if sess.step not in ("install", "pending_approval"):
            raise HTTPException(
                status_code=409,
                detail="Session is not awaiting workspace installation.",
            )
        session_id = sess.id
        slack_app_id = sess.slack_app_id
        new_client_id = sess.client_id
        client_secret_ref = sess.client_secret_ref
        session_signing_secret_ref = getattr(sess, "signing_secret_ref", None)
        draft_json = sess.agent_draft
    finally:
        db.close()

    if error:
        logger.info(
            "Slack install OAuth returned error=%s for session=%s", error, session_id
        )
        if error == "access_denied":
            # Non-admin user: admin approval is pending.
            db = SessionLocal()
            try:
                sess = db.query(SlackOAuthSession).filter(
                    SlackOAuthSession.id == session_id
                ).first()
                if sess:
                    sess.step = "pending_approval"
                    db.commit()
            finally:
                db.close()
        else:
            _fail_session(session_id, f"Slack install failed: {error}")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    if not code:
        _fail_session(session_id, "No authorisation code returned from Slack.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    if not client_secret_ref:
        _fail_session(session_id, "Session credentials unavailable; please restart.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )
    new_client_secret = _read_session_client_secret_by_ref(client_secret_ref)
    if not new_client_secret:
        _fail_session(session_id, "Session credentials could not be retrieved; please restart.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    try:
        install_info = await exchange_install_code(
            code=code,
            client_id=new_client_id,
            client_secret=new_client_secret,
            redirect_uri=_install_redirect_uri(base_url),
        )
    except (SlackOAuthError, Exception):
        logger.exception("Install code exchange failed for session=%s", session_id)
        _fail_session(session_id, "Failed to complete workspace installation.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    xoxb = install_info["xoxb"]
    team_id = install_info["team_id"]
    bot_user_id = install_info["bot_user_id"]

    if not xoxb or not slack_app_id:
        _fail_session(session_id, "Incomplete response from Slack.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    # Slack occasionally omits bot_user_id from the OAuth response.  Fall back
    # to auth.test so slack_bot_user_id is never stored as NULL — a missing
    # value silently breaks the member_joined_channel on-join flow.
    if not bot_user_id:
        try:
            async with httpx.AsyncClient(timeout=10.0) as _hc:
                _auth = await _hc.post(
                    "https://slack.com/api/auth.test",
                    headers={"Authorization": f"Bearer {xoxb}"},
                )
            _auth_data = _auth.json()
            bot_user_id = _auth_data.get("user_id", "")
            if bot_user_id:
                logger.info(
                    "Resolved bot_user_id=%s via auth.test for session=%s",
                    bot_user_id, session_id,
                )
            else:
                logger.warning(
                    "auth.test did not return user_id for session=%s: %s",
                    session_id, _auth_data.get("error", "unknown"),
                )
        except Exception:
            logger.exception("auth.test fallback failed for session=%s", session_id)

    # Load the pre-created pending_oauth agent id (set at start_oauth)
    db_pre = SessionLocal()
    try:
        sess_pre = db_pre.query(SlackOAuthSession).filter(
            SlackOAuthSession.id == session_id
        ).first()
        pre_agent_id = sess_pre.agent_id if sess_pre else None
    finally:
        db_pre.close()

    # Determine agent_id: reuse pre-created row if available, otherwise generate new
    import uuid as _uuid_mod
    agent_id = pre_agent_id or str(_uuid_mod.uuid4())

    # Persist xoxb to vault
    try:
        xoxb_ref = get_vault().write(
            vault_path_for_agent(agent_id, "xoxb"), xoxb
        )
    except (SecretVaultMisconfiguredError, SecretVaultError):
        logger.exception("Failed to store xoxb for auto-created agent session=%s", session_id)
        _fail_session(session_id, "Could not securely store bot token.")
        return RedirectResponse(
            url=_frontend_pending_url(base_url, session_id),
            status_code=302,
        )

    # Store client_secret for future OAuth rotation
    final_client_secret_ref = _store_new_app_client_secret(agent_id, new_client_secret)

    # Migrate signing_secret from session vault to agent vault
    agent_signing_secret_ref: Optional[str] = None
    if session_signing_secret_ref:
        raw_signing_secret = _read_session_signing_secret_by_ref(session_signing_secret_ref)
        if raw_signing_secret:
            try:
                agent_signing_secret_ref = get_vault().write(
                    vault_path_for_agent(agent_id, "signing_secret"), raw_signing_secret
                )
            except Exception:
                logger.exception(
                    "Failed to migrate signing_secret to agent vault for session=%s", session_id
                )
        _delete_session_signing_secret_by_ref(session_signing_secret_ref)

    # Load draft
    try:
        draft = json.loads(draft_json or "{}")
    except Exception:
        draft = {}

    # Update or create the Agent row
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first() if pre_agent_id else None
        if agent:
            # Promote the pending_oauth draft to a fully registered agent
            agent.slack_bot_user_id = bot_user_id or None
            agent.slack_team_id = team_id or None
            agent.xoxb_secret_ref = xoxb_ref
            agent.client_secret_ref = final_client_secret_ref
            if agent_signing_secret_ref:
                agent.signing_secret_ref = agent_signing_secret_ref
            agent.status = "registered"
            agent.pending_scopes = None  # scopes are now active after reinstall
            agent.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            _cb_email = getattr(getattr(request.state, "auth", None), "email", "") or ""
            agent = Agent(
                id=agent_id,
                name=draft.get("name", "Unnamed Agent"),
                description=draft.get("description"),
                usecase_description=draft.get("usecase_description"),
                owner_sub=owner_sub,
                creator_email=_cb_email,
                slack_app_id=slack_app_id,
                slack_bot_user_id=bot_user_id or None,
                slack_team_id=team_id or None,
                xoxb_secret_ref=xoxb_ref,
                xapp_secret_ref=None,
                client_secret_ref=final_client_secret_ref,
                signing_secret_ref=agent_signing_secret_ref,
                skill_ids=json_dumps(draft.get("skill_ids", [])),
                soul_text=draft.get("soul_text"),
                channel_ids=json_dumps(draft.get("channel_ids", [])),
                trigger_mode=draft.get("trigger_mode", "mention"),
                guardrails=_sanitise_guardrails(draft.get("guardrails", [])),
                llm_model=draft.get("llm_model"),
                gateway_api_key_ref=_gateway_ref_from_start_oauth(agent_id) if pre_agent_id else None,
                agent_type="interactive",
                creation_method="auto_oauth",
                status="registered",
            )
            db.add(agent)

        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            logger.error(
                "Agent conflict for slack_app_id=%s (session=%s)", slack_app_id, session_id,
            )
            _fail_session(session_id, "A conflicting agent already exists for this Slack app.")
            return RedirectResponse(
                url=_frontend_pending_url(base_url, session_id),
                status_code=302,
            )

        # Mark session done
        sess = db.query(SlackOAuthSession).filter(
            SlackOAuthSession.id == session_id
        ).first()
        if sess:
            sess.step = "done"
            sess.agent_id = agent_id

        # The app is now installed — drop the temporary self-hosted stored
        # avatar (used only while "Pending Slack install") and let
        # `_enrich_avatar`/SlackAppRecord.image_192 serve Slack's own icon.
        if agent.avatar_url and agent.avatar_url.startswith("/api/agents/"):
            _delete_avatar(agent.id)
            agent.avatar_url = None

        db.commit()

        try:
            _sync_slack_apps(db)
        except RuntimeError as exc:
            logger.warning("Slack registry sync after install failed: %s", exc)

    finally:
        db.close()

    # Clean up transient session secrets
    _delete_session_client_secret_by_ref(client_secret_ref)

    return RedirectResponse(
        url=_frontend_pending_url(base_url, session_id),
        status_code=302,
    )


# ── GET /api/slack-oauth/session/{session_id} ─────────────────────────────────

@router.get("/api/slack-oauth/session/{session_id}")
async def get_session_status(
    session_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Poll endpoint for the frontend pending page."""
    db = SessionLocal()
    try:
        sess = _get_session_for_owner(db, session_id, owner_sub)
        return {
            "session_id": sess.id,
            "step": sess.step,
            "slack_app_id": sess.slack_app_id,
            "agent_id": sess.agent_id,
            "error": sess.error_msg,
            "expires_at": sess.expires_at.isoformat(),
        }
    finally:
        db.close()


# ── POST /api/slack-oauth/session/{session_id}/retry-install ─────────────────

@router.post("/api/slack-oauth/session/{session_id}/retry-install")
async def retry_install(
    session_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Re-generate the Slack install URL for pending-approval sessions.

    Called when the user clicks "Complete Setup" after admin approval.
    Returns ``{ "redirect_url": ... }`` — the frontend navigates there.
    """
    base_url = _app_base_url()
    db = SessionLocal()
    try:
        sess = _get_session_for_owner(db, session_id, owner_sub)
        if sess.step != "pending_approval":
            raise HTTPException(
                status_code=409,
                detail=f"Session is in step '{sess.step}', not pending_approval.",
            )
        new_client_id = sess.client_id
        if not new_client_id:
            raise HTTPException(
                status_code=409,
                detail="Session is missing app client_id — please restart registration.",
            )
    finally:
        db.close()

    install_url = build_install_auth_url(
        new_app_client_id=new_client_id,
        redirect_uri=_install_redirect_uri(base_url),
        state=session_id,
    )
    return {"redirect_url": install_url}


# ── POST /api/slack-oauth/session/{session_id}/mark-pending-approval ─────────

@router.post("/api/slack-oauth/session/{session_id}/mark-pending-approval")
async def mark_pending_approval(
    session_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Transition a session from 'install' → 'pending_approval'.

    Called by the frontend when the user indicates Slack showed the admin
    approval waiting screen without redirecting back.  This unblocks the
    pending page UI so the 'Complete Setup' button appears.
    """
    db = SessionLocal()
    try:
        sess = _get_session_for_owner(db, session_id, owner_sub)
        if sess.step not in ("install",):
            raise HTTPException(
                status_code=409,
                detail=f"Session is already in step '{sess.step}'.",
            )
        sess.step = "pending_approval"
        db.commit()
        return {"status": "pending_approval"}
    finally:
        db.close()


# ── GET /api/slack-oauth/active-session ───────────────────────────────────────

@router.get("/api/slack-oauth/active-session")
async def get_active_session(
    agent_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Find the active (non-expired, non-done/error) OAuth session for an agent.

    Used by the agent detail page to resume an interrupted setup flow.
    Returns ``{"session_id": "..."}`` or 404 if no resumable session exists.
    """
    db = SessionLocal()
    try:
        sess = (
            db.query(SlackOAuthSession)
            .filter(
                SlackOAuthSession.agent_id == agent_id,
                SlackOAuthSession.owner_sub == owner_sub,
                SlackOAuthSession.step.notin_(["done", "error"]),
                SlackOAuthSession.expires_at > _utcnow(),
            )
            .order_by(SlackOAuthSession.created_at.desc())
            .first()
        )
        if not sess:
            raise HTTPException(status_code=404, detail="No active OAuth session found for this agent.")
        return {"session_id": sess.id, "step": sess.step}
    finally:
        db.close()


# ── POST /api/slack-oauth/resume-agent/{agent_id} ────────────────────────────

@router.post("/api/slack-oauth/resume-agent/{agent_id}")
async def resume_agent_oauth(
    agent_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Create a fresh OAuth session for a pending_oauth agent.

    Used when the original session has expired (30-min TTL) but the agent still
    exists in ``pending_oauth`` state.  The agent's stored ``slack_client_id``
    and ``client_secret_ref`` are used to rebuild the Slack install URL without
    needing the user to restart the whole flow.

    Returns ``{"session_id": "...", "redirect_url": "...", "agent_id": "..."}``.
    """
    base_url = _app_base_url()
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent or agent.owner_sub != owner_sub:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if agent.status != "pending_oauth":
            raise HTTPException(
                status_code=409,
                detail=f"Agent is in status '{agent.status}', not pending_oauth. Nothing to resume.",
            )
        if not agent.slack_client_id:
            raise HTTPException(
                status_code=409,
                detail="This agent predates install-resumption support. "
                       "Please decommission it and register a new agent.",
            )

        client_id = agent.slack_client_id
        # Prefer the agent-level secret ref; fall back to the most recent
        # active session's ref (covers agents created when vault_path_for_agent
        # did not yet accept 'client_secret' as a kind).
        client_secret_ref = agent.client_secret_ref
        if not client_secret_ref:
            fallback_sess = (
                db.query(SlackOAuthSession)
                .filter(
                    SlackOAuthSession.agent_id == agent_id,
                    SlackOAuthSession.client_secret_ref.isnot(None),
                )
                .order_by(SlackOAuthSession.created_at.desc())
                .first()
            )
            if fallback_sess:
                client_secret_ref = fallback_sess.client_secret_ref
                logger.info(
                    "resume-agent: using session client_secret_ref for agent=%s "
                    "(agent-level ref missing — likely created before vault fix)",
                    agent_id,
                )

        if not client_secret_ref:
            raise HTTPException(
                status_code=409,
                detail="No app credentials found for this agent. "
                       "Please decommission it and register a new agent.",
            )
    finally:
        db.close()

    # Read the client_secret from vault so we can re-provision a new session.
    client_secret = None
    try:
        client_secret = get_vault().read(client_secret_ref)
    except Exception:
        logger.exception("resume-agent: failed to read client_secret for agent=%s", agent_id)

    if not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve app credentials from the vault. "
                   "Please decommission and register a new agent.",
        )

    # Create a fresh session — gives the pending page a new, valid session_id
    # with a fresh 30-min window.
    import uuid as _uuid_mod
    new_session_id = str(_uuid_mod.uuid4())
    new_secret_ref = _store_session_client_secret(new_session_id, client_secret)

    db2 = SessionLocal()
    try:
        sess = SlackOAuthSession(
            id=new_session_id,
            owner_sub=owner_sub,
            step="pending_approval",   # go straight to pending_approval — admin already approved
            slack_app_id=agent.slack_app_id,
            client_id=client_id,
            client_secret_ref=new_secret_ref,
            agent_id=agent_id,
            expires_at=_utcnow() + timedelta(minutes=SLACK_OAUTH_SESSION_TTL_MINUTES),
        )
        db2.add(sess)
        db2.commit()
    except Exception:
        logger.exception("resume-agent: failed to create new session for agent=%s", agent_id)
        raise HTTPException(status_code=500, detail="Failed to create resume session.")
    finally:
        db2.close()

    install_url = build_install_auth_url(
        new_app_client_id=client_id,
        redirect_uri=_install_redirect_uri(base_url),
        state=new_session_id,
    )
    return {
        "session_id": new_session_id,
        "redirect_url": install_url,
        "agent_id": agent_id,
    }


# ── Shared helper: generate a reinstall URL for an agent ─────────────────────

async def _generate_reinstall_url(agent_id: str, caller_owner_sub: str, *, admin_bypass: bool = False) -> dict:
    """Create a new OAuth session for ``agent_id`` and return the Slack install URL.

    When ``admin_bypass=True`` the ownership check is skipped so admins can
    trigger reinstalls for agents they do not own.
    """
    base_url = _app_base_url()
    db = SessionLocal()
    try:
        agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if not admin_bypass and agent.owner_sub != caller_owner_sub:
            raise HTTPException(status_code=404, detail="Agent not found.")
        if agent.creation_method != "auto_oauth":
            raise HTTPException(
                status_code=409,
                detail="Only OAuth-created agents can be reinstalled via this endpoint.",
            )
        if agent.status not in ("registered", "pending_oauth"):
            raise HTTPException(
                status_code=409,
                detail=f"Agent is in status '{agent.status}' — cannot reinstall.",
            )
        if not agent.slack_client_id:
            raise HTTPException(
                status_code=409,
                detail="Agent is missing Slack app client_id. Please decommission and register a new agent.",
            )
        client_id = agent.slack_client_id
        client_secret_ref = agent.client_secret_ref
        if not client_secret_ref:
            fallback_sess = (
                db.query(SlackOAuthSession)
                .filter(
                    SlackOAuthSession.agent_id == agent_id,
                    SlackOAuthSession.client_secret_ref.isnot(None),
                )
                .order_by(SlackOAuthSession.created_at.desc())
                .first()
            )
            if fallback_sess:
                client_secret_ref = fallback_sess.client_secret_ref
        if not client_secret_ref:
            raise HTTPException(
                status_code=409,
                detail="No app credentials found. Please decommission and register a new agent.",
            )
    finally:
        db.close()

    try:
        client_secret = get_vault().read(client_secret_ref)
    except Exception:
        logger.exception("generate_reinstall_url: failed to read client_secret for agent=%s", agent_id)
        client_secret = None

    if not client_secret:
        raise HTTPException(
            status_code=500,
            detail="Could not retrieve app credentials. Please decommission and register a new agent.",
        )

    import uuid as _uuid_mod2
    new_session_id = str(_uuid_mod2.uuid4())
    new_secret_ref = _store_session_client_secret(new_session_id, client_secret)

    db2 = SessionLocal()
    try:
        sess = SlackOAuthSession(
            id=new_session_id,
            owner_sub=caller_owner_sub,
            step="install",
            slack_app_id=agent.slack_app_id,
            client_id=client_id,
            client_secret_ref=new_secret_ref,
            agent_id=agent_id,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=SLACK_OAUTH_SESSION_TTL_MINUTES),
        )
        db2.add(sess)
        db2.commit()
    except Exception:
        logger.exception("generate_reinstall_url: failed to create session for agent=%s", agent_id)
        raise HTTPException(status_code=500, detail="Failed to create reinstall session.")
    finally:
        db2.close()

    install_url = build_install_auth_url(
        new_app_client_id=client_id,
        redirect_uri=_install_redirect_uri(base_url),
        state=new_session_id,
    )
    return {"session_id": new_session_id, "redirect_url": install_url, "agent_id": agent_id}


# ── POST /api/slack-oauth/reinstall-agent/{agent_id} ─────────────────────────

@router.post("/api/slack-oauth/reinstall-agent/{agent_id}")
async def reinstall_agent_oauth(
    agent_id: str,
    request: Request,
    owner_sub: str = Depends(_owner_sub),
):
    """Generate a fresh Slack install URL for an already-registered agent.

    Used when the bot token needs to be refreshed — e.g. after adding new OAuth
    scopes to the manifest.  Creates a new SlackOAuthSession in ``install`` step
    so the install-callback will update the agent's ``xoxb_secret_ref`` with the
    new token once the user re-authorises.

    Returns ``{"session_id": "...", "redirect_url": "...", "agent_id": "..."}``.
    """
    return await _generate_reinstall_url(agent_id, owner_sub)


# ── Startup cleanup helper (called from main.py lifespan) ────────────────────

def prune_expired_sessions() -> int:
    """Delete expired SlackOAuthSession + SlackScopeReport rows; returns the count removed."""
    db = SessionLocal()
    try:
        now = _utcnow()
        deleted = (
            db.query(SlackOAuthSession)
            .filter(SlackOAuthSession.expires_at < now)
            .delete(synchronize_session=False)
        )
        deleted += (
            db.query(SlackScopeReport)
            .filter(SlackScopeReport.expires_at < now)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Pruned %d expired Slack OAuth sessions/scope reports", deleted)
        return deleted
    except Exception:
        logger.exception("Failed to prune expired Slack OAuth sessions")
        db.rollback()
        return 0
    finally:
        db.close()
