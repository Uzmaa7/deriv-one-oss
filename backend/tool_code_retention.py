"""Optional retention of redacted run_python samples."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone

from code_fingerprint import code_sha256, shape_sha256
from database import SessionLocal
from redaction import redact_text

logger = logging.getLogger(__name__)

_RETAIN = os.environ.get("RETAIN_TOOL_CODE_SAMPLES", "").lower() in ("1", "true", "yes")
_TTL_DAYS = int(os.environ.get("TOOL_CODE_SAMPLE_TTL_DAYS", "14"))
_MAX_PER_SHAPE_PER_DAY = 3


def maybe_record_run_python_sample(
    *,
    agent_id: str,
    skill_slugs: list[str],
    code: str,
    ok: bool,
) -> None:
    if not _RETAIN or not code.strip():
        return
    shape = shape_sha256(code)
    if not shape:
        return
    try:
        db = SessionLocal()
        try:
            from artifact_models import ToolCodeSample
            from sqlalchemy import func

            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            count = (
                db.query(func.count(ToolCodeSample.id))
                .filter(
                    ToolCodeSample.agent_id == agent_id,
                    ToolCodeSample.shape_sha256 == shape,
                    ToolCodeSample.created_at >= start,
                )
                .scalar()
            )
            if count and count >= _MAX_PER_SHAPE_PER_DAY:
                return
            now = datetime.now(timezone.utc)
            row = ToolCodeSample(
                agent_id=agent_id,
                skill_slugs_json=json.dumps(skill_slugs),
                code_redacted=redact_text(code),
                code_sha256=code_sha256(code),
                shape_sha256=shape,
                code_len=len(code),
                ok=ok,
                created_at=now,
                expires_at=now + timedelta(days=_TTL_DAYS),
            )
            db.add(row)
            db.commit()
        finally:
            db.close()
    except Exception:
        logger.exception("tool_code_retention: failed to record sample")


def purge_expired_tool_code_samples() -> int:
    """Delete expired tool_code_samples rows. Returns rows removed."""
    try:
        db = SessionLocal()
        try:
            from artifact_models import ToolCodeSample

            now = datetime.now(timezone.utc)
            deleted = (
                db.query(ToolCodeSample)
                .filter(ToolCodeSample.expires_at < now)
                .delete(synchronize_session=False)
            )
            db.commit()
            if deleted:
                logger.info("tool_code_retention: purged %d expired sample(s)", deleted)
            return int(deleted or 0)
        finally:
            db.close()
    except Exception:
        logger.exception("tool_code_retention: purge failed")
        return 0
