"""API endpoints for in-chat resource proposals (Skill/Automation/Lab).

Endpoints
─────────
GET   /api/proposals/{id}            Get a proposal's current state
PATCH /api/proposals/{id}             Overwrite a pending proposal's content (e.g. apply an AI-edit)
POST  /api/proposals/{id}/approve     Approve — creates the real resource (automation/lab)
                                       or, for skills, just flips status (real creation
                                       happens through the existing /publish form).
POST  /api/proposals/{id}/discard     Discard a pending proposal.
POST  /api/proposals/{id}/test-chat   Skill only — chat against the draft skill_md, no persistence.
POST  /api/proposals/{id}/ai-edit     Skill or automation — suggest an edit from a plain-language request.
POST  /api/proposals/{id}/test        Automation only — run it once via a real but inactive Automation row.

Rows are written by the propose_* tool handlers in main.py / workspace_openai_chat.py
during a chat turn; this router only reads/resolves them.
"""

from __future__ import annotations

from app_config import APP_NAME

import json
import logging
from datetime import datetime, timezone
from typing import Optional

import anthropic
from apscheduler.triggers.cron import CronTrigger
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from database import ResourceProposal, SessionLocal
from tenancy import get_tenant_or_404, request_owner_sub

logger = logging.getLogger(__name__)
router = APIRouter()

_MAX_HISTORY = 10
_MAX_MESSAGE_LEN = 4000


def deps_owner_sub(request: Request) -> str:
    return request_owner_sub(request)


class ChatHistoryItem(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def role_valid(cls, v: str) -> str:
        r = (v or "").strip().lower()
        if r not in ("user", "assistant"):
            raise ValueError("role must be user or assistant.")
        return r

    @field_validator("content")
    @classmethod
    def content_bounded(cls, v: str) -> str:
        t = (v or "").strip()
        if not t:
            raise ValueError("content is required.")
        if len(t) > 8000:
            raise ValueError("history message too long.")
        return t


class TurnRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE_LEN)
    history: list[ChatHistoryItem] = Field(default_factory=list, max_length=_MAX_HISTORY)


class PatchProposalRequest(BaseModel):
    content: dict


_AUTOMATION_EDITOR_SYSTEM = """You are editing the configuration of a draft scheduled Automation for \
{APP_NAME}'s Workspace. The user describes a change in natural language. You output the complete \
updated automation config as JSON.

Rules:
- Only change what the user asked for; keep everything else as-is.
- `schedule` must be a valid 5-field cron expression (minute hour day-of-month month day-of-week).
- Never invent a slack_channel or skill_id that wasn't already present or explicitly requested.

Respond with JSON ONLY (no markdown fences, no prose outside JSON):
{
  "content": {
    "name": "<name>",
    "instructions": "<what it should do>",
    "schedule": "<cron expression>",
    "is_active": true,
    "skill_id": null,
    "slack_channel": null
  },
  "summary": "<one short sentence describing what you changed>",
  "reply": "<friendly message to the user about the edit>"
}
""".replace("{APP_NAME}", APP_NAME)


def _validate_cron(schedule: str) -> Optional[str]:
    try:
        CronTrigger.from_crontab(schedule)
    except Exception:
        return f"'{schedule}' isn't a valid 5-field cron schedule."
    return None


def _call_automation_editor_agent(
    current_content: dict, user_message: str, history: list[ChatHistoryItem],
) -> tuple[dict, str, str]:
    from api_skill_publish import MODEL_GATEWAY_API_KEY, _MODEL_GATEWAY_URL
    from model_catalog import platform_default_model

    if not MODEL_GATEWAY_API_KEY:
        raise HTTPException(status_code=503, detail="AI editor is not configured.")
    model = platform_default_model()
    if not model:
        raise HTTPException(status_code=503, detail="MODEL is not configured.")

    messages: list[dict] = [{"role": h.role, "content": h.content} for h in history[-_MAX_HISTORY:]]
    prompt = (
        f"Current automation config:\n```json\n{json.dumps(current_content, indent=2)}\n```\n\n"
        f"User request:\n{user_message}"
    )
    messages.append({"role": "user", "content": prompt})

    try:
        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=_AUTOMATION_EDITOR_SYSTEM,
            messages=messages,
        )
        raw = response.content[0].text.strip()
    except anthropic.APIError as exc:
        logger.error("automation_ai_edit: editor API error: %s", exc)
        raise HTTPException(status_code=502, detail="AI editor unavailable. Try again shortly.") from exc

    try:
        parsed = json.loads(raw)
        content = parsed["content"]
        summary = str(parsed.get("summary", "")).strip()
        reply = str(parsed.get("reply", "")).strip()
    except Exception as exc:
        logger.warning("automation_ai_edit: unparseable editor response: %r", raw[:300])
        raise HTTPException(status_code=502, detail="AI editor returned an unexpected response.") from exc

    cron_error = _validate_cron(str(content.get("schedule", "")))
    if cron_error:
        raise HTTPException(status_code=502, detail=cron_error)

    return content, summary, reply


