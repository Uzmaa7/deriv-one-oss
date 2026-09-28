"""REST API for the platform agent (singleton) channel configuration.

All write endpoints are admin-only.  Read endpoints require any authenticated user.

Endpoints
─────────
GET  /api/platform-agent                              Platform agent record
GET  /api/platform-agent/channel-configs              List all channel configs
POST /api/platform-agent/channel-configs              Manually add a channel config
GET  /api/platform-agent/channel-configs/{channel_id} Get one config
PUT  /api/platform-agent/channel-configs/{channel_id} Edit config
DELETE /api/platform-agent/channel-configs/{channel_id} Remove config
POST /api/platform-agent/channel-configs/{channel_id}/regenerate-soul  Re-run LLM soul gen
GET  /api/platform-agent/channel-configs/{channel_id}/memories          Channel-scoped memories
POST /api/platform-agent/slack-credentials            Set Slack credentials manually (admin)
DELETE /api/platform-agent/slack-credentials          Clear Slack credentials (admin)
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from model_catalog import platform_default_model
from agent_channel_configs import (
    AgentChannelConfig,
    CHANNEL_TYPES,
    MEMORY_SCOPES,
    channel_config_to_dict,
    get_channel_config,
    get_platform_agent,
    upsert_channel_config,
)
from agents import Agent, AgentMemory, memory_to_dict
from database import SessionLocal
from secret_vault import SecretVaultError, SecretVaultMisconfiguredError, get_vault, vault_path_for_agent

logger = logging.getLogger(__name__)
router = APIRouter()

_MODEL_GATEWAY_API_KEY = os.environ.get("MODEL_GATEWAY_API_KEY")
_MODEL_GATEWAY_URL_RAW = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").rstrip("/")
_MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL_RAW[:-3] if _MODEL_GATEWAY_URL_RAW.endswith("/v1") else _MODEL_GATEWAY_URL_RAW
_MODEL = platform_default_model()

_MAX_SOUL = 8000
_MAX_DISPLAY_NAME = 120
_MAX_CHANNEL_ID = 32


# ── Auth helpers ──────────────────────────────────────────────────────────────

def _get_auth(request: Request):
    return getattr(request.state, "auth", None)


def _require_admin(request: Request):
    auth = _get_auth(request)
    if not auth or not getattr(auth, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return auth


def _require_authenticated(request: Request):
    auth = _get_auth(request)
    if not auth or not getattr(auth, "email", None):
        raise HTTPException(status_code=401, detail="Authentication required.")
    return auth


# ── Platform agent helper ──────────────────────────────────────────────────────

def _get_platform_agent_or_404():
    agent = get_platform_agent()
    if not agent:
        raise HTTPException(status_code=404, detail="Platform agent not initialised.")
    return agent


def _get_platform_agent_dict(agent) -> dict:
    import json as _json
    try:
        skill_ids = _json.loads(agent.skill_ids or "[]")
    except Exception:
        skill_ids = []
    return {
        "id": str(agent.id),
        "name": agent.name,
        "description": agent.description,
        "agent_type": agent.agent_type,
        "status": agent.status,
        "is_platform_agent": True,
        "channel_fallback_behavior": getattr(agent, "channel_fallback_behavior", "silent"),
        "slack_app_id": agent.slack_app_id,
        "slack_bot_user_id": agent.slack_bot_user_id,
        "skill_ids": skill_ids,
        "created_at": agent.created_at.isoformat() if agent.created_at else None,
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
    }


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class ChannelConfigCreate(BaseModel):
    channel_id: str = Field(..., max_length=_MAX_CHANNEL_ID)
    channel_name: Optional[str] = Field(None, max_length=200)
    channel_topic: Optional[str] = Field(None, max_length=500)
    display_name: Optional[str] = Field(None, max_length=_MAX_DISPLAY_NAME)
    soul_text: Optional[str] = Field(None, max_length=_MAX_SOUL)
    skill_ids: Optional[list[str]] = None
    guardrails: Optional[list[dict]] = None
    access_policy: Optional[dict] = None
    memory_scope: str = Field("channel", pattern="^(channel|global|user)$")
    join_behavior: Optional[dict] = None
    is_active: bool = True


class ChannelConfigUpdate(BaseModel):
    channel_name: Optional[str] = Field(None, max_length=200)
    channel_topic: Optional[str] = Field(None, max_length=500)
    display_name: Optional[str] = Field(None, max_length=_MAX_DISPLAY_NAME)
    soul_text: Optional[str] = Field(None, max_length=_MAX_SOUL)
    skill_ids: Optional[list[str]] = None
    guardrails: Optional[list[dict]] = None
    access_policy: Optional[dict] = None
    memory_scope: Optional[str] = Field(None, pattern="^(channel|global|user)$")
    join_behavior: Optional[dict] = None
    is_active: Optional[bool] = None


def _validate_channel_id(channel_id: str) -> str:
    channel_id = channel_id.strip()
    if not channel_id or len(channel_id) > _MAX_CHANNEL_ID:
        raise HTTPException(status_code=422, detail="Invalid channel_id.")
    import re
    if not re.match(r"^[A-Za-z0-9_\-]{1,32}$", channel_id):
        raise HTTPException(status_code=422, detail="channel_id contains invalid characters.")
    return channel_id


def _validate_access_policy(policy: Optional[dict]) -> Optional[dict]:
    if policy is None:
        return None
    if policy.get("mode") not in ("open", "allowlist"):
        raise HTTPException(status_code=422, detail="access_policy.mode must be 'open' or 'allowlist'.")
    emails = policy.get("emails") or []
    if not isinstance(emails, list) or len(emails) > 200:
        raise HTTPException(status_code=422, detail="access_policy.emails must be a list of up to 200 emails.")
    import re
    email_re = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    for e in emails:
        if not isinstance(e, str) or not email_re.match(e.strip()):
            raise HTTPException(status_code=422, detail=f"Invalid email in allowlist: {e!r}")
    policy["emails"] = [e.strip().lower() for e in emails]
    return policy


# ── GET /api/platform-agent ───────────────────────────────────────────────────

@router.get("/api/platform-agent")
def get_platform_agent_endpoint(request: Request):
    _require_authenticated(request)
    agent = _get_platform_agent_or_404()
    return _get_platform_agent_dict(agent)


# ── PATCH /api/platform-agent ─────────────────────────────────────────────────

class PlatformAgentUpdate(BaseModel):
    skill_ids: Optional[list[str]] = None
    name: Optional[str] = Field(None, max_length=120)
    description: Optional[str] = Field(None, max_length=500)
    soul_text: Optional[str] = Field(None, max_length=8000)


@router.patch("/api/platform-agent")
def patch_platform_agent(body: PlatformAgentUpdate, request: Request):
    _require_admin(request)
    agent = _get_platform_agent_or_404()

    if body.skill_ids is not None:
        import re
        slug_re = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")
        for s in body.skill_ids:
            if not isinstance(s, str) or not slug_re.match(s):
                raise HTTPException(status_code=422, detail=f"Invalid skill slug: {s!r}")

    db = SessionLocal()
    try:
        import json as _json
        db_agent = db.query(Agent).filter(Agent.id == agent.id).first()
        if not db_agent:
            raise HTTPException(status_code=404, detail="Platform agent not found.")
        if body.skill_ids is not None:
            db_agent.skill_ids = _json.dumps(body.skill_ids)
        if body.name is not None:
            db_agent.name = body.name
        if body.description is not None:
            db_agent.description = body.description
        if body.soul_text is not None:
            db_agent.soul_text = body.soul_text
        db.commit()
        db.refresh(db_agent)
        return _get_platform_agent_dict(db_agent)
    finally:
        db.close()


# ── GET /api/platform-agent/channel-configs ──────────────────────────────────

@router.get("/api/platform-agent/channel-configs")
def list_channel_configs(request: Request):
    _require_authenticated(request)
    agent = _get_platform_agent_or_404()
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentChannelConfig)
            .filter(AgentChannelConfig.agent_id == str(agent.id))
            .order_by(AgentChannelConfig.created_at.desc())
            .all()
        )
        return {"channel_configs": [channel_config_to_dict(r) for r in rows]}
    finally:
        db.close()


# ── POST /api/platform-agent/channel-configs ─────────────────────────────────

@router.post("/api/platform-agent/channel-configs", status_code=201)
def create_channel_config(body: ChannelConfigCreate, request: Request):
    _require_admin(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(body.channel_id)
    access_policy = _validate_access_policy(body.access_policy)

    # Validate skill_ids slugs
    if body.skill_ids is not None:
        import re
        slug_re = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")
        for s in body.skill_ids:
            if not isinstance(s, str) or not slug_re.match(s):
                raise HTTPException(status_code=422, detail=f"Invalid skill slug: {s!r}")

    cfg = upsert_channel_config(
        str(agent.id), channel_id,
        channel_name=body.channel_name,
        channel_topic=body.channel_topic,
        display_name=body.display_name,
        soul_text=body.soul_text,
        skill_ids=body.skill_ids,
        guardrails=body.guardrails,
        access_policy=access_policy,
        memory_scope=body.memory_scope,
        join_behavior=body.join_behavior,
        is_active=body.is_active,
        join_status="done",
    )
    if not cfg:
        raise HTTPException(status_code=500, detail="Failed to create channel config.")
    return channel_config_to_dict(cfg)


# ── GET /api/platform-agent/channel-configs/{channel_id} ─────────────────────

@router.get("/api/platform-agent/channel-configs/{channel_id}")
def get_channel_config_endpoint(channel_id: str, request: Request):
    _require_authenticated(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(channel_id)
    db = SessionLocal()
    try:
        cfg = (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == str(agent.id),
                AgentChannelConfig.channel_id == channel_id,
            )
            .first()
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Channel config not found.")
        return channel_config_to_dict(cfg)
    finally:
        db.close()


# ── PUT /api/platform-agent/channel-configs/{channel_id} ─────────────────────

@router.put("/api/platform-agent/channel-configs/{channel_id}")
def update_channel_config(channel_id: str, body: ChannelConfigUpdate, request: Request):
    _require_admin(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(channel_id)
    access_policy = _validate_access_policy(body.access_policy) if body.access_policy is not None else None

    if body.skill_ids is not None:
        import re
        slug_re = re.compile(r"^[a-z0-9][a-z0-9\-]{0,80}$")
        for s in body.skill_ids:
            if not isinstance(s, str) or not slug_re.match(s):
                raise HTTPException(status_code=422, detail=f"Invalid skill slug: {s!r}")

    db = SessionLocal()
    try:
        cfg = (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == str(agent.id),
                AgentChannelConfig.channel_id == channel_id,
            )
            .first()
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Channel config not found.")

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if body.channel_name is not None:
            cfg.channel_name = body.channel_name[:200]
        if body.channel_topic is not None:
            cfg.channel_topic = body.channel_topic[:500]
        if body.display_name is not None:
            cfg.display_name = body.display_name[:_MAX_DISPLAY_NAME]
        if body.soul_text is not None:
            cfg.soul_text = body.soul_text[:_MAX_SOUL] if body.soul_text else None
        if body.skill_ids is not None:
            cfg.skill_ids = json.dumps(body.skill_ids)
        if body.guardrails is not None:
            cfg.guardrails = body.guardrails
        if access_policy is not None:
            cfg.access_policy = access_policy
        if body.memory_scope is not None:
            cfg.memory_scope = body.memory_scope
        if body.join_behavior is not None:
            cfg.join_behavior = body.join_behavior
        if body.is_active is not None:
            cfg.is_active = body.is_active
        cfg.updated_at = now
        db.commit()
        db.refresh(cfg)
        return channel_config_to_dict(cfg)
    finally:
        db.close()


# ── DELETE /api/platform-agent/channel-configs/{channel_id} ──────────────────

@router.delete("/api/platform-agent/channel-configs/{channel_id}", status_code=204)
def delete_channel_config(channel_id: str, request: Request):
    _require_admin(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(channel_id)
    db = SessionLocal()
    try:
        cfg = (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == str(agent.id),
                AgentChannelConfig.channel_id == channel_id,
            )
            .first()
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Channel config not found.")
        db.delete(cfg)
        db.commit()
    finally:
        db.close()
    return Response(status_code=204)


# ── POST /api/platform-agent/channel-configs/{channel_id}/regenerate-soul ────

@router.post("/api/platform-agent/channel-configs/{channel_id}/regenerate-soul")
async def regenerate_soul(channel_id: str, request: Request):
    _require_admin(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(channel_id)

    db = SessionLocal()
    try:
        cfg = (
            db.query(AgentChannelConfig)
            .filter(
                AgentChannelConfig.agent_id == str(agent.id),
                AgentChannelConfig.channel_id == channel_id,
            )
            .first()
        )
        if not cfg:
            raise HTTPException(status_code=404, detail="Channel config not found.")
        channel_name = cfg.channel_name or channel_id
        channel_topic = cfg.channel_topic or ""
    finally:
        db.close()

    if not _MODEL_GATEWAY_API_KEY:
        raise HTTPException(status_code=503, detail="LLM not configured — cannot regenerate soul.")

    import anthropic
    _SOUL_GEN_PROMPT = (
        "You are configuring an AI assistant that will serve a Slack channel.\n"
        "Given the channel's name and topic, write a focused personality (soul_text) "
        "for this assistant. Be specific to the team's domain and work patterns. "
        "Write in second person (\"You are...\"). Maximum 400 words. "
        "Output only the soul text, no preamble.\n\n"
        f"Channel name: {channel_name}\n"
        f"Channel topic: {channel_topic or '(no topic set)'}\n"
    )
    client = anthropic.AsyncAnthropic(api_key=_MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    response = await client.messages.create(
        model=_MODEL,
        max_tokens=600,
        messages=[{"role": "user", "content": _SOUL_GEN_PROMPT}],
    )
    generated_soul = "".join(
        b.text for b in response.content if hasattr(b, "text") and b.text
    ).strip()

    if not generated_soul:
        raise HTTPException(status_code=500, detail="LLM returned empty soul text.")

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    updated = upsert_channel_config(
        str(agent.id), channel_id,
        soul_text=generated_soul,
        soul_generated_at=now,
    )
    if not updated:
        raise HTTPException(status_code=500, detail="Failed to save regenerated soul.")
    return channel_config_to_dict(updated)


# ── GET /api/platform-agent/channel-configs/{channel_id}/memories ─────────────

@router.get("/api/platform-agent/channel-configs/{channel_id}/memories")
def list_channel_memories(channel_id: str, request: Request, limit: int = 50):
    _require_authenticated(request)
    agent = _get_platform_agent_or_404()
    channel_id = _validate_channel_id(channel_id)
    if limit < 1 or limit > 200:
        limit = 50
    db = SessionLocal()
    try:
        rows = (
            db.query(AgentMemory)
            .filter(
                AgentMemory.agent_id == str(agent.id),
                AgentMemory.scope == "channel",
                AgentMemory.channel_id == channel_id,
            )
            .order_by(AgentMemory.updated_at.desc())
            .limit(limit)
            .all()
        )
        return {"memories": [memory_to_dict(m) for m in rows]}
    finally:
        db.close()


# ── POST /api/platform-agent/slack-credentials ────────────────────────────────

class SlackCredentialsIn(BaseModel):
    slack_app_id: str = Field(..., min_length=1, max_length=40, description="Slack App ID (Axxx...)")
    bot_token: str = Field(..., min_length=1, max_length=512, description="Bot token starting with xoxb-")
    signing_secret: str = Field(..., min_length=1, max_length=512, description="App signing secret")
    slack_team_id: Optional[str] = Field(None, max_length=20, description="Workspace/Team ID (Txxx...), optional")
    slack_bot_user_id: Optional[str] = Field(None, max_length=40, description="Bot User ID (Uxxx...), optional")


@router.post("/api/platform-agent/slack-credentials")
async def set_slack_credentials(
    body: SlackCredentialsIn,
    request: Request,
    _admin=Depends(_require_admin),
):
    """Store Slack credentials for the platform agent in the vault and update the agent row."""
    agent = get_platform_agent()
    if not agent:
        raise HTTPException(status_code=404, detail="Platform agent not found.")

    # Validate token format without exposing the value in logs
    if not body.bot_token.startswith("xoxb-"):
        raise HTTPException(status_code=422, detail="bot_token must start with 'xoxb-'.")

    agent_id = str(agent.id)

    try:
        xoxb_ref = get_vault().write(vault_path_for_agent(agent_id, "xoxb"), body.bot_token)
    except (SecretVaultMisconfiguredError, SecretVaultError) as exc:
        logger.error("Failed to store xoxb for platform agent: %s", exc)
        raise HTTPException(status_code=503, detail="Could not store bot token in secret vault.")

    try:
        signing_ref = get_vault().write(vault_path_for_agent(agent_id, "signing_secret"), body.signing_secret)
    except (SecretVaultMisconfiguredError, SecretVaultError) as exc:
        logger.error("Failed to store signing_secret for platform agent: %s", exc)
        raise HTTPException(status_code=503, detail="Could not store signing secret in secret vault.")

    db = SessionLocal()
    try:
        db_agent = db.query(Agent).filter(Agent.id == agent_id).first()
        if not db_agent:
            raise HTTPException(status_code=404, detail="Platform agent row not found in DB.")
        db_agent.slack_app_id = body.slack_app_id.strip()
        db_agent.xoxb_secret_ref = xoxb_ref
        db_agent.signing_secret_ref = signing_ref
        if body.slack_team_id:
            db_agent.slack_team_id = body.slack_team_id.strip()
        if body.slack_bot_user_id:
            db_agent.slack_bot_user_id = body.slack_bot_user_id.strip()
        db.commit()
        db.refresh(db_agent)
        return {
            "ok": True,
            "slack_app_id": db_agent.slack_app_id,
            "slack_team_id": db_agent.slack_team_id,
            "slack_bot_user_id": db_agent.slack_bot_user_id,
        }
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to update platform agent Slack fields")
        raise HTTPException(status_code=500, detail="Failed to update platform agent.") from exc
    finally:
        db.close()


# ── DELETE /api/platform-agent/slack-credentials ──────────────────────────────

@router.delete("/api/platform-agent/slack-credentials")
async def clear_slack_credentials(
    request: Request,
    _admin=Depends(_require_admin),
):
    """Clear Slack credentials from the platform agent (does not delete vault secrets)."""
    agent = get_platform_agent()
    if not agent:
        raise HTTPException(status_code=404, detail="Platform agent not found.")

    db = SessionLocal()
    try:
        db_agent = db.query(Agent).filter(Agent.id == str(agent.id)).first()
        if not db_agent:
            raise HTTPException(status_code=404, detail="Platform agent row not found.")
        db_agent.slack_app_id = None
        db_agent.slack_bot_user_id = None
        db_agent.slack_team_id = None
        db_agent.xoxb_secret_ref = None
        db_agent.signing_secret_ref = None
        db.commit()
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        logger.exception("Failed to clear platform agent Slack credentials")
        raise HTTPException(status_code=500, detail="Failed to clear credentials.") from exc
    finally:
        db.close()
