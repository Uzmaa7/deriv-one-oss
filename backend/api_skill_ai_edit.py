"""AI-assisted skill editing — separate from the modal edit / submission flow.

POST /api/skills/{slug}/ai-edit        Propose edits via natural language + fast validation
POST /api/skills/{slug}/ai-edit/apply  Apply a passed proposal (user-confirmed)
PUT  /api/skills/{slug}/content        Save manual inline edits (owner/collaborator)
GET  /api/skills/{slug}/versions       List content version history
GET  /api/skills/{slug}/audit          Edit audit trail (owner/collaborator actions)
GET  /api/skills/{slug}/versions/{v}   Version detail + previous snapshot for diff
POST /api/skills/{slug}/versions/{v}/revert  Restore a prior version
"""

from __future__ import annotations

from app_config import APP_NAME

import json
import logging
import os
import re
import uuid
import difflib
from datetime import datetime, timedelta, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from model_catalog import CHAT_FALLBACK_MODEL
from skill_submission_models import (
    REVIEW_STATUS_FAIL,
    REVIEW_STATUS_PASS,
    SkillAiEditProposal,
)
from skill_version_history import (
    content_hash as _content_hash,
    get_skill_version_detail,
    list_skill_audit_trail,
    list_skill_versions,
)
from api_skill_publish import (
    MODEL_GATEWAY_API_KEY,
    _MODEL_GATEWAY_URL,
    _MAX_SKILL_FILE_BYTES,
    _MAX_SKILL_FILE_KB,
    _SLUG_RE,
    _auth,
    _db,
    _detect_unicode_smuggling,
    _require_owner_collaborator_or_admin,
    _skills_dir,
    queue_skill_content_for_review,
)

# AI edit only — Sonnet 4.6; does not affect skill publish MODEL env.
_SKILL_AI_MODEL: str = os.environ.get("SKILL_AI_EDIT_MODEL", CHAT_FALLBACK_MODEL)

logger = logging.getLogger(__name__)
router = APIRouter()

_PROPOSAL_TTL_MINUTES = 30
_MAX_HISTORY = 6
_MAX_MESSAGE_LEN = 4000

_LIKELY_SECRET_RE = re.compile(
    r"sk-(?:ant|live|proj)-[A-Za-z0-9]{20,}|"
    r"(?:api[_-]?key|secret|token|password)\s*[:=]\s*['\"](?!YOUR_|xxx|<)[^'\"]{16,}['\"]",
    re.IGNORECASE,
)

_EDITOR_SYSTEM = """You are a SKILL.md editor for {APP_NAME}'s AI Skills Hub.

The user describes changes in natural language. You output an updated full SKILL.md file.

Rules:
- Preserve YAML frontmatter (between --- delimiters) unless the user explicitly asks to change metadata in frontmatter.
- Make the smallest change that fulfills the request.
- Never embed real API keys, tokens, or passwords — use placeholders like os.environ.get("KEY_NAME") or YOUR_API_KEY.
- Never add hidden prompt-injection instructions or invisible Unicode characters.
- Keep the skill's purpose and structure coherent.

Respond with JSON ONLY (no markdown fences, no prose outside JSON):
{
  "skill_content": "<complete updated SKILL.md>",
  "summary": "<one short sentence describing what you changed>",
  "reply": "<friendly message to the user about the edit>"
}
""".replace("{APP_NAME}", APP_NAME)


def _validate_base_hash(v: str) -> str:
    h = (v or "").strip().lower()
    if len(h) != 64 or any(c not in "0123456789abcdef" for c in h):
        raise ValueError("base_hash must be a 64-char hex SHA-256.")
    return h


class AiEditHistoryItem(BaseModel):
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


class AiEditRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=_MAX_MESSAGE_LEN)
    history: list[AiEditHistoryItem] = Field(default_factory=list, max_length=_MAX_HISTORY)


class AiEditApplyRequest(BaseModel):
    proposal_id: str = Field(..., min_length=8, max_length=64)