def _proposal_to_dict(p: ResourceProposal) -> dict:
    try:
        content = json.loads(p.content)
    except Exception:
        content = {}
    return {
        "id": p.id,
        "chat_id": p.chat_id,
        "kind": p.kind,
        "name": p.name,
        "content": content,
        "status": p.status,
        "created_resource_id": p.created_resource_id,
        "created_at": p.created_at.isoformat() if p.created_at else None,
    }


@router.get("/api/proposals/{proposal_id}")
def get_proposal(proposal_id: str, owner_sub: str = Depends(deps_owner_sub)):
    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        return _proposal_to_dict(p)
    finally:
        db.close()


@router.patch("/api/proposals/{proposal_id}")
def patch_proposal(
    proposal_id: str, body: PatchProposalRequest, owner_sub: str = Depends(deps_owner_sub),
):
    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.status != "pending":
            raise HTTPException(status_code=409, detail="Proposal already resolved.")
        p.content = json.dumps(body.content)
        p.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(p)
        return _proposal_to_dict(p)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("patch_proposal failed for proposal_id=%s", proposal_id)
        raise HTTPException(status_code=500, detail="Could not update proposal.")
    finally:
        db.close()


@router.post("/api/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub),
):
    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.status != "pending":
            raise HTTPException(status_code=409, detail="Proposal already resolved.")
        try:
            content = json.loads(p.content)
        except Exception:
            content = {}

        if p.kind == "skill":
            # No auto-submit — /publish's ACL-heavy manual form does the real work.
            p.status = "approved"

        elif p.kind == "automation":
            # Lazy import: main.py imports this router before AutomationCreate /
            # create_automation are defined further down the same module, so a
            # top-level import here would raise ImportError at server startup.
            from main import AutomationCreate, AutomationUpdate, create_automation, update_automation

            if p.created_resource_id:
                # A "Test run" already created this automation (inactive) — update
                # it in place to match the final proposal content rather than
                # creating a second row.
                update_automation(
                    p.created_resource_id,
                    AutomationUpdate(
                        name=p.name,
                        instructions=content.get("instructions", ""),
                        schedule=content.get("schedule", ""),
                        is_active=content.get("is_active", True),
                        skill_id=content.get("skill_id"),
                        slack_channel=content.get("slack_channel"),
                    ),
                    owner_sub=owner_sub,
                )
            else:
                body = AutomationCreate(
                    name=p.name,
                    instructions=content.get("instructions", ""),
                    schedule=content.get("schedule", ""),
                    is_active=content.get("is_active", True),
                    skill_id=content.get("skill_id"),
                    slack_channel=content.get("slack_channel"),
                )
                result = create_automation(body=body, request=request, owner_sub=owner_sub)
                p.created_resource_id = result["id"]
            p.status = "approved"

        elif p.kind == "lab":
            from api_apps import AppCreateRequest, create_app

            body = AppCreateRequest(
                name=p.name,
                description=content.get("description", ""),
                skill_ids=content.get("skill_ids") or None,
            )
            result = await create_app(body=body, request=request, owner_sub=owner_sub)
            p.created_resource_id = result["id"]
            p.status = "approved"

        else:
            raise HTTPException(status_code=400, detail=f"Unknown proposal kind: {p.kind}")

        p.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(p)
        return _proposal_to_dict(p)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("approve_proposal failed for proposal_id=%s", proposal_id)
        raise HTTPException(status_code=500, detail="Could not approve proposal.")
    finally:
        db.close()


@router.post("/api/proposals/{proposal_id}/discard")
def discard_proposal(proposal_id: str, owner_sub: str = Depends(deps_owner_sub)):
    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.status != "pending":
            raise HTTPException(status_code=409, detail="Proposal already resolved.")
        if p.kind == "automation" and p.created_resource_id:
            # A "Test run" created a real (inactive) automation before the user
            # decided to discard — clean it up so nothing orphaned is left behind.
            from main import delete_automation

            try:
                delete_automation(p.created_resource_id, owner_sub=owner_sub)
            except HTTPException:
                pass  # already gone, e.g. deleted in another tab
            p.created_resource_id = None
        p.status = "discarded"
        p.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(p)
        return _proposal_to_dict(p)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("discard_proposal failed for proposal_id=%s", proposal_id)
        raise HTTPException(status_code=500, detail="Could not discard proposal.")
    finally:
        db.close()


