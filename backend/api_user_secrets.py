"""User-owned personal secrets — CRUD API.

Each user can store their own secrets (Slack tokens, GitHub PATs, API keys, etc.)
that are injected as environment variables into every run_python sandbox execution
alongside any skill-level secrets.

Storage contract:
  - Raw values are NEVER stored in the database.
  - The database holds only an opaque the vault version ref.
  - the vault holds the actual value.

secret naming: {RESOURCE_PREFIX}-user-{sha256(owner_sub)[:32]}-{slug_lowercase}

Slug rules (enforced at write time):
  - Must match ^[A-Z][A-Z0-9_]{1,99}$ (UPPER_SNAKE_CASE, 2–100 chars)
  - Must not start with a reserved server prefix (see _BLOCKED_PREFIXES)
  - Must not shadow env vars in the sandbox system allowlist
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from database import SessionLocal, UserSecret
from secret_vault import (
    SecretVaultError,
    SecretVaultMisconfiguredError,
    get_vault,
    vault_path_for_user_secret,
)
from tenancy import request_owner_sub

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Slug validation ───────────────────────────────────────────────────────────

_SLUG_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,99}$")

# Server-controlled env var prefixes / names that users must not shadow.
# These are never present in the sandbox env (see _execute_code_sync safe_env),
# but we block them at write time to prevent confusion and future-proof the
# restriction in case the allowlist ever expands.
_BLOCKED_PREFIXES: tuple[str, ...] = (
    "MODEL_GATEWAY_",
    "PROJECT_ID_",
    "GOOGLE_",
    "TEAM_",
    "POLICY_",
    "ENABLE_MOCK",
    "HUB_DATABASE",
    "ANTHROPIC_",
)

# Exact names that match the sandbox system-var allowlist
_BLOCKED_EXACT: frozenset[str] = frozenset({
    "PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP",
    "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED",
    "SLACK_TOKEN",
})


def _validate_slug(slug: str) -> None:
    """Raise ValueError with a user-readable message if slug is invalid."""
    if not isinstance(slug, str):
        raise ValueError("slug must be a string")
    if not _SLUG_RE.match(slug):
        raise ValueError(
            "slug must be UPPER_SNAKE_CASE (A–Z, 0–9, underscore), "
            "start with a letter, and be 2–100 characters long. "
            f"Got: {slug!r}"
        )
    if slug in _BLOCKED_EXACT:
        raise ValueError(f"slug {slug!r} is reserved and cannot be used")
    for prefix in _BLOCKED_PREFIXES:
        if slug.startswith(prefix):
            raise ValueError(
                f"slug cannot start with reserved prefix {prefix!r}"
            )


# ── Request / response models ─────────────────────────────────────────────────

class UserSecretCreate(BaseModel):
    slug: str
    value: str
    display_name: Optional[str] = None
    description: Optional[str] = None


class UserSecretUpdate(BaseModel):
    value: str
    display_name: Optional[str] = None
    description: Optional[str] = None


def _secret_to_dict(row: UserSecret) -> dict:
    """Public representation — never includes the raw secret value."""
    return {
        "id": row.id,
        "slug": row.slug,
        "display_name": row.display_name,
        "description": row.description,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
    }


# ── Reusable core (callable without an HTTP request) ───────────────────────────

def upsert_user_secret(
    owner_sub: str,
    slug: str,
    value: str,
    *,
    display_name: str | None = None,
    description: str | None = None,
) -> UserSecret:
    """Validate, vault-write, and upsert a `UserSecret` row. Raises ValueError
    (bad slug/value) or SecretVaultError/SecretVaultMisconfiguredError (vault
    failure) — callers translate those into whatever response shape they need.
    """
    _validate_slug(slug)
    if not value or not value.strip():
        raise ValueError("value must not be empty")
    if len(value) > 64 * 1024:
        raise ValueError("value must be <= 64 KiB")

    display_name = (display_name or "").strip()[:200] or None
    description = (description or "").strip()[:500] or None

    vault = get_vault()
    secret_name = vault_path_for_user_secret(owner_sub, slug)
    ref = vault.write(secret_name, value)

    db = SessionLocal()
    try:
        existing = (
            db.query(UserSecret)
            .filter(UserSecret.owner_sub == owner_sub, UserSecret.slug == slug)
            .first()
        )
        now = datetime.now(timezone.utc)
        if existing:
            existing.secret_ref = ref
            existing.display_name = display_name
            existing.description = description
            existing.updated_at = now
        else:
            db.add(UserSecret(
                owner_sub=owner_sub,
                slug=slug,
                display_name=display_name,
                description=description,
                secret_ref=ref,
            ))
        db.commit()

        row = (
            db.query(UserSecret)
            .filter(UserSecret.owner_sub == owner_sub, UserSecret.slug == slug)
            .first()
        )
        return row
    finally:
        db.close()


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("/api/secrets", status_code=201)
def create_user_secret(
    body: UserSecretCreate,
    owner_sub: str = Depends(request_owner_sub),
):
    """Store a new user secret. Upserts if the slug already exists for this user."""
    try:
        row = upsert_user_secret(
            owner_sub,
            body.slug,
            body.value,
            display_name=body.display_name,
            description=body.description,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except SecretVaultMisconfiguredError as exc:
        logger.error("Vault unavailable when creating user secret: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Secret storage is temporarily unavailable. Try again shortly.",
        )
    except SecretVaultError as exc:
        logger.error("vault.write failed for user secret slug=%s: %s", body.slug, exc)
        raise HTTPException(status_code=500, detail="Failed to store secret. Contact support.")

    return _secret_to_dict(row)


@router.get("/api/secrets")
def list_user_secrets(owner_sub: str = Depends(request_owner_sub)):
    """List all secret slugs for this user. Values are never returned."""
    db = SessionLocal()
    try:
        rows = (
            db.query(UserSecret)
            .filter(UserSecret.owner_sub == owner_sub)
            .order_by(UserSecret.created_at)
            .all()
        )
        return [_secret_to_dict(r) for r in rows]
    finally:
        db.close()


@router.put("/api/secrets/{secret_id}")
def update_user_secret(
    secret_id: str,
    body: UserSecretUpdate,
    owner_sub: str = Depends(request_owner_sub),
):
    """Rotate the value of an existing user secret."""
    if not body.value or not body.value.strip():
        raise HTTPException(status_code=422, detail="value must not be empty")
    if len(body.value) > 64 * 1024:
        raise HTTPException(status_code=422, detail="value must be <= 64 KiB")

    db = SessionLocal()
    try:
        row = db.get(UserSecret, secret_id)
        if not row or row.owner_sub != owner_sub:
            raise HTTPException(status_code=404, detail="Secret not found")

        try:
            vault = get_vault()
        except SecretVaultMisconfiguredError as exc:
            logger.error("Vault unavailable when updating user secret: %s", exc)
            raise HTTPException(status_code=503, detail="Secret storage is temporarily unavailable.")

        secret_name = vault_path_for_user_secret(owner_sub, row.slug)
        try:
            ref = vault.write(secret_name, body.value)
        except SecretVaultError as exc:
            logger.error("vault.write failed during rotation for slug=%s: %s", row.slug, exc)
            raise HTTPException(status_code=500, detail="Failed to rotate secret.")

        row.secret_ref = ref
        if body.display_name is not None:
            row.display_name = (body.display_name or "").strip()[:200] or None
        if body.description is not None:
            row.description = (body.description or "").strip()[:500] or None
        row.updated_at = datetime.now(timezone.utc)
        db.commit()
        return _secret_to_dict(row)
    finally:
        db.close()


@router.delete("/api/secrets/{secret_id}")
def delete_user_secret(
    secret_id: str,
    owner_sub: str = Depends(request_owner_sub),
):
    """Delete a user secret — removes both the DB row and the secret."""
    db = SessionLocal()
    try:
        row = db.get(UserSecret, secret_id)
        if not row or row.owner_sub != owner_sub:
            raise HTTPException(status_code=404, detail="Secret not found")

        slug_for_log = row.slug

        try:
            vault = get_vault()
            vault.delete(row.secret_ref)
        except SecretVaultMisconfiguredError:
            logger.warning(
                "Vault unavailable during delete; removing DB row for slug=%s anyway",
                slug_for_log,
            )
        except SecretVaultError:
            logger.warning(
                "vault.delete failed for slug=%s; removing DB row anyway",
                slug_for_log,
            )

        db.delete(row)
        db.commit()
        return {"ok": True}
    finally:
        db.close()