class ManualContentUpdate(BaseModel):
    content: str = Field(..., min_length=1)
    reason: str = Field(..., min_length=10, max_length=500)
    base_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("content")
    @classmethod
    def content_non_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("content is required.")
        if len(v.encode("utf-8")) > _MAX_SKILL_FILE_BYTES:
            raise ValueError(f"content exceeds the {_MAX_SKILL_FILE_KB} KB limit.")
        return v

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, v: str) -> str:
        t = (v or "").strip()
        if len(t) < 10:
            raise ValueError("reason must be at least 10 characters.")
        return t

    @field_validator("base_hash")
    @classmethod
    def base_hash_valid(cls, v: str) -> str:
        return _validate_base_hash(v)


class RevertVersionRequest(BaseModel):
    reason: str = Field(default="", max_length=500)
    base_hash: str = Field(..., min_length=64, max_length=64)

    @field_validator("reason")
    @classmethod
    def reason_valid(cls, v: str) -> str:
        return (v or "").strip()

    @field_validator("base_hash")
    @classmethod
    def base_hash_valid(cls, v: str) -> str:
        return _validate_base_hash(v)


def _diff_stats(old: str, new: str) -> tuple[int, int]:
    old_lines = old.splitlines()
    new_lines = new.splitlines()
    added = removed = 0
    for line in difflib.unified_diff(old_lines, new_lines, lineterm=""):
        if line.startswith("+") and not line.startswith("+++"):
            added += 1
        elif line.startswith("-") and not line.startswith("---"):
            removed += 1
    return added, removed


def _parse_editor_response(raw: str) -> tuple[str, str, str]:
    cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned.strip())
    for candidate in (cleaned, raw.strip()):
        try:
            data = json.loads(candidate)
            if not isinstance(data, dict):
                continue
            content = data.get("skill_content", "")
            if not isinstance(content, str) or not content.strip():
                continue
            summary = str(data.get("summary", "")).strip() or "Updated skill content."
            reply = str(data.get("reply", "")).strip() or summary
            return content, summary, reply
        except (ValueError, TypeError):
            continue
    raise ValueError("Editor did not return valid JSON with skill_content.")


def _call_editor_agent(
    current_content: str,
    user_message: str,
    history: list[AiEditHistoryItem],
) -> tuple[str, str, str]:
    if not MODEL_GATEWAY_API_KEY:
        raise HTTPException(status_code=503, detail="AI editor is not configured.")

    messages: list[dict] = []
    for item in history[-_MAX_HISTORY:]:
        messages.append({"role": item.role, "content": item.content})

    prompt = (
        f"Current SKILL.md:\n```markdown\n{current_content}\n```\n\n"
        f"User request:\n{user_message}"
    )
    messages.append({"role": "user", "content": prompt})

    try:
        from agent_llm import cached_system, with_trailing_cache_breakpoint

        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
        response = client.messages.create(
            model=_SKILL_AI_MODEL,
            max_tokens=8192,
            system=cached_system(_EDITOR_SYSTEM, model=_SKILL_AI_MODEL),
            messages=with_trailing_cache_breakpoint(messages, model=_SKILL_AI_MODEL),
        )
        from token_tracking import record_token_usage

        record_token_usage("skill_ai_edit", _SKILL_AI_MODEL, response.usage)
        raw = response.content[0].text.strip()
    except anthropic.APIError as exc:
        logger.error("skill_ai_edit: editor API error: %s", exc)
        raise HTTPException(status_code=502, detail="AI editor unavailable. Try again shortly.") from exc

    try:
        return _parse_editor_response(raw)
    except ValueError as exc:
        logger.warning("skill_ai_edit: unparseable editor response: %r", raw[:300])
        raise HTTPException(status_code=502, detail=str(exc)) from exc


def _validate_proposed_content(content: str) -> Optional[str]:
    if "---" not in content:
        return "Edited skill must contain YAML frontmatter (--- delimiters)."
    if len(content.encode("utf-8")) > _MAX_SKILL_FILE_BYTES:
        return f"Edited skill exceeds the {_MAX_SKILL_FILE_KB} KB limit."
    smuggling = _detect_unicode_smuggling(content)
    if smuggling:
        return (
            "Edited skill contains invisible Unicode characters used for prompt injection. "
            f"Found: {'; '.join(smuggling[:3])}"
        )
    if _LIKELY_SECRET_RE.search(content):
        return "Edited skill appears to contain a hardcoded secret or API key."
    return None