@router.post("/api/proposals/{proposal_id}/test-chat")
def test_chat_proposal(
    proposal_id: str, body: TurnRequest, owner_sub: str = Depends(deps_owner_sub),
):
    """Skill only — chat against the draft skill_md as system prompt. Fully
    stateless: no tools, no persistence, no ACL/secrets (nothing to resolve
    for a skill that isn't published yet)."""
    from api_skill_publish import MODEL_GATEWAY_API_KEY, _MODEL_GATEWAY_URL
    from model_catalog import platform_default_model

    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.kind != "skill":
            raise HTTPException(status_code=400, detail="Test chat is only available for skill drafts.")
        try:
            content = json.loads(p.content)
        except Exception:
            content = {}
        skill_md = content.get("skill_md", "")
    finally:
        db.close()

    if not MODEL_GATEWAY_API_KEY:
        raise HTTPException(status_code=503, detail="Test chat is not configured.")
    model = platform_default_model()
    if not model:
        raise HTTPException(status_code=503, detail="MODEL is not configured.")

    messages: list[dict] = [{"role": h.role, "content": h.content} for h in body.history[-_MAX_HISTORY:]]
    messages.append({"role": "user", "content": body.message})

    try:
        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
        response = client.messages.create(
            model=model,
            max_tokens=2048,
            system=skill_md or "You are a helpful assistant.",
            messages=messages,
        )
        reply = response.content[0].text.strip()
    except anthropic.APIError as exc:
        logger.error("test_chat_proposal: API error: %s", exc)
        raise HTTPException(status_code=502, detail="Test chat unavailable. Try again shortly.") from exc

    return {"reply": reply}


@router.post("/api/proposals/{proposal_id}/ai-edit")
def ai_edit_proposal(
    proposal_id: str, body: TurnRequest, owner_sub: str = Depends(deps_owner_sub),
):
    """Suggest an edit to a pending skill or automation proposal's content, from
    a plain-language request. Never applies the suggestion — the caller must
    PATCH the proposal to apply it."""
    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.status != "pending":
            raise HTTPException(status_code=409, detail="Proposal already resolved.")
        if p.kind not in ("skill", "automation"):
            raise HTTPException(status_code=400, detail="AI-edit is only available for skill or automation drafts.")
        try:
            content = json.loads(p.content)
        except Exception:
            content = {}
    finally:
        db.close()

    if p.kind == "skill":
        from api_skill_ai_edit import _call_editor_agent, _validate_proposed_content

        current_md = content.get("skill_md", "")
        proposed_md, summary, reply = _call_editor_agent(current_md, body.message, body.history)
        validation_error = _validate_proposed_content(proposed_md)
        if validation_error:
            raise HTTPException(status_code=502, detail=validation_error)
        proposed_content = {**content, "skill_md": proposed_md}
    else:
        proposed_content, summary, reply = _call_automation_editor_agent(content, body.message, body.history)

    return {"content": proposed_content, "summary": summary, "reply": reply}


@router.post("/api/proposals/{proposal_id}/test")
async def test_proposal(
    proposal_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub),
):
    """Automation only — creates a real but inactive Automation (if not already
    created by a prior test) and triggers the existing async run pipeline."""
    from main import AutomationCreate, create_automation, trigger_run

    db = SessionLocal()
    try:
        p = get_tenant_or_404(db, ResourceProposal, proposal_id, owner_sub, "Proposal not found")
        if p.kind != "automation":
            raise HTTPException(status_code=400, detail="Test run is only available for automation drafts.")
        if p.status != "pending":
            raise HTTPException(status_code=409, detail="Proposal already resolved.")
        try:
            content = json.loads(p.content)
        except Exception:
            content = {}

        if not p.created_resource_id:
            body = AutomationCreate(
                name=p.name,
                instructions=content.get("instructions", ""),
                schedule=content.get("schedule", ""),
                is_active=False,
                skill_id=content.get("skill_id"),
                slack_channel=content.get("slack_channel"),
            )
            result = create_automation(body=body, request=request, owner_sub=owner_sub)
            p.created_resource_id = result["id"]
            p.updated_at = datetime.now(timezone.utc)
            db.commit()
        automation_id = p.created_resource_id
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        logger.exception("test_proposal failed for proposal_id=%s", proposal_id)
        raise HTTPException(status_code=500, detail="Could not start test run.")
    finally:
        db.close()

    trigger_run(automation_id, owner_sub=owner_sub)
    return {"automation_id": automation_id}
