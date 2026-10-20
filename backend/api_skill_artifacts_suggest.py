"""AI-suggested skill artifacts (draft → human accept)."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import anthropic
from fastapi import APIRouter, BackgroundTasks, Depends, Form, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from artifact_gate import review as gate_review
from model_catalog import platform_default_model
from artifact_models import (
    ARTIFACT_STATUS_APPROVED,
    ARTIFACT_STATUS_PENDING_APPROVAL,
    AUTHOR_AI_SUGGESTED,
    EFFECT_READ,
    EFFECT_WRITE,
    JOB_STATUS_DONE,
    JOB_STATUS_FAILED,
    JOB_STATUS_PENDING,
    JOB_STATUS_RUNNING,
    REC_STATUS_DRAFT_READY,
    REC_STATUS_FAILED,
    REC_STATUS_PUBLISHED,
    SkillArtifact,
    SkillArtifactRecommendation,
    SkillArtifactSuggestJob,
    SkillArtifactTestSession,
    TEST_STATUS_ERROR,
    TEST_STATUS_FAILED,
    TEST_STATUS_PASSED,
    TEST_STATUS_PENDING,
    TEST_STATUS_RUNNING,
    ToolCodeSample,
    artifact_to_dict,
    parse_manifest,
)
from api_skill_artifacts import _next_version, _require_skill_editor
from artifact_params import ParamsValidationError, validate_params
from auth_policy import AuthContext
from cf_access import build_auth_context
from code_fingerprint import code_sha256
from database import SessionLocal
from skill_submission_models import REVIEW_STATUS_PASS

logger = logging.getLogger(__name__)
router = APIRouter()

_TEST_RUN_RATE_WINDOW_SECS = 600
_TEST_RUN_RATE_MAX = 10
_test_run_rate: dict[str, list[float]] = {}

_MAX_OUTPUT_EXCERPT = 8192
_MAX_ARTIFACT_DESCRIPTION = 4000

_SKILLS_DIR_RUNTIME = Path("/src/data/skills")
_SKILLS_DIR_DEV = Path(__file__).parent.parent / "src" / "data" / "skills"


def _skills_dir() -> Path:
    if _SKILLS_DIR_RUNTIME.is_dir():
        return _SKILLS_DIR_RUNTIME
    return _SKILLS_DIR_DEV


_MODEL_GATEWAY_URL_RAW = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").strip()
_MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL_RAW.rstrip("/")
if _MODEL_GATEWAY_URL.lower().endswith("/v1"):
    _MODEL_GATEWAY_URL = _MODEL_GATEWAY_URL[:-3].rstrip("/")
MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
MODEL: str = platform_default_model()
# Large artifacts (e.g. routing skills) need more than 8k output tokens.
_ARTIFACT_GENERATOR_MAX_TOKENS = max(
    8192, int(os.environ.get("ARTIFACT_GENERATOR_MAX_TOKENS", "16384"))
)

_GENERATOR_SYSTEM = """You draft Python modules for the platform agent artifact system.

Return EXACTLY two markdown sections (do NOT put Python inside JSON strings):

## MANIFEST
A single JSON object:
{
  "name": "<snake_case import name>",
  "entrypoint": "<function name>",
  "effect_class": "read" | "write",
  "summary": "<one line>",
  "declared_hosts": ["host.example.com"],
  "declared_secrets": ["ENV_KEY_NAMES_ONLY"],
  "params_schema": { "type": "object", "properties": {}, "required": [] }
}

## CODE
```python
<full python module source — no markdown fences inside the code>
```

Rules:
- Use requests only through a module-level helper whose first statement checks ALLOWED_HOSTS.
- Never use eval, exec, subprocess, socket, pickle, or dynamic imports.
- entrypoint must exist at module level with typed parameters matching params_schema.
- declared_secrets lists key names only; never embed secret values.
- Prefer read effect unless the skill clearly performs writes (POST that mutates state).
- Keep the module focused; avoid duplicating entire skill docs as comments.
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


def _load_skill_text(slug: str) -> str:
    try:
        return _skill_markdown(slug)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Skill content not found.") from None


def _load_redacted_samples(db: Session, slug: str, limit: int = 3) -> list[str]:
    rows = (
        db.query(ToolCodeSample)
        .order_by(ToolCodeSample.created_at.desc())
        .limit(200)
        .all()
    )
    out: list[str] = []
    for row in rows:
        try:
            slugs = json.loads(row.skill_slugs_json or "[]")
        except json.JSONDecodeError:
            slugs = []
        if slug not in slugs:
            continue
        out.append(row.code_redacted)
        if len(out) >= limit:
            break
    return out


_MAX_CONTINUATIONS = 2
_CONTINUE_CODE_PROMPT = (
    "Continue the ## CODE python block exactly where you stopped. "
    "Do not repeat the MANIFEST or any code already emitted. Close the fence when done."
)

_SEARCH_REPLACE_BLOCK_RE = re.compile(
    r"<<<<<<< SEARCH\r?\n(.*?)\r?\n=======\r?\n(.*?)\r?\n>>>>>>> REPLACE",
    re.DOTALL,
)


def _parse_manifest_blob(manifest_blob: str) -> dict | None:
    manifest_blob = manifest_blob.strip()
    if manifest_blob.startswith("```"):
        manifest_blob = re.sub(r"^```(?:json)?\s*", "", manifest_blob, flags=re.I)
        manifest_blob = re.sub(r"\s*```$", "", manifest_blob)
    try:
        manifest = json.loads(manifest_blob)
    except json.JSONDecodeError:
        start = manifest_blob.find("{")
        end = manifest_blob.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            manifest = json.loads(manifest_blob[start : end + 1])
        except json.JSONDecodeError:
            return None
    if not isinstance(manifest, dict):
        return None
    manifest.setdefault("name", manifest.get("entrypoint", "artifact"))
    return manifest


