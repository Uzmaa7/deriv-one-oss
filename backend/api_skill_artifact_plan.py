"""AI artifact planning — suggest discrete artifacts before code generation."""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from api_skill_artifacts import _require_skill_editor
from api_skill_artifacts_suggest import _load_skill_text, _run_suggest_job, _skills_dir
from model_catalog import platform_default_model
from artifact_models import (
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    REC_STATUS_DISMISSED,
    REC_STATUS_GENERATING,
    REC_STATUS_PROPOSED,
    REC_STATUS_PUBLISHED,
    SkillArtifactPlan,
    SkillArtifactRecommendation,
    SkillArtifactSuggestJob,
    plan_to_dict,
    recommendation_to_dict,
)
from auth_policy import AuthContext
from cf_access import build_auth_context
from database import SessionLocal

logger = logging.getLogger(__name__)
router = APIRouter()

_MODEL_GATEWAY_URL_RAW = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").strip()
_MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL_RAW.rstrip("/")
if _MODEL_GATEWAY_URL.lower().endswith("/v1"):
    _MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL[:-3].rstrip("/")
MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
MODEL: str = platform_default_model()

_PLANNER_SYSTEM = """You analyze agent skill documentation and propose discrete Python artifacts.

Artifacts are small gate-approved modules (one entrypoint, one API surface or tight operation).
Do NOT propose one artifact that implements an entire multi-phase workflow, Slack routing, or HITL flows.
Prefer separate read helpers per GET endpoint and separate write helpers per POST/PATCH when needed.
Skip Slack posting unless a single tiny API wrapper is clearly justified.

Return JSON only:
{
  "artifacts": [
    {
      "name": "snake_case_module_name",
      "description": "One sentence: what call_artifact params do and when agents use it.",
      "effect_class": "read" | "write"
    }
  ]
}
Rules:
- 3 to 8 artifacts maximum.
- name must match ^[a-z][a-z0-9_]{2,39}$
- effect_class write only when the artifact performs POST/PATCH/DELETE that mutates remote state.
- Do not duplicate the same API in multiple names.
"""


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


def _skill_markdown(slug: str) -> str:
    path = _skills_dir() / slug / "skill.md"
    if not path.is_file():
        raise FileNotFoundError(slug)
    return path.read_text(encoding="utf-8")


def _parse_plan_response(raw: str) -> list[dict]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("planner response must be an object")
    items = data.get("artifacts")
    if not isinstance(items, list):
        raise ValueError("artifacts array required")
    out: list[dict] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        desc = str(item.get("description", "")).strip()
        effect = str(item.get("effect_class", "read")).strip().lower()
        if not re.match(r"^[a-z][a-z0-9_]{2,39}$", name) or not desc:
            continue
        if effect not in ("read", "write"):
            effect = "read"
        out.append({"name": name, "description": desc, "effect_class": effect, "sort_order": i})
    if not out:
        raise ValueError("no valid artifact suggestions in response")
    return out


