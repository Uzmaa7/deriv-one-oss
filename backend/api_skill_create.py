"""AI-assisted skill creation endpoints.

Flow:
  POST /api/skills/create-job  → creates job, kicks off background generator
  GET  /api/skills/create-job/{id} → polls status / returns result
"""

from __future__ import annotations

from app_config import APP_NAME

import json
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Optional

import anthropic
import httpx
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from cf_access import build_auth_context
from database import SessionLocal
from model_catalog import platform_default_model
from skill_creation_models import (
    SkillCreationJob,
    JOB_STATUS_PENDING,
    JOB_STATUS_FETCHING,
    JOB_STATUS_GENERATING,
    JOB_STATUS_DONE,
    JOB_STATUS_ERROR,
    init_skill_creation_tables,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Config ─────────────────────────────────────────────────────────────────────

MODEL_GATEWAY_API_KEY = os.environ.get("MODEL_GATEWAY_API_KEY", "")
_raw_base = os.environ.get("MODEL_GATEWAY_URL", "").rstrip("/")
if _raw_base.endswith("/v1"):
    _raw_base = _raw_base[:-3]
_MODEL_GATEWAY_URL = _raw_base or None

MODEL = platform_default_model()

_MAX_API_REF_BYTES = 512 * 1024   # 512 KB cap on uploaded reference docs
_MAX_URL_CONTENT_BYTES = 256 * 1024  # 256 KB cap on fetched URL content

# ── Startup ────────────────────────────────────────────────────────────────────

init_skill_creation_tables()

# ── Helpers ────────────────────────────────────────────────────────────────────


def _db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _auth(request: Request):
    try:
        return build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


# ── Endpoints ──────────────────────────────────────────────────────────────────


@router.post("/api/skills/create-job")
async def create_skill_job(
    request: Request,
    background_tasks: BackgroundTasks,
    skill_name: str = Form(...),
    description: str = Form(""),
    api_ref_url: str = Form(""),
    api_ref_file: Optional[UploadFile] = File(None),
    db: Session = Depends(_db),
):
    """Start an AI skill creation job.

    Accepts:
      - skill_name     (required)
      - description    (optional)
      - api_ref_url    (optional) — URL the agent will fetch for API reference
      - api_ref_file   (optional) — uploaded API reference document
    """
    auth = _auth(request)

    name = skill_name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="skill_name is required.")
    if len(name) > 200:
        raise HTTPException(status_code=422, detail="skill_name must be 200 characters or fewer.")

    desc = description.strip()[:2000]
    url = api_ref_url.strip()

    # Validate URL format if provided
    if url and not re.match(r"^https?://", url, re.IGNORECASE):
        raise HTTPException(status_code=422, detail="api_ref_url must start with http:// or https://")

    # Read uploaded file if provided
    uploaded_content: Optional[str] = None
    if api_ref_file and api_ref_file.filename:
        raw = await api_ref_file.read()
        if len(raw) > _MAX_API_REF_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"API reference file exceeds the 512 KB limit.",
            )
        try:
            uploaded_content = raw.decode("utf-8", errors="replace")
        except Exception:
            raise HTTPException(status_code=422, detail="API reference file must be readable text.")

    job = SkillCreationJob(
        id=str(uuid.uuid4()),
        owner_sub=auth.sub,
        skill_name=name,
        description=desc or None,
        api_ref_url=url or None,
        api_ref_content=uploaded_content,
        status=JOB_STATUS_PENDING,
        created_at=datetime.now(timezone.utc),
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(_run_skill_creation, job.id)

    from event_log import log_event as _log_event
    _log_event(auth.sub, auth.email, "skill.create_started", {
        "skill_name": name,
        "has_api_ref": bool(url or uploaded_content),
    })

    return {"job_id": job.id, "status": job.status}


@router.get("/api/skills/create-job/{job_id}")
def get_create_job(job_id: str, request: Request, db: Session = Depends(_db)):
    """Poll a skill creation job for status and result."""
    auth = _auth(request)

    row = db.query(SkillCreationJob).filter(SkillCreationJob.id == job_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found.")
    if row.owner_sub != auth.sub:
        raise HTTPException(status_code=403, detail="Access denied.")

    return {
        "job_id": row.id,
        "status": row.status,
        "status_detail": row.status_detail,
        "result_content": row.result_content,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
    }


# ── Background task ────────────────────────────────────────────────────────────


def _run_skill_creation(job_id: str) -> None:
    """Background task: fetch optional API reference then call Claude to generate SKILL.md."""
    db = SessionLocal()
    try:
        job = db.query(SkillCreationJob).filter(SkillCreationJob.id == job_id).first()
        if job is None:
            logger.error("Skill creator: job %s not found", job_id)
            return

        api_context = job.api_ref_content or ""

        # ── Step 1: fetch URL if provided ───────────────────────────────────────
        if job.api_ref_url:
            job.status = JOB_STATUS_FETCHING
            job.status_detail = f"Fetching API reference from {job.api_ref_url}…"
            db.commit()

            try:
                fetched = _fetch_url(job.api_ref_url)
                if fetched:
                    api_context = fetched if not api_context else api_context + "\n\n" + fetched
                    logger.info("Skill creator: fetched %d chars from %s", len(fetched), job.api_ref_url)
                else:
                    logger.warning("Skill creator: empty response from %s", job.api_ref_url)
            except Exception as exc:
                logger.warning("Skill creator: failed to fetch %s: %s", job.api_ref_url, exc)
                # Non-fatal: proceed without URL content, note it
                api_context += f"\n\n[Note: Could not fetch {job.api_ref_url}: {exc}]"

        # ── Step 2: generate SKILL.md via Claude ────────────────────────────────
        job.status = JOB_STATUS_GENERATING
        job.status_detail = "AI is writing your skill…"
        db.commit()

        try:
            result = _call_skill_generator(
                skill_name=job.skill_name,
                description=job.description or "",
                api_context=api_context,
            )
        except Exception as exc:
            logger.exception("Skill creator: generation failed for job %s", job_id)
            job.status = JOB_STATUS_ERROR
            job.error_message = f"Skill generation failed: {exc}"
            job.completed_at = datetime.now(timezone.utc)
            db.commit()
            return

        job.status = JOB_STATUS_DONE
        job.status_detail = "Skill created successfully."
        job.result_content = result
        job.completed_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Skill creator: job %s complete (%d chars)", job_id, len(result))

    except Exception:
        logger.exception("Skill creator: unhandled error for job %s", job_id)
        try:
            row = db.query(SkillCreationJob).filter(SkillCreationJob.id == job_id).first()
            if row:
                row.status = JOB_STATUS_ERROR
                row.error_message = "An internal error occurred during skill creation."
                row.completed_at = datetime.now(timezone.utc)
                db.commit()
        except Exception:
            pass
    finally:
        db.close()


def _fetch_url(url: str) -> str:
    """Fetch text content from a URL. Returns truncated content."""
    from url_fetch_guard import httpx_request_hook, validate_fetch_url

    ok, err = validate_fetch_url(url)
    if not ok:
        raise ValueError(err)

    headers = {
        "User-Agent": f"Mozilla/5.0 (compatible; {APP_NAME.replace(' ', '')}SkillCreator/1.0)",
        "Accept": "text/html,text/plain,application/json,*/*",
    }
    with httpx.Client(
        timeout=20,
        follow_redirects=True,
        max_redirects=5,
        event_hooks={"request": [httpx_request_hook]},
    ) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        content = resp.text
        # Truncate to cap
        if len(content.encode("utf-8")) > _MAX_URL_CONTENT_BYTES:
            content = content[: _MAX_URL_CONTENT_BYTES // 4 * 3]  # ~192 KB in chars
            content += "\n\n[Content truncated — file too large]"
        return content


# ── Skill generator prompt ────────────────────────────────────────────────────

_SKILL_GENERATOR_SYSTEM_PROMPT = """You are an expert at writing {APP_NAME} AI Skill files (SKILL.md).

A SKILL.md is a Markdown file that serves as an instruction set and knowledge base for an AI agent
(Claude) when executing a specific skill within the {APP_NAME} platform.

## SKILL.md structure

Every SKILL.md MUST follow this structure exactly:

```
---
name: <lowercase-hyphenated-slug>
description: >-
  <One comprehensive sentence describing EXACTLY when to use this skill and what it does.
   Include trigger keywords. Add "Do NOT use for X" boundaries where applicable.>
version: 1.0.0
author: <team-or-author>
tags: [tag1, tag2, tag3]
---

# <Skill Display Name>

<One-paragraph overview of the skill's purpose and capabilities.>

> **IMPORTANT:** <Any critical runtime note — e.g. "Always call run_python to execute code,
  never generate placeholder output.">

---

## When NOT to use

- **<Boundary 1>** — <explanation>
- **<Boundary 2>** — <explanation>

---

## Setup / Authentication

<How the skill authenticates. If it uses environment variables, show:>

```python
import os
API_KEY = os.environ.get("SKILL_API_KEY")
```

<If it uses the vault, show the stdlib fetch pattern.>

---

## Usage Patterns

### <Pattern 1 Name>

<Brief description>

```python
<Working Python code example>
```

### <Pattern 2 Name>

<Brief description>

```python
<Working Python code example>
```

---

## Common Queries

<A few natural-language trigger examples with the corresponding code snippet.>
```

## Rules for writing SKILL.md files

1. **Secrets via env vars**: If the API needs credentials, use `os.environ.get("KEY_NAME")`.
   Name keys descriptively in UPPER_SNAKE_CASE (e.g. `DATADOG_API_KEY`, `SLACK_BOT_TOKEN`).
   Never hardcode real credential values.

2. **Python-only**: All code examples must be Python 3.10+. Use only stdlib + commonly available
   packages (requests, httpx, json, csv, datetime, pandas, etc.).

3. **run_python instruction**: Always include the reminder that the agent has a `run_python` tool
   and should ALWAYS call it to execute code rather than generating placeholder output.

4. **Boundaries matter**: The "When NOT to use" section prevents misuse. Think carefully about
   what adjacent skills might overlap and be explicit.

5. **Tags**: 5-10 lowercase hyphenated tags relevant to the API/service being wrapped.

6. **Description field**: Must be comprehensive — it's used as semantic search text.
   Include service name, key capabilities, trigger keywords, and explicit do-nots.

7. **No placeholder secrets**: Never write `YOUR_API_KEY`, `<token>`, or similar.
   Write `os.environ.get("KEY_NAME")` and explain in the Setup section.

8. **Return the FULL skill file**: Start with `---` (YAML front matter) and end with the last
   content section. Do not add any preamble or explanation outside the Markdown file itself.
""".replace("{APP_NAME}", APP_NAME)


def _call_skill_generator(
    skill_name: str,
    description: str,
    api_context: str,
) -> str:
    """Call Claude to generate a complete SKILL.md file."""
    parts = [f"Create a complete SKILL.md for the following skill:\n\n**Skill Name:** {skill_name}"]

    if description:
        parts.append(f"**Description hint:** {description}")

    if api_context.strip():
        # Trim very long context
        ctx = api_context.strip()
        ctx_limit = 60_000  # ~60k chars is plenty for Claude
        if len(ctx) > ctx_limit:
            ctx = ctx[:ctx_limit] + "\n\n[...content truncated...]"
        parts.append(
            f"**API Reference / Documentation:**\n\n```\n{ctx}\n```"
        )
    else:
        parts.append(
            "No API reference was provided. Use your knowledge of this service/API to write "
            "accurate, working code examples. If you are uncertain about specific endpoint paths "
            "or parameters, note them with TODO comments."
        )

    parts.append(
        "\nGenerate the full SKILL.md now. Output ONLY the Markdown file content — "
        "start with `---` and end with the final section. No preamble, no explanation."
    )

    user_message = "\n\n".join(parts)

    client = anthropic.Anthropic(
        api_key=MODEL_GATEWAY_API_KEY,
        base_url=_MODEL_GATEWAY_URL,
    )

    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=_SKILL_GENERATOR_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )
    from token_tracking import record_token_usage as _record_usage
    _record_usage("skill_create", MODEL, response.usage)

    raw = response.content[0].text.strip()

    # Strip accidental markdown code fences wrapping the whole file
    if raw.startswith("```markdown"):
        raw = raw[len("```markdown"):].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()
    elif raw.startswith("```"):
        raw = raw[3:].strip()
        if raw.endswith("```"):
            raw = raw[:-3].strip()

    # Ensure starts with YAML front matter
    if not raw.startswith("---"):
        raw = "---\n" + raw

    return raw
