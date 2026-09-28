"""Skill artifact upload, review, approval, and listing."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from artifact_gate import review as gate_review
from model_catalog import platform_default_model
from artifact_models import (
    ARTIFACT_STATUS_APPROVED,
    ARTIFACT_STATUS_BLOCKED,
    ARTIFACT_STATUS_PENDING_APPROVAL,
    AUTHOR_HUMAN,
    EFFECT_READ,
    EFFECT_WRITE,
    SkillArtifact,
    SkillArtifactPreflight,
    artifact_to_dict,
    parse_manifest,
)
from artifact_runtime import (
    disable_superseded_artifact_versions,
    invalidate_artifact_cache,
    reconcile_superseded_artifact_versions,
)
from auth_policy import AuthContext
from cf_access import build_auth_context
from database import SessionLocal
from skill_collaborators import is_skill_owner_or_collaborator
from skill_submission_models import (
    REVIEW_STATUS_FAIL,
    REVIEW_STATUS_PASS,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REVIEWING,
)

logger = logging.getLogger(__name__)
router = APIRouter()

_MODEL_GATEWAY_URL_RAW = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").strip()
_MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL_RAW.rstrip("/")
if _MODEL_GATEWAY_URL.lower().endswith("/v1"):
    _MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL[:-3].rstrip("/")
MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
MODEL: str = platform_default_model()

_ARTIFACT_REVIEWER_PROMPT = """You review Python skill artifacts for security issues only.
Respond with JSON only: {"verdict": "pass"|"fail", "reason": "..."}
Fail on credential exfiltration, obfuscation, or behavior outside the stated manifest."""


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth(request: Request) -> AuthContext:
    cached = getattr(request.state, "auth", None)
    if isinstance(cached, AuthContext):
        return cached
    return build_auth_context(request.headers)


def _require_skill_editor(slug: str, auth, db: Session) -> None:
    if auth.is_admin:
        return
    email = (getattr(auth, "email", None) or "").strip().lower()
    if not is_skill_owner_or_collaborator(db, slug, auth.sub, email):
        raise HTTPException(status_code=403, detail="Not allowed to manage this skill.")


def _next_version(db: Session, slug: str, name: str) -> int:
    row = (
        db.query(SkillArtifact)
        .filter(SkillArtifact.slug == slug, SkillArtifact.name == name)
        .order_by(SkillArtifact.version.desc())
        .first()
    )
    return (row.version + 1) if row else 1


def _call_artifact_reviewer(code: str, manifest: dict) -> tuple[str, str]:
    if not MODEL_GATEWAY_API_KEY:
        return REVIEW_STATUS_FAIL, "LLM reviewer not configured."
    client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    user = json.dumps({"manifest": manifest, "code": code[:80_000]})
    try:
        resp = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=_ARTIFACT_REVIEWER_PROMPT,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        data = json.loads(text.strip().strip("`").replace("json\n", "", 1))
        verdict = data.get("verdict", "fail")
        reason = data.get("reason", "")
        if verdict == "pass":
            return REVIEW_STATUS_PASS, reason or "No issues detected."
        return REVIEW_STATUS_FAIL, reason or "Review failed."
    except Exception:
        logger.exception("artifact AI reviewer failed")
        return REVIEW_STATUS_FAIL, "Internal error during AI review."


def _run_preflight_review(preflight_id: str) -> None:
    db = SessionLocal()
    try:
        row = db.query(SkillArtifactPreflight).filter(SkillArtifactPreflight.id == preflight_id).first()
        if not row:
            return
        row.ai_review_status = REVIEW_STATUS_REVIEWING
        db.commit()
        manifest = json.loads(row.proposed_manifest_json)
        verdict, reason = _call_artifact_reviewer(row.proposed_code, manifest)
        row.ai_review_status = verdict
        row.ai_review_reason = reason
        row.reviewed_at = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def _create_artifact_from_preflight(
    db: Session,
    slug: str,
    pf: SkillArtifactPreflight,
    auth,
) -> SkillArtifact:
    manifest = json.loads(pf.proposed_manifest_json)
    gate = gate_review(pf.proposed_code, manifest)
    if not gate.ok:
        raise HTTPException(status_code=422, detail={"gate": json.loads(gate.to_json())})
    if pf.ai_review_status == REVIEW_STATUS_FAIL:
        raise HTTPException(status_code=422, detail=pf.ai_review_reason or "AI review failed.")

    name = manifest["name"]
    version = _next_version(db, slug, name)
    effect = manifest.get("effect_class", EFFECT_READ)
    status = ARTIFACT_STATUS_PENDING_APPROVAL
    if effect == EFFECT_READ and pf.ai_review_status == REVIEW_STATUS_PASS:
        status = ARTIFACT_STATUS_APPROVED

    code_hash = hashlib.sha256(pf.proposed_code.encode()).hexdigest()
    now = datetime.now(timezone.utc)
    row = SkillArtifact(
        id=str(uuid.uuid4()),
        slug=slug,
        name=name,
        version=version,
        code=pf.proposed_code,
        code_sha256=code_hash,
        manifest_json=pf.proposed_manifest_json,
        entrypoint=manifest.get("entrypoint", ""),
        params_schema_json=json.dumps(manifest.get("params_schema"))
        if manifest.get("params_schema")
        else None,
        declared_hosts_json=json.dumps(manifest.get("declared_hosts") or []),
        declared_secrets_json=json.dumps(manifest.get("declared_secrets") or []),
        effect_class=effect,
        api_summary=gate.api_summary,
        gate_status="pass",
        gate_report_json=gate.to_json(),
        ai_review_status=pf.ai_review_status,
        ai_review_reason=pf.ai_review_reason,
        status=status,
        author_kind=AUTHOR_HUMAN,
        uploader_sub=auth.sub,
        uploader_email=getattr(auth, "email", None),
        approved_at=now if status == ARTIFACT_STATUS_APPROVED else None,
        approved_by_sub=auth.sub if status == ARTIFACT_STATUS_APPROVED else None,
        approved_by_email=getattr(auth, "email", None) if status == ARTIFACT_STATUS_APPROVED else None,
        created_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/api/skills/{slug}/artifacts")
def list_artifacts(slug: str, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    rows = (
        db.query(SkillArtifact)
        .filter(SkillArtifact.slug == slug)
        .order_by(SkillArtifact.name, SkillArtifact.version.desc())
        .all()
    )
    return {"artifacts": [artifact_to_dict(r) for r in rows]}


@router.get("/api/skills/{slug}/artifacts/{name}/versions/{version}")
def get_artifact_detail(
    slug: str,
    name: str,
    version: int,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    row = (
        db.query(SkillArtifact)
        .filter(
            SkillArtifact.slug == slug,
            SkillArtifact.name == name,
            SkillArtifact.version == version,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    manifest = parse_manifest(row.manifest_json)
    detail = artifact_to_dict(row)
    detail["code"] = row.code
    detail["manifest"] = manifest
    detail["declared_hosts"] = json.loads(row.declared_hosts_json or "[]")
    detail["declared_secrets"] = json.loads(row.declared_secrets_json or "[]")
    if row.params_schema_json:
        try:
            detail["params_schema"] = json.loads(row.params_schema_json)
        except json.JSONDecodeError:
            detail["params_schema"] = None
    return {"artifact": detail}


@router.post("/api/skills/{slug}/artifacts/preflight")
async def preflight_artifact(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
    artifact_file: UploadFile = File(...),
    manifest: str = Form(...),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    raw = await artifact_file.read()
    if len(raw) > 120_000:
        raise HTTPException(status_code=413, detail="Artifact too large.")
    try:
        code = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=422, detail="Artifact must be UTF-8 Python.")

    try:
        manifest_obj = parse_manifest(manifest)
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    gate = gate_review(code, manifest_obj)
    content_hash = hashlib.sha256(code.encode()).hexdigest()
    manifest_hash = hashlib.sha256(manifest.encode()).hexdigest()

    pf = SkillArtifactPreflight(
        id=str(uuid.uuid4()),
        slug=slug,
        owner_sub=auth.sub,
        content_hash=content_hash,
        manifest_hash=manifest_hash,
        gate_status="pass" if gate.ok else "fail",
        gate_report_json=gate.to_json(),
        proposed_code=code,
        proposed_manifest_json=json.dumps(manifest_obj),
        created_at=datetime.now(timezone.utc),
    )
    if not gate.ok:
        pf.ai_review_status = REVIEW_STATUS_FAIL
        pf.ai_review_reason = "Static gate failed."
    db.add(pf)
    db.commit()
    db.refresh(pf)

    if gate.ok:
        background_tasks.add_task(_run_preflight_review, pf.id)

    return {
        "preflight_id": pf.id,
        "content_hash": content_hash,
        "manifest_hash": manifest_hash,
        "gate_status": pf.gate_status,
        "gate_report": json.loads(gate.to_json()),
        "ai_review_status": pf.ai_review_status,
    }


@router.get("/api/skills/{slug}/artifacts/preflight/{preflight_id}")
def get_artifact_preflight(
    slug: str,
    preflight_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    row = db.query(SkillArtifactPreflight).filter(SkillArtifactPreflight.id == preflight_id).first()
    if not row or row.slug != slug:
        raise HTTPException(status_code=404, detail="Preflight not found.")
    if row.owner_sub != auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden.")
    return {
        "preflight_id": row.id,
        "gate_status": row.gate_status,
        "gate_report": json.loads(row.gate_report_json or "{}"),
        "ai_review_status": row.ai_review_status,
        "ai_review_reason": row.ai_review_reason,
        "content_hash": row.content_hash,
    }


@router.post("/api/skills/{slug}/artifacts")
def submit_artifact(
    slug: str,
    request: Request,
    db: Session = Depends(_db),
    preflight_id: str = Form(...),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    pf = (
        db.query(SkillArtifactPreflight)
        .filter(
            SkillArtifactPreflight.id == preflight_id.strip(),
            SkillArtifactPreflight.owner_sub == auth.sub,
            SkillArtifactPreflight.slug == slug,
        )
        .first()
    )
    if not pf:
        raise HTTPException(status_code=404, detail="Preflight not found.")
    if pf.gate_status != "pass":
        raise HTTPException(status_code=422, detail="Gate did not pass.")
    if pf.ai_review_status not in (REVIEW_STATUS_PASS, REVIEW_STATUS_PENDING):
        if pf.ai_review_status == REVIEW_STATUS_FAIL:
            raise HTTPException(status_code=422, detail=pf.ai_review_reason or "AI review failed.")
    if pf.ai_review_status == REVIEW_STATUS_PENDING:
        raise HTTPException(status_code=409, detail="AI review still pending.")

    row = _create_artifact_from_preflight(db, slug, pf, auth)
    return {"artifact": artifact_to_dict(row)}


@router.post("/api/skills/{slug}/artifacts/{name}/versions/{version}/approve")
def approve_artifact(
    slug: str,
    name: str,
    version: int,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    row = (
        db.query(SkillArtifact)
        .filter(
            SkillArtifact.slug == slug,
            SkillArtifact.name == name,
            SkillArtifact.version == version,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    if row.uploader_sub == auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Approver must differ from uploader.")
    if row.effect_class != EFFECT_WRITE and row.status == ARTIFACT_STATUS_APPROVED:
        return {"artifact": artifact_to_dict(row)}

    now = datetime.now(timezone.utc)
    row.status = ARTIFACT_STATUS_APPROVED
    row.approved_by_sub = auth.sub
    row.approved_by_email = getattr(auth, "email", None)
    row.approved_at = now
    superseded = disable_superseded_artifact_versions(db, slug, name, version)
    db.commit()
    db.refresh(row)
    return {
        "artifact": artifact_to_dict(row),
        "superseded_disabled": superseded,
    }


@router.post("/api/skills/{slug}/artifacts/{name}/disable")
def disable_artifact(
    slug: str,
    name: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    rows = (
        db.query(SkillArtifact)
        .filter(SkillArtifact.slug == slug, SkillArtifact.name == name)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="Artifact not found.")
    for row in rows:
        row.disabled = True
        row.status = ARTIFACT_STATUS_BLOCKED
    invalidate_artifact_cache()
    db.commit()
    return {"disabled": name, "count": len(rows)}


@router.post("/api/skills/{slug}/artifacts/reconcile-superseded")
def reconcile_superseded(slug: str, request: Request, db: Session = Depends(_db)):
    """Disable older approved versions so only the latest per name stays active."""
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    disabled = reconcile_superseded_artifact_versions(db, slug)
    db.commit()
    return {"slug": slug, "superseded_disabled": disabled}