def _run_plan_job(plan_id: str) -> None:
    db = SessionLocal()
    try:
        plan = db.query(SkillArtifactPlan).filter(SkillArtifactPlan.id == plan_id).first()
        if plan is None:
            return
        plan.status = JOB_STATUS_RUNNING
        plan.status_detail = "Reading skill…"
        db.commit()

        skill_md = _skill_markdown(plan.slug)
        if not MODEL_GATEWAY_API_KEY:
            plan.status = JOB_STATUS_FAILED
            plan.error_message = "LLM not configured"
            plan.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
        user = f"Skill slug: {plan.slug}\n\nSkill documentation:\n{skill_md[:96_000]}"
        if len(skill_md) > 96_000:
            user += "\n[truncated]\n"

        plan.status_detail = "Analyzing APIs and workflows…"
        db.commit()

        resp = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=_PLANNER_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        items = _parse_plan_response(text)

        for item in items:
            db.add(
                SkillArtifactRecommendation(
                    id=str(uuid.uuid4()),
                    plan_id=plan.id,
                    slug=plan.slug,
                    name=item["name"],
                    description=item["description"],
                    effect_class=item["effect_class"],
                    sort_order=item["sort_order"],
                    status=REC_STATUS_PROPOSED,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )
        plan.status = JOB_STATUS_DONE
        plan.status_detail = f"{len(items)} suggestion(s) ready"
        plan.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        logger.exception("artifact plan %s failed", plan_id)
        try:
            plan = db.query(SkillArtifactPlan).filter(SkillArtifactPlan.id == plan_id).first()
            if plan:
                plan.status = JOB_STATUS_FAILED
                plan.error_message = str(exc)
                plan.status_detail = plan.error_message
                plan.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _latest_plan_with_recs(db: Session, slug: str) -> tuple[SkillArtifactPlan | None, list[SkillArtifactRecommendation]]:
    plan = (
        db.query(SkillArtifactPlan)
        .filter(SkillArtifactPlan.slug == slug, SkillArtifactPlan.status == JOB_STATUS_DONE)
        .order_by(SkillArtifactPlan.completed_at.desc())
        .first()
    )
    if not plan:
        return None, []
    recs = (
        db.query(SkillArtifactRecommendation)
        .filter(
            SkillArtifactRecommendation.plan_id == plan.id,
            SkillArtifactRecommendation.status != REC_STATUS_DISMISSED,
        )
        .order_by(SkillArtifactRecommendation.sort_order)
        .all()
    )
    return plan, recs


@router.get("/api/skills/{slug}/artifact-recommendations")
def list_artifact_recommendations(slug: str, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    plan, recs = _latest_plan_with_recs(db, slug)
    running = (
        db.query(SkillArtifactPlan)
        .filter(
            SkillArtifactPlan.slug == slug,
            SkillArtifactPlan.status.in_([JOB_STATUS_PENDING, JOB_STATUS_RUNNING]),
        )
        .order_by(SkillArtifactPlan.created_at.desc())
        .first()
    )
    return {
        "plan": plan_to_dict(plan) if plan else None,
        "running_plan": plan_to_dict(running) if running else None,
        "recommendations": [recommendation_to_dict(r) for r in recs],
    }


@router.post("/api/skills/{slug}/artifact-plans")
def start_artifact_plan(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    _load_skill_text(slug)

    plan = SkillArtifactPlan(
        id=str(uuid.uuid4()),
        slug=slug,
        owner_sub=auth.sub,
        status=JOB_STATUS_PENDING,
        status_detail="Queued",
        created_at=datetime.now(timezone.utc),
    )
    db.add(plan)
    db.commit()
    db.refresh(plan)
    background_tasks.add_task(_run_plan_job, plan.id)
    return {"plan_id": plan.id, "status": plan.status}


@router.get("/api/skills/{slug}/artifact-plans/{plan_id}")
def get_artifact_plan(slug: str, plan_id: str, request: Request, db: Session = Depends(_db)):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    plan = (
        db.query(SkillArtifactPlan)
        .filter(SkillArtifactPlan.id == plan_id.strip(), SkillArtifactPlan.slug == slug)
        .first()
    )
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found.")
    recs = (
        db.query(SkillArtifactRecommendation)
        .filter(SkillArtifactRecommendation.plan_id == plan.id)
        .order_by(SkillArtifactRecommendation.sort_order)
        .all()
    )
    return {
        "plan": plan_to_dict(plan),
        "recommendations": [recommendation_to_dict(r) for r in recs],
    }


@router.post("/api/skills/{slug}/artifact-recommendations/{rec_id}/generate")
def generate_from_recommendation(
    slug: str,
    rec_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    rec = (
        db.query(SkillArtifactRecommendation)
        .filter(
            SkillArtifactRecommendation.id == rec_id.strip(),
            SkillArtifactRecommendation.slug == slug,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    if rec.status == REC_STATUS_DISMISSED:
        raise HTTPException(status_code=409, detail="Recommendation was dismissed.")
    if rec.status == REC_STATUS_PUBLISHED:
        raise HTTPException(status_code=409, detail="Already published.")
    if rec.status == REC_STATUS_GENERATING and rec.suggest_job_id:
        job_existing = (
            db.query(SkillArtifactSuggestJob)
            .filter(SkillArtifactSuggestJob.id == rec.suggest_job_id)
            .first()
        )
        if job_existing and job_existing.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING):
            raise HTTPException(status_code=409, detail="Generation already in progress.")

    job = SkillArtifactSuggestJob(
        id=str(uuid.uuid4()),
        slug=slug,
        owner_sub=auth.sub,
        artifact_name_hint=rec.name,
        recommendation_id=rec.id,
        status=JOB_STATUS_PENDING,
        status_detail="Queued",
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    rec.status = REC_STATUS_GENERATING
    rec.suggest_job_id = job.id
    rec.error_message = None
    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    background_tasks.add_task(_run_suggest_job, job.id, rec.description)
    return {"job_id": job.id, "recommendation_id": rec.id}


@router.post("/api/skills/{slug}/artifact-recommendations/{rec_id}/dismiss")
def dismiss_recommendation(
    slug: str,
    rec_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    rec = (
        db.query(SkillArtifactRecommendation)
        .filter(
            SkillArtifactRecommendation.id == rec_id.strip(),
            SkillArtifactRecommendation.slug == slug,
        )
        .first()
    )
    if not rec:
        raise HTTPException(status_code=404, detail="Recommendation not found.")
    rec.status = REC_STATUS_DISMISSED
    rec.updated_at = datetime.now(timezone.utc)
    db.commit()
    return {"ok": True}
