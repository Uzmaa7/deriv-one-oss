"""Atomic skill.md commits: optimistic base_hash + short per-slug lock."""

from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from skill_version_history import content_hash, record_skill_version, resolve_edit_actor

logger = logging.getLogger(__name__)

_LOCK_TTL_SECONDS = 8
_LOCK_KEY_PREFIX = "skill:edit-lock:"
_local_locks: dict[str, threading.Lock] = {}
_local_locks_guard = threading.Lock()

_REDIS_HOST = os.environ.get("REDIS_HOST", "").strip()
_REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))


def skill_conflict(current_hash: str, message: str = "Skill was updated by someone else. Reload and retry.") -> HTTPException:
    return HTTPException(
        status_code=409,
        detail={
            "code": "skill_conflict",
            "message": message,
            "current_hash": current_hash,
        },
    )


def _local_lock(slug: str) -> threading.Lock:
    with _local_locks_guard:
        lock = _local_locks.get(slug)
        if lock is None:
            lock = threading.Lock()
            _local_locks[slug] = lock
        return lock


@contextmanager
def _edit_lock(slug: str) -> Iterator[None]:
    """Cross-pod Redis NX lock when available; otherwise process-local lock."""
    token = str(uuid.uuid4())
    key = f"{_LOCK_KEY_PREFIX}{slug}"
    redis_client = None
    acquired_redis = False

    if _REDIS_HOST:
        try:
            import redis

            redis_client = redis.Redis(
                host=_REDIS_HOST,
                port=_REDIS_PORT,
                decode_responses=True,
                socket_connect_timeout=1.5,
                socket_timeout=1.5,
            )
            # Busy-wait briefly so a concurrent save finishes instead of 409-busy.
            for _ in range(20):
                if redis_client.set(key, token, nx=True, ex=_LOCK_TTL_SECONDS):
                    acquired_redis = True
                    break
                threading.Event().wait(0.05)
            if not acquired_redis:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "skill_busy",
                        "message": "Another edit is in progress. Try again in a moment.",
                    },
                )
        except HTTPException:
            raise
        except Exception:
            logger.warning("skill_edit_commit: Redis lock unavailable for %s — using local lock", slug)
            redis_client = None

    local = _local_lock(slug)
    if not local.acquire(timeout=2.0):
        if acquired_redis and redis_client is not None:
            try:
                if redis_client.get(key) == token:
                    redis_client.delete(key)
            except Exception:
                pass
        raise HTTPException(
            status_code=409,
            detail={
                "code": "skill_busy",
                "message": "Another edit is in progress. Try again in a moment.",
            },
        )

    try:
        yield
    finally:
        local.release()
        if acquired_redis and redis_client is not None:
            try:
                if redis_client.get(key) == token:
                    redis_client.delete(key)
            except Exception:
                logger.debug("skill_edit_commit: failed to release Redis lock for %s", slug)


def _apply_to_disk(slug: str, content: str, *, source: str, skills_dir: Path) -> Path:
    skill_path = skills_dir / slug / "skill.md"
    skill_path.parent.mkdir(parents=True, exist_ok=True)
    skill_path.write_text(content, encoding="utf-8")
    logger.info("skill_edit_commit: wrote %s (%s)", skill_path, source)

    from skills_store import push_skill_file

    push_skill_file(slug, skill_path)

    from skills_invalidate import notify_skills_changed

    notify_skills_changed(source, slug)
    return skill_path


def commit_skill_content(
    db: Session,
    slug: str,
    *,
    new_content: str,
    expected_base_hash: str,
    reason: str,
    source: str,
    auth,
    skills_dir: Path,
) -> tuple[int, str]:
    """
    Commit new skill.md if live hash still matches expected_base_hash.

    Returns (version_number, new_content_hash).
    Raises HTTPException 409 on conflict / busy.
    """
    expected = (expected_base_hash or "").strip().lower()
    if len(expected) != 64 or any(c not in "0123456789abcdef" for c in expected):
        raise HTTPException(status_code=422, detail="base_hash must be a 64-char hex SHA-256.")

    skill_path = skills_dir / slug / "skill.md"
    if not skill_path.exists():
        raise HTTPException(status_code=404, detail=f"Skill '{slug}' not found.")

    with _edit_lock(slug):
        live = skill_path.read_text(encoding="utf-8")
        live_hash = content_hash(live)
        if live_hash != expected:
            raise skill_conflict(live_hash)

        if new_content == live:
            return 0, live_hash

        _apply_to_disk(slug, new_content, source=source, skills_dir=skills_dir)

        user_name, user_email, actor_role = resolve_edit_actor(db, slug, auth)
        try:
            version_num = record_skill_version(
                db,
                slug,
                new_content=new_content,
                previous_content=live,
                reason=reason,
                source=source,
                user_sub=auth.sub,
                user_email=user_email or getattr(auth, "email", None),
                user_name=user_name,
                actor_role=actor_role,
            )
        except IntegrityError:
            db.rollback()
            # Rare: version collision under lock — retry once with fresh max.
            try:
                version_num = record_skill_version(
                    db,
                    slug,
                    new_content=new_content,
                    previous_content=live,
                    reason=reason,
                    source=source,
                    user_sub=auth.sub,
                    user_email=user_email or getattr(auth, "email", None),
                    user_name=user_name,
                    actor_role=actor_role,
                )
            except IntegrityError as exc:
                db.rollback()
                logger.exception("skill_edit_commit: version insert failed for %s", slug)
                raise HTTPException(status_code=500, detail="Failed to record skill version.") from exc

        return version_num, content_hash(new_content)