def _code_block_has_closed_fence(text: str) -> bool:
    if "## CODE" not in text.upper():
        return False
    after_code = re.split(r"##\s*CODE\b", text, maxsplit=1, flags=re.IGNORECASE)[-1]
    open_fence = re.search(r"```(?:python)?\s*\n", after_code, re.IGNORECASE)
    if not open_fence:
        return False
    rest = after_code[open_fence.end() :]
    return "```" in rest


def apply_search_replace_blocks(current_code: str, diff_text: str) -> str:
    """Apply SEARCH/REPLACE diff blocks to current_code (exactly-once match required)."""
    blocks = list(_SEARCH_REPLACE_BLOCK_RE.finditer(diff_text))
    if not blocks:
        raise ValueError("No SEARCH/REPLACE blocks found in ## CHANGES")

    result = current_code
    for match in blocks:
        search = match.group(1)
        replace = match.group(2)
        count = result.count(search)
        if count == 0:
            raise ValueError(f"SEARCH block not found in current code: {search[:80]!r}")
        if count > 1:
            raise ValueError(
                f"SEARCH block matches {count} locations, ambiguous: {search[:80]!r}"
            )
        result = result.replace(search, replace, 1)
    return result


def _parse_sectioned_response(
    raw: str,
    *,
    stop_reason: str | None = None,
) -> tuple[str, dict] | None:
    """Parse ## MANIFEST + ## CODE format (avoids JSON-escaping large Python blobs)."""
    if "## MANIFEST" not in raw.upper():
        return None
    manifest_match = re.search(
        r"##\s*MANIFEST\s*\n(.*?)(?=##\s*(?:CODE|CHANGES)\b|\Z)",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not manifest_match:
        return None
    manifest = _parse_manifest_blob(manifest_match.group(1))
    if manifest is None:
        return None

    code_match = re.search(
        r"##\s*CODE\s*\n```(?:python)?\s*\n(.*?)```",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not code_match:
        if stop_reason == "max_tokens":
            raise ValueError("Response truncated (max_tokens): CODE fence not closed")
        return None

    code = code_match.group(1).strip("\n")
    if not code.strip():
        return None
    return code, manifest


def _parse_diff_editor_response(
    raw: str,
    current_code: str,
    *,
    stop_reason: str | None = None,
) -> tuple[str, dict]:
    if stop_reason == "max_tokens":
        raise ValueError("Response truncated (max_tokens): edit response incomplete")

    if "## MANIFEST" not in raw.upper():
        raise ValueError("## MANIFEST section required")
    manifest_match = re.search(
        r"##\s*MANIFEST\s*\n(.*?)(?=##\s*CHANGES\b|\Z)",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not manifest_match:
        raise ValueError("Could not parse ## MANIFEST section")
    manifest = _parse_manifest_blob(manifest_match.group(1))
    if manifest is None:
        raise ValueError("Invalid manifest JSON in ## MANIFEST")

    changes_match = re.search(
        r"##\s*CHANGES\s*\n(.*)",
        raw,
        flags=re.DOTALL | re.IGNORECASE,
    )
    if not changes_match:
        raise ValueError("## CHANGES section required for diff edit")

    applied_code = apply_search_replace_blocks(current_code, changes_match.group(1).strip())
    return applied_code, manifest


def _parse_generator_response(
    raw: str,
    *,
    stop_reason: str | None = None,
) -> tuple[str, dict]:
    sectioned = _parse_sectioned_response(raw, stop_reason=stop_reason)
    if sectioned is not None:
        return sectioned

    if stop_reason == "max_tokens":
        raise ValueError("Response truncated (max_tokens): could not parse complete artifact")

    cleaned = raw.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        # Legacy: model sometimes wraps JSON in prose — grab the outermost object.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(cleaned[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("generator response must be an object")
    code = data.get("code")
    manifest = data.get("manifest")
    if not isinstance(code, str) or not isinstance(manifest, dict):
        raise ValueError("code and manifest required")
    manifest.setdefault("name", manifest.get("entrypoint", "artifact"))
    return code, manifest


def _stitch_continued_response(
    client: anthropic.Anthropic,
    *,
    system: str,
    user_content: str,
    initial_text: str,
    initial_resp: Any,
    continuation_prompt: str = _CONTINUE_CODE_PROMPT,
    max_continuations: int = _MAX_CONTINUATIONS,
) -> tuple[str, str | None]:
    text = initial_text
    resp = initial_resp
    messages: list[dict[str, str]] = [
        {"role": "user", "content": user_content},
        {"role": "assistant", "content": text},
    ]
    for _ in range(max_continuations):
        if getattr(resp, "stop_reason", None) != "max_tokens":
            break
        if _code_block_has_closed_fence(text):
            break
        messages.append({"role": "user", "content": continuation_prompt})
        cont_resp = client.messages.create(
            model=MODEL,
            max_tokens=_ARTIFACT_GENERATOR_MAX_TOKENS,
            system=system,
            messages=messages,
        )
        cont_text = "".join(
            b.text for b in cont_resp.content if getattr(b, "type", None) == "text"
        )
        text += cont_text
        messages.append({"role": "assistant", "content": cont_text})
        resp = cont_resp
    return text, getattr(resp, "stop_reason", None)


def _call_generator(
    skill_md: str,
    samples: list[str],
    *,
    artifact_name_hint: str | None,
    gate_feedback: str | None,
    scope_description: str | None = None,
    test_failure_feedback: str | None = None,
) -> tuple[str, dict]:
    if not MODEL_GATEWAY_API_KEY:
        raise RuntimeError("LLM not configured")
    client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    user_parts = [
        "Skill documentation:\n",
        skill_md[:96_000 if scope_description else 48_000],
    ]
    cap = 96_000 if scope_description else 48_000
    if len(skill_md) > cap:
        user_parts.append("\n[skill.md truncated for generator context]\n")
    if scope_description:
        user_parts.append(
            "\nArtifact brief (implement this scope; do not rewrite the entire skill):\n"
            f"{scope_description.strip()}\n"
        )
    if artifact_name_hint:
        user_parts.append(f"\nPreferred artifact name: {artifact_name_hint}\n")
    if samples:
        user_parts.append("\nRedacted examples of code agents currently rewrite:\n")
        for i, sample in enumerate(samples, 1):
            user_parts.append(f"\n--- sample {i} ---\n{sample[:8000]}\n")
    if gate_feedback:
        user_parts.append(f"\nFix these static gate violations:\n{gate_feedback}\n")
    if test_failure_feedback:
        user_parts.append(
            "\nThe artifact failed a sandbox test with these params and output. "
            "Fix the bug; keep the same name/entrypoint/params_schema unless they "
            f"caused the failure:\n{test_failure_feedback}\n"
        )
    resp = client.messages.create(
        model=MODEL,
        max_tokens=_ARTIFACT_GENERATOR_MAX_TOKENS,
        system=_GENERATOR_SYSTEM,
        messages=[{"role": "user", "content": "".join(user_parts)}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    user_content = "".join(user_parts)
    text, stop_reason = _stitch_continued_response(
        client,
        system=_GENERATOR_SYSTEM,
        user_content=user_content,
        initial_text=text,
        initial_resp=resp,
    )
    return _parse_generator_response(text, stop_reason=stop_reason)


_MAX_EDIT_PROMPT = 4000
_EDIT_RATE_WINDOW_SECS = 600
_EDIT_RATE_MAX = 15
_edit_rate: dict[str, list[float]] = {}

_ARTIFACT_EDITOR_DIFF_SYSTEM = """You edit an existing platform agent artifact Python module.

Return EXACTLY two markdown sections:

## MANIFEST
A single JSON object (same schema as new artifacts):
{
  "name": "<snake_case import name>",
  "entrypoint": "<function name>",
  "effect_class": "read" | "write",
  "summary": "<one line>",
  "declared_hosts": ["host.example.com"],
  "declared_secrets": ["ENV_KEY_NAMES_ONLY"],
  "params_schema": { "type": "object", "properties": {}, "required": [] }
}

## CHANGES
One or more SEARCH/REPLACE blocks (do NOT re-emit the full module):
<<<<<<< SEARCH
[exact lines from current code to find — include 3-5 context lines]
=======
[replacement lines]
>>>>>>> REPLACE

Rules:
- Apply the user's edit request with the smallest change that satisfies it.
- Each SEARCH block must be an exact substring of the current module and must match exactly once.
- For deletions, leave the REPLACE section empty. For insertions, use a context anchor as SEARCH.
- Preserve name and entrypoint unless the user explicitly asks to rename or change the entrypoint.
- Keep params_schema backward-compatible unless the user asks to change parameters.
- Never use eval, exec, subprocess, socket, pickle, or dynamic imports in new code.
- Never embed secret values.
"""


_ARTIFACT_EDITOR_SYSTEM = """You edit an existing platform agent artifact Python module.

Return EXACTLY two markdown sections:

## MANIFEST
A single JSON object (same schema as new artifacts):
{
  "name": "<snake_case import name>",
  "entrypoint": "<function name>",
  "effect_class": "read" | "write",
  "summary": "<one line>",
  "declared_hosts": ["host.example.com"],
  "declared_secrets": ["ENV_KEY_NAMES_ONLY"],
  "params_schema": { "type": "object", "properties": {}, "required": [] }
}

## CODE
```python
<full updated python module source>
```

Rules:
- Apply the user's edit request with the smallest change that satisfies it.
- Preserve name and entrypoint unless the user explicitly asks to rename or change the entrypoint.
- Keep params_schema backward-compatible unless the user asks to change parameters.
- Use requests only through a module-level helper whose first statement checks ALLOWED_HOSTS.
- Never use eval, exec, subprocess, socket, pickle, or dynamic imports.
- Never embed secret values.
"""


def _check_edit_rate(job_id: str) -> None:
    now = time.monotonic()
    hits = _edit_rate.get(job_id, [])
    hits = [t for t in hits if now - t < _EDIT_RATE_WINDOW_SECS]
    if len(hits) >= _EDIT_RATE_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many edit requests for this job. Try again later.",
        )
    hits.append(now)
    _edit_rate[job_id] = hits


def _call_artifact_editor(
    skill_md: str,
    current_code: str,
    current_manifest: dict,
    edit_prompt: str,
    *,
    gate_feedback: str | None = None,
    use_diff: bool = True,
) -> tuple[str, dict]:
    if not MODEL_GATEWAY_API_KEY:
        raise RuntimeError("LLM not configured")
    client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=_MODEL_GATEWAY_URL)
    manifest_json = json.dumps(current_manifest, indent=2)
    user_parts = [
        "Skill documentation (context only):\n",
        skill_md[:32_000],
        "\n\nCurrent manifest JSON:\n",
        manifest_json[:16_000],
        "\n\nCurrent Python module:\n```python\n",
        current_code[:80_000],
        "\n```\n\nEdit request from reviewer:\n",
        edit_prompt.strip(),
        "\n",
    ]
    if gate_feedback:
        user_parts.append(f"\nFix these static gate violations:\n{gate_feedback}\n")
    system = _ARTIFACT_EDITOR_DIFF_SYSTEM if use_diff else _ARTIFACT_EDITOR_SYSTEM
    user_content = "".join(user_parts)
    resp = client.messages.create(
        model=MODEL,
        max_tokens=_ARTIFACT_GENERATOR_MAX_TOKENS,
        system=system,
        messages=[{"role": "user", "content": user_content}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
    stop_reason = getattr(resp, "stop_reason", None)

    if use_diff:
        return _parse_diff_editor_response(text, current_code, stop_reason=stop_reason)

    text, stop_reason = _stitch_continued_response(
        client,
        system=system,
        user_content=user_content,
        initial_text=text,
        initial_resp=resp,
    )
    return _parse_generator_response(text, stop_reason=stop_reason)


def _load_edit_baseline(
    db: Session,
    job: SkillArtifactSuggestJob,
) -> tuple[str, dict]:
    """Return (code, manifest) to edit — from the job draft or a forked published artifact."""
    if job.proposed_code and job.proposed_manifest_json:
        try:
            manifest = parse_manifest(job.proposed_manifest_json)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"Invalid stored manifest: {exc}") from exc
        return job.proposed_code, manifest

    if not job.source_artifact_id:
        raise RuntimeError("No code available to edit.")

    from artifact_models import SkillArtifact

    row = db.query(SkillArtifact).filter(SkillArtifact.id == job.source_artifact_id).first()
    if not row:
        raise RuntimeError("Source artifact not found.")
    try:
        manifest = parse_manifest(row.manifest_json)
    except (json.JSONDecodeError, ValueError) as exc:
        raise RuntimeError(f"Invalid source manifest: {exc}") from exc
    return row.code, manifest


def _run_artifact_edit_job(job_id: str, edit_prompt: str) -> None:
    db = SessionLocal()
    try:
        job = db.query(SkillArtifactSuggestJob).filter(SkillArtifactSuggestJob.id == job_id).first()
        if job is None:
            return
        job.status = JOB_STATUS_RUNNING
        job.status_detail = "Applying AI edit…"
        job.error_message = None
        job.completed_at = None
        db.commit()

        skill_md = _skill_markdown(job.slug)
        try:
            current_code, current_manifest = _load_edit_baseline(db, job)
        except Exception as exc:
            job.status = JOB_STATUS_FAILED
            job.error_message = str(exc)
            job.status_detail = job.error_message
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        code: str | None = None
        manifest: dict | None = None
        gate_report = None
        feedback: str | None = None

        for attempt in range(3):
            use_diff = attempt < 2
            job.status_detail = f"Editor attempt {attempt + 1}/3…"
            db.commit()
            try:
                code, manifest = _call_artifact_editor(
                    skill_md,
                    current_code,
                    current_manifest,
                    edit_prompt,
                    gate_feedback=feedback,
                    use_diff=use_diff,
                )
            except Exception as exc:
                logger.warning("edit job %s attempt %d failed: %s", job_id, attempt + 1, exc)
                if attempt >= 2:
                    job.status = JOB_STATUS_FAILED
                    job.error_message = f"Editor failed: {exc}"
                    job.status_detail = job.error_message
                    job.completed_at = datetime.now(timezone.utc)
                    db.commit()
                    return
                if use_diff:
                    feedback = (
                        f"Your last response could not be parsed ({exc}). "
                        "Use ## MANIFEST (JSON only) then ## CHANGES with SEARCH/REPLACE blocks. "
                        "Each SEARCH must match exactly once in the current module."
                    )
                else:
                    feedback = (
                        f"Your last response could not be parsed ({exc}). "
                        "Use ## MANIFEST (JSON only) then ## CODE with a ```python fence."
                    )
                continue

            gate_report = gate_review(code, manifest)
            if gate_report.ok:
                break
            feedback = gate_report.to_json()

        if not code or not manifest or gate_report is None or not gate_report.ok:
            job.status = JOB_STATUS_FAILED
            job.error_message = "Edited draft did not pass the static gate."
            if gate_report and gate_report.violations:
                v0 = gate_report.violations[0]
                job.error_message += f" ({v0.rule}: {v0.detail})"
            job.status_detail = job.error_message
            job.gate_report_json = gate_report.to_json() if gate_report else None
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        effect = manifest.get("effect_class", EFFECT_READ)
        note = (
            "Edited draft passed the static gate. Re-run tests before publishing."
            if effect == EFFECT_WRITE
            else "Edited draft passed the static gate. Re-run tests, then publish."
        )

        job.proposed_code = code
        job.proposed_manifest_json = json.dumps(manifest)
        job.gate_report_json = gate_report.to_json()
        job.verification_note = note
        job.status = JOB_STATUS_DONE
        job.status_detail = "Ready for review"
        job.completed_at = datetime.now(timezone.utc)
        _sync_recommendation_draft_ready(db, job)
        db.commit()
    except Exception:
        logger.exception("artifact edit job %s failed", job_id)
        try:
            job = db.query(SkillArtifactSuggestJob).filter(SkillArtifactSuggestJob.id == job_id).first()
            if job:
                job.status = JOB_STATUS_FAILED
                job.error_message = "Internal error during edit."
                job.status_detail = job.error_message
                job.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


class ArtifactAiEditRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=_MAX_EDIT_PROMPT)


def _start_artifact_edit_job(
    db: Session,
    job: SkillArtifactSuggestJob,
    prompt: str,
    background_tasks: BackgroundTasks,
) -> dict:
    if job.status == JOB_STATUS_RUNNING:
        raise HTTPException(status_code=409, detail="Job is already running.")
    _check_edit_rate(job.id)
    job.status = JOB_STATUS_PENDING
    job.status_detail = "Edit queued"
    job.error_message = None
    db.commit()
    background_tasks.add_task(_run_artifact_edit_job, job.id, prompt.strip())
    return {"job_id": job.id, "status": job.status}


def _sync_recommendation_failed(db: Session, job: SkillArtifactSuggestJob, message: str) -> None:
    if not job.recommendation_id:
        return
    rec = (
        db.query(SkillArtifactRecommendation)
        .filter(SkillArtifactRecommendation.id == job.recommendation_id)
        .first()
    )
    if rec is None:
        return
    rec.status = REC_STATUS_FAILED
    rec.error_message = message
    rec.updated_at = datetime.now(timezone.utc)


def _sync_recommendation_draft_ready(db: Session, job: SkillArtifactSuggestJob) -> None:
    if not job.recommendation_id:
        return
    rec = (
        db.query(SkillArtifactRecommendation)
        .filter(SkillArtifactRecommendation.id == job.recommendation_id)
        .first()
    )
    if rec is None:
        return
    rec.status = REC_STATUS_DRAFT_READY
    rec.error_message = None
    rec.updated_at = datetime.now(timezone.utc)


def _run_suggest_job(job_id: str, scope_description: str | None = None) -> None:
    db = SessionLocal()
    try:
        job = db.query(SkillArtifactSuggestJob).filter(SkillArtifactSuggestJob.id == job_id).first()
        if job is None:
            return
        job.status = JOB_STATUS_RUNNING
        job.status_detail = "Generating draft…"
        db.commit()

        skill_md = _skill_markdown(job.slug)
        samples = _load_redacted_samples(db, job.slug)

        code: str | None = None
        manifest: dict | None = None
        gate_report = None
        feedback: str | None = None

        for attempt in range(3):
            job.status_detail = f"Generator attempt {attempt + 1}/3…"
            db.commit()
            try:
                code, manifest = _call_generator(
                    skill_md,
                    samples,
                    artifact_name_hint=job.artifact_name_hint,
                    gate_feedback=feedback,
                    scope_description=scope_description or _get_scope_description(db, job),
                )
            except Exception as exc:
                logger.warning("suggest job %s generator attempt %d failed: %s", job_id, attempt + 1, exc)
                if attempt >= 2:
                    job.status = JOB_STATUS_FAILED
                    job.error_message = f"Generator failed: {exc}"
                    job.status_detail = job.error_message
                    job.completed_at = datetime.now(timezone.utc)
                    _sync_recommendation_failed(db, job, job.error_message or "Generator failed")
                    db.commit()
                    return
                feedback = (
                    f"Your last response could not be parsed ({exc}). "
                    "Use ## MANIFEST (JSON only) then ## CODE with a ```python fence. "
                    "Do not embed Python inside JSON strings."
                )
                continue

            gate_report = gate_review(code, manifest)
            if gate_report.ok:
                break
            feedback = gate_report.to_json()

        if not code or not manifest or gate_report is None or not gate_report.ok:
            job.status = JOB_STATUS_FAILED
            job.error_message = "Could not produce a draft that passes the static gate."
            if gate_report and gate_report.violations:
                v0 = gate_report.violations[0]
                job.error_message += f" ({v0.rule}: {v0.detail})"
            job.status_detail = job.error_message
            job.gate_report_json = gate_report.to_json() if gate_report else None
            job.completed_at = datetime.now(timezone.utc)
            _sync_recommendation_failed(db, job, job.error_message or "Gate failed")
            db.commit()
            return

        effect = manifest.get("effect_class", EFFECT_READ)
        note = (
            "Draft passed the static gate. For write-effect artifacts, use dry_run in "
            "the entrypoint when testing before production calls."
            if effect == EFFECT_WRITE
            else "Draft passed the static gate. Accept to register; verify output in staging."
        )

        job.proposed_code = code
        job.proposed_manifest_json = json.dumps(manifest)
        job.gate_report_json = gate_report.to_json()
        job.verification_note = note
        job.status = JOB_STATUS_DONE
        job.status_detail = "Ready for review"
        job.completed_at = datetime.now(timezone.utc)
        _sync_recommendation_draft_ready(db, job)
        db.commit()
    except Exception:
        logger.exception("suggest job %s failed", job_id)
        try:
            job = db.query(SkillArtifactSuggestJob).filter(SkillArtifactSuggestJob.id == job_id).first()
            if job:
                job.status = JOB_STATUS_FAILED
                job.error_message = "Internal error during suggestion."
                job.status_detail = job.error_message
                job.completed_at = datetime.now(timezone.utc)
                _sync_recommendation_failed(db, job, job.error_message)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


@router.post("/api/skills/{slug}/artifacts/suggest-job")
def start_suggest_job(
    slug: str,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
    artifact_name_hint: str = Form(""),
    artifact_description: str = Form(""),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    _load_skill_text(slug)

    hint = artifact_name_hint.strip() or None
    if hint and not re.match(r"^[a-z][a-z0-9_]{2,39}$", hint):
        raise HTTPException(status_code=422, detail="Invalid artifact_name_hint")

    description = artifact_description.strip() or None
    if description and len(description) > _MAX_ARTIFACT_DESCRIPTION:
        raise HTTPException(
            status_code=422,
            detail=f"artifact_description must be at most {_MAX_ARTIFACT_DESCRIPTION} characters.",
        )

    job = SkillArtifactSuggestJob(
        id=str(uuid.uuid4()),
        slug=slug,
        owner_sub=auth.sub,
        artifact_name_hint=hint,
        artifact_description=description,
        status=JOB_STATUS_PENDING,
        status_detail="Queued",
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    background_tasks.add_task(_run_suggest_job, job.id)
    return {"job_id": job.id, "status": job.status}


@router.get("/api/skills/{slug}/artifacts/suggest-job/{job_id}")
def get_suggest_job(
    slug: str,
    job_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    row = (
        db.query(SkillArtifactSuggestJob)
        .filter(SkillArtifactSuggestJob.id == job_id.strip(), SkillArtifactSuggestJob.slug == slug)
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="Job not found.")
    if row.owner_sub != auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden.")
    return {
        "job_id": row.id,
        "status": row.status,
        "status_detail": row.status_detail,
        "proposed_code": row.proposed_code,
        "proposed_manifest": json.loads(row.proposed_manifest_json)
        if row.proposed_manifest_json
        else None,
        "gate_report": json.loads(row.gate_report_json) if row.gate_report_json else None,
        "verification_note": row.verification_note,
        "error_message": row.error_message,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


@router.post("/api/skills/{slug}/artifacts/suggest-job/{job_id}/ai-edit")
def ai_edit_suggest_job(
    slug: str,
    job_id: str,
    body: ArtifactAiEditRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    _load_skill_text(slug)

    job = (
        db.query(SkillArtifactSuggestJob)
        .filter(
            SkillArtifactSuggestJob.id == job_id.strip(),
            SkillArtifactSuggestJob.slug == slug,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.owner_sub != auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden.")
    if job.status in (JOB_STATUS_PENDING, JOB_STATUS_RUNNING):
        raise HTTPException(status_code=409, detail="Job is already running.")
    if not job.proposed_code and not job.source_artifact_id:
        raise HTTPException(status_code=422, detail="No draft available to edit.")

    return _start_artifact_edit_job(db, job, body.prompt, background_tasks)


@router.post("/api/skills/{slug}/artifacts/{name}/versions/{version}/ai-edit")
def ai_edit_published_artifact(
    slug: str,
    name: str,
    version: int,
    body: ArtifactAiEditRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)
    _load_skill_text(slug)

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

    job = SkillArtifactSuggestJob(
        id=str(uuid.uuid4()),
        slug=slug,
        owner_sub=auth.sub,
        artifact_name_hint=row.name,
        source_artifact_id=row.id,
        status=JOB_STATUS_PENDING,
        status_detail="Edit queued",
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return _start_artifact_edit_job(db, job, body.prompt, background_tasks)


@router.post("/api/skills/{slug}/artifacts/suggestions/{job_id}/accept")
def accept_suggestion(
    slug: str,
    job_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    job = (
        db.query(SkillArtifactSuggestJob)
        .filter(
            SkillArtifactSuggestJob.id == job_id.strip(),
            SkillArtifactSuggestJob.slug == slug,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Suggestion not found.")
    if job.owner_sub != auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden.")
    if job.status != JOB_STATUS_DONE or not job.proposed_code or not job.proposed_manifest_json:
        raise HTTPException(status_code=409, detail="Suggestion is not ready to accept.")

    manifest = parse_manifest(job.proposed_manifest_json)
    gate = gate_review(job.proposed_code, manifest)
    if not gate.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "Gate failed at accept time", "gate": json.loads(gate.to_json())},
        )

    import hashlib

    name = manifest["name"]
    version = _next_version(db, slug, name)
    effect = manifest.get("effect_class", EFFECT_READ)
    now = datetime.now(timezone.utc)
    if effect == EFFECT_READ:
        status = ARTIFACT_STATUS_APPROVED
        approved_at = now
        approved_sub = auth.sub
        approved_email = getattr(auth, "email", None)
    else:
        status = ARTIFACT_STATUS_PENDING_APPROVAL
        approved_at = None
        approved_sub = None
        approved_email = None

    code_hash = hashlib.sha256(job.proposed_code.encode()).hexdigest()
    row = SkillArtifact(
        id=str(uuid.uuid4()),
        slug=slug,
        name=name,
        version=version,
        code=job.proposed_code,
        code_sha256=code_hash,
        manifest_json=job.proposed_manifest_json,
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
        ai_review_status=REVIEW_STATUS_PASS,
        ai_review_reason="Accepted from AI suggestion job.",
        status=status,
        author_kind=AUTHOR_AI_SUGGESTED,
        uploader_sub=auth.sub,
        uploader_email=getattr(auth, "email", None),
        approved_by_sub=approved_sub,
        approved_by_email=approved_email,
        approved_at=approved_at,
        created_at=now,
    )
    db.add(row)
    if job.recommendation_id:
        rec = (
            db.query(SkillArtifactRecommendation)
            .filter(SkillArtifactRecommendation.id == job.recommendation_id)
            .first()
        )
        if rec:
            rec.status = REC_STATUS_PUBLISHED
            rec.published_artifact_id = row.id
            rec.updated_at = now
    db.commit()
    db.refresh(row)
    return {"artifact": artifact_to_dict(row)}


class TestRunRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)
    auto_fix: bool = True


def _check_test_run_rate(job_id: str) -> None:
    now = time.monotonic()
    hits = _test_run_rate.get(job_id, [])
    hits = [t for t in hits if now - t < _TEST_RUN_RATE_WINDOW_SECS]
    if len(hits) >= _TEST_RUN_RATE_MAX:
        raise HTTPException(
            status_code=429,
            detail="Too many test runs for this job. Try again later.",
        )
    hits.append(now)
    _test_run_rate[job_id] = hits


def _truncate_output(text: str, limit: int = _MAX_OUTPUT_EXCERPT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… (truncated)"


def _format_sandbox_result(result: dict[str, Any]) -> str:
    parts: list[str] = []
    if result.get("stdout"):
        parts.append(str(result["stdout"]).strip())
    if result.get("stderr"):
        parts.append(f"[stderr]\n{str(result['stderr']).strip()}")
    output = "\n".join(parts) or "(no output)"
    if len(output) > _MAX_OUTPUT_EXCERPT:
        output = output[:_MAX_OUTPUT_EXCERPT] + "\n… (truncated)"
    return output


def _sandbox_run_ok(output: str, result: dict[str, Any]) -> bool:
    if output.startswith("[error]"):
        return False
    if result.get("returncode", 0) != 0:
        return False
    stderr = (result.get("stderr") or "").strip()
    if "Traceback (most recent call last)" in stderr:
        return False
    return True


def _load_skill_secrets(slug: str) -> dict[str, str]:
    try:
        from skill_submission_models import SkillSecret
        from secret_vault import SecretVaultError, get_vault
    except ImportError:
        return {}

    db = SessionLocal()
    try:
        rows = db.query(SkillSecret).filter(SkillSecret.slug == slug).all()
    except Exception:
        return {}
    finally:
        db.close()

    if not rows:
        return {}

    try:
        vault = get_vault()
    except Exception:
        return {}

    result: dict[str, str] = {}
    for row in rows:
        try:
            result[row.env_key] = vault.read(row.secret_ref)
        except SecretVaultError:
            logger.warning("Could not read skill secret env_key=%s slug=%s", row.env_key, slug)
    return result


def _get_scope_description(db: Session, job: SkillArtifactSuggestJob) -> str | None:
    if job.recommendation_id:
        rec = (
            db.query(SkillArtifactRecommendation)
            .filter(SkillArtifactRecommendation.id == job.recommendation_id)
            .first()
        )
        return rec.description if rec else None
    if job.artifact_description and str(job.artifact_description).strip():
        return str(job.artifact_description).strip()
    return None


def _append_attempt(
    attempts: list[dict],
    *,
    attempt: int,
    code: str,
    status: str,
    output: str = "",
    error: str | None = None,
    regenerated: bool = False,
) -> None:
    attempts.append({
        "attempt": attempt,
        "code_sha256": code_sha256(code),
        "status": status,
        "output_excerpt": _truncate_output(output, 4000),
        "error": error,
        "regenerated": regenerated,
    })


async def _execute_draft_artifact(
    code: str,
    manifest: dict,
    params: dict,
    slug: str,
) -> tuple[str, dict[str, Any], bool]:
    from artifact_runtime import build_draft_call_code, scoped_secrets
    from executor import execute_in_sandbox

    mod_name = manifest.get("name") or manifest.get("entrypoint", "artifact")
    call_code = build_draft_call_code(manifest, params)
    declared = manifest.get("declared_secrets") or []
    secrets = scoped_secrets(_load_skill_secrets(slug), declared)
    try:
        result = await execute_in_sandbox(
            call_code,
            "/tmp",
            secrets or None,
            artifacts={mod_name: code},
        )
    except TimeoutError:
        return "[error] Code execution timed out.", {"returncode": 1}, False
    except Exception as exc:
        return f"[error] Could not execute code: {exc}", {"returncode": 1}, False

    output = _format_sandbox_result(result)
    return output, result, _sandbox_run_ok(output, result)


async def _run_test_session(session_id: str) -> None:
    db = SessionLocal()
    try:
        session = (
            db.query(SkillArtifactTestSession)
            .filter(SkillArtifactTestSession.id == session_id)
            .first()
        )
        if session is None:
            return

        job = (
            db.query(SkillArtifactSuggestJob)
            .filter(SkillArtifactSuggestJob.id == session.job_id)
            .first()
        )
        if job is None or not job.proposed_code or not job.proposed_manifest_json:
            session.status = TEST_STATUS_ERROR
            session.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        session.status = TEST_STATUS_RUNNING
        db.commit()

        try:
            raw_params = json.loads(session.params_json)
        except json.JSONDecodeError:
            session.status = TEST_STATUS_ERROR
            session.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        code = job.proposed_code
        manifest = parse_manifest(job.proposed_manifest_json)
        skill_md = _skill_markdown(job.slug)
        samples = _load_redacted_samples(db, job.slug)
        scope_description = _get_scope_description(db, job)
        attempts: list[dict] = json.loads(session.attempts_json or "[]")

        for attempt_num in range(1, session.max_attempts + 1):
            gate = gate_review(code, manifest)
            if not gate.ok:
                _append_attempt(
                    attempts,
                    attempt=attempt_num,
                    code=code,
                    status="gate_failed",
                    error=gate.to_json(),
                )
                session.attempts_json = json.dumps(attempts)
                session.status = TEST_STATUS_FAILED
                session.final_code = code
                session.final_manifest_json = json.dumps(manifest)
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            try:
                bound_params = validate_params(raw_params, manifest.get("params_schema"))
            except ParamsValidationError as exc:
                _append_attempt(
                    attempts,
                    attempt=attempt_num,
                    code=code,
                    status="params_invalid",
                    error=str(exc),
                )
                session.attempts_json = json.dumps(attempts)
                session.status = TEST_STATUS_FAILED
                session.final_code = code
                session.final_manifest_json = json.dumps(manifest)
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            output, _result, ok = await _execute_draft_artifact(
                code, manifest, bound_params, job.slug
            )

            if ok:
                _append_attempt(
                    attempts,
                    attempt=attempt_num,
                    code=code,
                    status="passed",
                    output=output,
                )
                session.attempts_json = json.dumps(attempts)
                session.status = TEST_STATUS_PASSED
                session.final_code = code
                session.final_manifest_json = json.dumps(manifest)
                session.completed_at = datetime.now(timezone.utc)

                gate_report = gate_review(code, manifest)
                job.proposed_code = code
                job.proposed_manifest_json = json.dumps(manifest)
                job.gate_report_json = gate_report.to_json()
                job.status_detail = "Test passed — ready for review"
                db.commit()
                return

            _append_attempt(
                attempts,
                attempt=attempt_num,
                code=code,
                status="failed",
                output=output,
                error=output if output.startswith("[error]") else "Sandbox run failed",
            )
            session.attempts_json = json.dumps(attempts)
            db.commit()

            if not session.auto_fix or attempt_num >= session.max_attempts:
                session.status = TEST_STATUS_FAILED
                session.final_code = code
                session.final_manifest_json = json.dumps(manifest)
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            feedback = (
                f"Params: {json.dumps(bound_params)}\n"
                f"Output:\n{output}\n"
                "Fix the bug; keep the same name/entrypoint/params_schema unless they caused the failure."
            )
            try:
                code, manifest = _call_generator(
                    skill_md,
                    samples,
                    artifact_name_hint=manifest.get("name"),
                    gate_feedback=None,
                    scope_description=scope_description,
                    test_failure_feedback=feedback,
                )
            except Exception as exc:
                logger.warning("test session %s auto-fix attempt %d failed: %s", session_id, attempt_num, exc)
                _append_attempt(
                    attempts,
                    attempt=attempt_num,
                    code=code,
                    status="regenerate_failed",
                    error=str(exc),
                    regenerated=True,
                )
                session.attempts_json = json.dumps(attempts)
                session.status = TEST_STATUS_FAILED
                session.final_code = code
                session.final_manifest_json = json.dumps(manifest)
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
                return

            _append_attempt(
                attempts,
                attempt=attempt_num,
                code=code,
                status="regenerated",
                regenerated=True,
            )
            session.attempts_json = json.dumps(attempts)
            db.commit()

        session.status = TEST_STATUS_FAILED
        session.completed_at = datetime.now(timezone.utc)
        db.commit()
    except Exception:
        logger.exception("test session %s failed", session_id)
        try:
            session = (
                db.query(SkillArtifactTestSession)
                .filter(SkillArtifactTestSession.id == session_id)
                .first()
            )
            if session:
                session.status = TEST_STATUS_ERROR
                session.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _require_job_access(job: SkillArtifactSuggestJob, auth: AuthContext) -> None:
    if job.owner_sub != auth.sub and not auth.is_admin:
        raise HTTPException(status_code=403, detail="Forbidden.")


@router.post("/api/skills/{slug}/artifacts/suggest-job/{job_id}/test-runs")
def start_test_run(
    slug: str,
    job_id: str,
    body: TestRunRequest,
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()
    _require_skill_editor(slug, auth, db)

    job = (
        db.query(SkillArtifactSuggestJob)
        .filter(
            SkillArtifactSuggestJob.id == job_id.strip(),
            SkillArtifactSuggestJob.slug == slug,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    _require_job_access(job, auth)

    if job.status != JOB_STATUS_DONE or not job.proposed_code or not job.proposed_manifest_json:
        raise HTTPException(status_code=409, detail="Suggestion is not ready to test.")

    manifest = parse_manifest(job.proposed_manifest_json)
    try:
        validate_params(body.params, manifest.get("params_schema"))
    except ParamsValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    _check_test_run_rate(job.id)

    session = SkillArtifactTestSession(
        id=str(uuid.uuid4()),
        job_id=job.id,
        slug=slug,
        owner_sub=auth.sub,
        params_json=json.dumps(body.params),
        auto_fix=body.auto_fix,
        max_attempts=3,
        status=TEST_STATUS_PENDING,
        attempts_json="[]",
        created_at=datetime.now(timezone.utc),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    background_tasks.add_task(_run_test_session, session.id)
    return {"test_run_id": session.id}


@router.get("/api/skills/{slug}/artifacts/suggest-job/{job_id}/test-runs/{test_run_id}")
def get_test_run(
    slug: str,
    job_id: str,
    test_run_id: str,
    request: Request,
    db: Session = Depends(_db),
):
    auth = _auth(request)
    slug = slug.strip().lower()

    job = (
        db.query(SkillArtifactSuggestJob)
        .filter(
            SkillArtifactSuggestJob.id == job_id.strip(),
            SkillArtifactSuggestJob.slug == slug,
        )
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    _require_job_access(job, auth)

    session = (
        db.query(SkillArtifactTestSession)
        .filter(
            SkillArtifactTestSession.id == test_run_id.strip(),
            SkillArtifactTestSession.job_id == job.id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="Test run not found.")

    attempts = json.loads(session.attempts_json or "[]")
    final_manifest = None
    if session.final_manifest_json:
        try:
            final_manifest = json.loads(session.final_manifest_json)
        except json.JSONDecodeError:
            final_manifest = None

    return {
        "test_run_id": session.id,
        "status": session.status,
        "attempts": attempts,
        "final_code": session.final_code,
        "final_manifest": final_manifest,
        "completed_at": session.completed_at.isoformat() if session.completed_at else None,
    }