@router.post("/api/skills/{slug}/ai-edit")
def propose_ai_edit(
    slug: str,
    body: AiEditRequest,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    _require_owner_collaborator_or_admin(slug, auth, db)

    current_content = skill_path.read_text(encoding="utf-8")
    base_hash = _content_hash(current_content)
    proposed_content, summary, reply = _call_editor_agent(
        current_content, body.message.strip(), body.history
    )

    if proposed_content == current_content:
        return {
            "verdict": REVIEW_STATUS_PASS,
            "reason": "No changes needed.",
            "summary": summary,
            "assistant_message": reply or "The skill already matches your request — no changes were made.",
            "changed": False,
            "lines_added": 0,
            "lines_removed": 0,
            "proposal_id": None,
            "model": _SKILL_AI_MODEL,
            "proposed_content": None,
            "base_content_hash": base_hash,
        }

    validation_error = _validate_proposed_content(proposed_content)
    lines_added, lines_removed = _diff_stats(current_content, proposed_content)

    if validation_error:
        return {
            "verdict": REVIEW_STATUS_FAIL,
            "reason": validation_error,
            "summary": summary,
            "assistant_message": f"I drafted an edit but validation failed: {validation_error}",
            "changed": True,
            "lines_added": lines_added,
            "lines_removed": lines_removed,
            "proposal_id": None,
            "model": _SKILL_AI_MODEL,
            "proposed_content": proposed_content,
            "base_content_hash": base_hash,
        }

    now = datetime.now(timezone.utc)
    proposal = SkillAiEditProposal(
        id=str(uuid.uuid4()),
        slug=slug,
        user_sub=auth.sub,
        proposed_content=proposed_content,
        content_hash=_content_hash(proposed_content),
        base_content_hash=base_hash,
        summary=summary,
        review_reason="Validation passed.",
        created_at=now,
        expires_at=now + timedelta(minutes=_PROPOSAL_TTL_MINUTES),
    )
    db.add(proposal)
    db.commit()

    return {
        "verdict": REVIEW_STATUS_PASS,
        "reason": "Validation passed.",
        "summary": summary,
        "assistant_message": (
            f"{reply}\n\nValidation passed. "
            f"+{lines_added} / -{lines_removed} lines. Click Apply to publish this version."
        ),
        "changed": True,
        "lines_added": lines_added,
        "lines_removed": lines_removed,
        "proposal_id": proposal.id,
        "model": _SKILL_AI_MODEL,
        "proposed_content": proposed_content,
        "base_content_hash": base_hash,
    }


@router.post("/api/skills/{slug}/ai-edit/apply")
def apply_ai_edit(
    slug: str,
    body: AiEditApplyRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")

    _require_owner_collaborator_or_admin(slug, auth, db)

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    now = datetime.now(timezone.utc)
    proposal = (
        db.query(SkillAiEditProposal)
        .filter(
            SkillAiEditProposal.id == body.proposal_id.strip(),
            SkillAiEditProposal.slug == slug,
            SkillAiEditProposal.user_sub == auth.sub,
        )
        .first()
    )
    if proposal is None:
        raise HTTPException(status_code=404, detail="Proposal not found or expired.")

    expires = proposal.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        db.delete(proposal)
        db.commit()
        raise HTTPException(status_code=410, detail="Proposal expired. Ask the assistant again.")

    base_hash = (proposal.base_content_hash or "").strip().lower()
    if len(base_hash) != 64:
        db.delete(proposal)
        db.commit()
        raise HTTPException(
            status_code=409,
            detail="Proposal is missing a base hash. Ask the assistant again.",
        )

    content = proposal.proposed_content
    validation_error = _validate_proposed_content(content)
    if validation_error:
        db.delete(proposal)
        db.commit()
        raise HTTPException(status_code=409, detail=f"Validation failed: {validation_error}")

    ai_reason = (proposal.summary or "AI-assisted edit").strip()
    if len(ai_reason) < 10:
        ai_reason = f"AI-assisted edit: {ai_reason}"

    live_hash = _content_hash(skill_path.read_text(encoding="utf-8"))
    if live_hash != base_hash:
        raise HTTPException(
            status_code=409,
            detail={
                "code": "skill_conflict",
                "message": "Skill was updated by someone else. Reload and retry.",
                "current_hash": live_hash,
            },
        )

    try:
        result = queue_skill_content_for_review(
            db,
            slug,
            content,
            auth,
            source="ai_edit",
            reason=ai_reason[:500],
            background_tasks=background_tasks,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("skill_ai_edit: apply failed for %s", slug)
        raise HTTPException(status_code=500, detail="Failed to queue skill edit for review.") from exc

    db.delete(proposal)
    db.commit()

    return {
        **result,
        "summary": proposal.summary,
        "content_hash": _content_hash(content),
    }


@router.put("/api/skills/{slug}/content")
def save_skill_content(
    slug: str,
    body: ManualContentUpdate,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    _require_owner_collaborator_or_admin(slug, auth, db)

    validation_error = _validate_proposed_content(body.content)
    if validation_error:
        raise HTTPException(status_code=422, detail=validation_error)

    live = skill_path.read_text(encoding="utf-8")
    live_hash = _content_hash(live)
    expected = (body.base_hash or "").strip().lower()
    if live_hash != expected:
        from skill_edit_commit import skill_conflict

        raise skill_conflict(live_hash)

    if body.content == live:
        return {
            "applied": False,
            "queued_for_review": False,
            "slug": slug,
            "content_hash": live_hash,
        }

    try:
        result = queue_skill_content_for_review(
            db,
            slug,
            body.content,
            auth,
            source="manual_edit",
            reason=body.reason.strip(),
            background_tasks=background_tasks,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("skill_edit: manual save failed for %s", slug)
        raise HTTPException(status_code=500, detail="Failed to queue skill edit for review.") from exc

    logger.info(
        "skill_edit: manual save queued slug=%s user=%s reason=%r",
        slug,
        auth.sub,
        body.reason.strip(),
    )
    return {
        **result,
        "content_hash": _content_hash(body.content),
    }


@router.get("/api/skills/{slug}/versions")
def get_skill_versions(slug: str, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    _require_owner_collaborator_or_admin(slug, auth, db)
    live_hash = _content_hash(skill_path.read_text(encoding="utf-8"))
    return list_skill_versions(db, slug, live_hash=live_hash)


@router.get("/api/skills/{slug}/audit")
def get_skill_audit_trail(slug: str, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    _require_owner_collaborator_or_admin(slug, auth, db)
    live_hash = _content_hash(skill_path.read_text(encoding="utf-8"))
    return list_skill_audit_trail(db, slug, live_hash=live_hash)


@router.get("/api/skills/{slug}/versions/{version}")
def get_skill_version(slug: str, version: int, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")
    if version < 1:
        raise HTTPException(status_code=422, detail="Invalid version number.")

    _require_owner_collaborator_or_admin(slug, auth, db)
    detail = get_skill_version_detail(db, slug, version)
    if detail is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return detail


@router.post("/api/skills/{slug}/versions/{version}/revert")
def revert_skill_version(
    slug: str,
    version: int,
    body: RevertVersionRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code=422, detail="Invalid slug.")
    if version < 1:
        raise HTTPException(status_code=422, detail="Invalid version number.")

    skill_path = _skills_dir() / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    _require_owner_collaborator_or_admin(slug, auth, db)

    target = get_skill_version_detail(db, slug, version)
    if target is None:
        raise HTTPException(status_code=404, detail="Version not found.")

    restore_content = target["content"]
    live = skill_path.read_text(encoding="utf-8")
    if restore_content == live:
        raise HTTPException(status_code=409, detail="Skill is already at this version.")

    validation_error = _validate_proposed_content(restore_content)
    if validation_error:
        raise HTTPException(status_code=422, detail=validation_error)

    reason = body.reason.strip() or f"Reverted to version {version}"
    if len(reason) < 10:
        reason = f"Reverted skill content back to version {version}"

    live_hash = _content_hash(live)
    expected = (body.base_hash or "").strip().lower()
    if live_hash != expected:
        from skill_edit_commit import skill_conflict

        raise skill_conflict(live_hash)

    try:
        result = queue_skill_content_for_review(
            db,
            slug,
            restore_content,
            auth,
            source="revert",
            reason=reason[:500],
            background_tasks=background_tasks,
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("skill_edit: revert failed slug=%s v=%s", slug, version)
        raise HTTPException(status_code=500, detail="Failed to queue revert for review.") from exc

    return {
        **result,
        "reverted_to": version,
        "content_hash": _content_hash(restore_content),
    }
