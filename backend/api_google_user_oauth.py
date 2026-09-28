"""HTTP API for the Google Workspace user-integration OAuth flow.

Endpoints
─────────
POST /api/google-user-oauth/start
    Authenticated endpoint. Generates a UUID CSRF state, persists it in
    ``google_user_oauth_states`` with a 15-minute TTL, and returns the
    Google OAuth authorisation URL. The frontend navigates the browser there.

GET  /api/google-user-oauth/callback   (Google → browser → us)
    Google redirects here after the user approves (or denies) access.
    Validates the ``state`` param, exchanges the auth code for a refresh
    token, builds a credentials JSON blob, and stores it as
    ``GOOGLE_OAUTH_CREDENTIALS`` via the same vault path used by
    ``api_user_secrets.py``. Finally redirects to the frontend:
    - Success: ``{APP_BASE_URL}/integrations?oauth_connected=google-workspace``
    - Error:   ``{APP_BASE_URL}/integrations?oauth_error=<reason>&oauth_integration=google-workspace``

Security
────────
- The ``state`` UUID is validated against the DB row; mismatched or expired
  states are rejected (CSRF protection).
- The ``owner_sub`` stored in the state row is cross-checked against the JWT
  on the callback so a token cannot be injected across users.
- No token or secret value is logged; only error codes and safe refs appear.
- Rows are single-use: consumed and deleted immediately after the callback.
- Expired rows (> 15 min) are pruned at application startup.
- All ``/api/*`` routes are already behind the Cloudflare Access JWT middleware.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from database import GoogleUserOAuthState, SessionLocal, UserSecret
from google_oauth import (
    GoogleOAuthError,
    build_credentials_blob,
    build_google_auth_url,
    exchange_google_code,
)
from secret_vault import (
    SecretVaultError,
    SecretVaultMisconfiguredError,
    get_vault,
    vault_path_for_user_secret,
)
from tenancy import request_owner_sub
from app_config import RESOURCE_PREFIX

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_TTL_MINUTES = 15
_SLUG = "GOOGLE_OAUTH_CREDENTIALS"


# ── Config helpers ────────────────────────────────────────────────────────────

def _get_client_id() -> str:
    v = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    if not v:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google Workspace integration is not configured on this deployment. "
                "Set GOOGLE_OAUTH_CLIENT_ID in the environment."
            ),
        )
    return v


def _get_client_secret() -> str:
    """Read the Google OAuth client secret: env var first (dev), then vault."""
    env_val = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if env_val:
        return env_val
    try:
        return get_vault().read(f"{RESOURCE_PREFIX}-google-oauth-client-secret")
    except SecretVaultMisconfiguredError as exc:
        logger.error("Secret vault not configured: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Secret store is not configured — cannot retrieve Google app credentials.",
        )
    except SecretVaultError:
        logger.exception("Failed to read Google OAuth client secret from vault")
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve Google app credentials.",
        )


def _app_base_url() -> str:
    return os.environ.get("APP_BASE_URL", "").rstrip("/")


def _callback_uri(base_url: str) -> str:
    return f"{base_url}/api/google-user-oauth/callback"


def _frontend_success_url(base_url: str) -> str:
    return f"{base_url}/integrations?oauth_connected=google-workspace"


def _frontend_error_url(base_url: str, reason: str) -> str:
    safe = reason[:120].replace("&", "").replace("=", "")
    return f"{base_url}/integrations?oauth_error={safe}&oauth_integration=google-workspace"


# ── POST /api/google-user-oauth/start ────────────────────────────────────────

@router.post("/api/google-user-oauth/start")
async def start_google_oauth(
    request: Request,
    owner_sub: str = Depends(request_owner_sub),
):
    """Generate a CSRF state token and return the Google authorisation URL."""
    client_id = _get_client_id()
    base_url = _app_base_url()
    if not base_url:
        raise HTTPException(
            status_code=503,
            detail="APP_BASE_URL is not configured — cannot build OAuth redirect URI.",
        )

    expires_at = datetime.now(timezone.utc) + timedelta(minutes=_STATE_TTL_MINUTES)

    db = SessionLocal()
    try:
        state_row = GoogleUserOAuthState(
            owner_sub=owner_sub,
            expires_at=expires_at.replace(tzinfo=None),
        )
        db.add(state_row)
        db.commit()
        db.refresh(state_row)
        state_id = state_row.id
    finally:
        db.close()

    redirect_url = build_google_auth_url(
        client_id=client_id,
        redirect_uri=_callback_uri(base_url),
        state=state_id,
    )
    return {"redirect_url": redirect_url}


# ── GET /api/google-user-oauth/callback ──────────────────────────────────────

@router.get("/api/google-user-oauth/callback")
async def google_oauth_callback(
    request: Request,
    owner_sub: str = Depends(request_owner_sub),
):
    """Google redirects here after the user approves or denies access."""
    params = request.query_params
    state = params.get("state", "")
    code = params.get("code", "")
    error = params.get("error", "")
    base_url = _app_base_url()

    if error:
        logger.warning("Google OAuth returned error=%s (state=%s)", error, state)
        return RedirectResponse(
            url=_frontend_error_url(base_url, error),
            status_code=302,
        )

    if not state or not code:
        return RedirectResponse(
            url=_frontend_error_url(base_url, "missing_params"),
            status_code=302,
        )

    # Validate and consume the state row (CSRF + expiry check).
    db = SessionLocal()
    try:
        state_row = (
            db.query(GoogleUserOAuthState)
            .filter(GoogleUserOAuthState.id == state)
            .first()
        )
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if (
            not state_row
            or state_row.owner_sub != owner_sub
            or state_row.expires_at < now_naive
        ):
            logger.warning(
                "Invalid or expired Google OAuth state (state=%s, owner=%s)",
                state,
                owner_sub,
            )
            return RedirectResponse(
                url=_frontend_error_url(base_url, "invalid_state"),
                status_code=302,
            )
        db.delete(state_row)
        db.commit()
    finally:
        db.close()

    # Get credentials.
    try:
        client_id = _get_client_id()
        client_secret = _get_client_secret()
    except HTTPException as exc:
        logger.error("Could not retrieve Google app credentials during callback: %s", exc.detail)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "config_error"),
            status_code=302,
        )

    # Exchange auth code for tokens.
    try:
        token_data = await exchange_google_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=_callback_uri(base_url),
        )
    except GoogleOAuthError as exc:
        logger.error(
            "Google OAuth code exchange failed: google_error=%s", exc.google_error
        )
        return RedirectResponse(
            url=_frontend_error_url(base_url, exc.google_error or "token_exchange_failed"),
            status_code=302,
        )
    except Exception:
        logger.exception("Unexpected error during Google OAuth code exchange")
        return RedirectResponse(
            url=_frontend_error_url(base_url, "token_exchange_failed"),
            status_code=302,
        )

    refresh_token = token_data.get("refresh_token", "")
    credentials_json = build_credentials_blob(
        client_id=client_id,
        client_secret=client_secret,
        refresh_token=refresh_token,
    )

    # Store credentials blob in the vault and upsert the DB row.
    try:
        vault = get_vault()
    except SecretVaultMisconfiguredError:
        logger.error("Vault unavailable when storing Google credentials for owner=%s", owner_sub)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "storage_unavailable"),
            status_code=302,
        )

    secret_name = vault_path_for_user_secret(owner_sub, _SLUG)
    try:
        ref = vault.write(secret_name, credentials_json)
    except SecretVaultError:
        logger.exception("vault.write failed for GOOGLE_OAUTH_CREDENTIALS owner=%s", owner_sub)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "storage_failed"),
            status_code=302,
        )

    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        existing = (
            db.query(UserSecret)
            .filter(UserSecret.owner_sub == owner_sub, UserSecret.slug == _SLUG)
            .first()
        )
        if existing:
            existing.secret_ref = ref
            existing.display_name = "Google Workspace credentials (OAuth)"
            existing.description = "Connected via Google OAuth — grants personal Gmail, Calendar, and Drive access."
            existing.updated_at = now
        else:
            db.add(UserSecret(
                owner_sub=owner_sub,
                slug=_SLUG,
                display_name="Google Workspace credentials (OAuth)",
                description="Connected via Google OAuth — grants personal Gmail, Calendar, and Drive access.",
                secret_ref=ref,
            ))
        db.commit()
    except Exception:
        logger.exception("DB upsert failed for GOOGLE_OAUTH_CREDENTIALS owner=%s", owner_sub)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "db_error"),
            status_code=302,
        )
    finally:
        db.close()

    logger.info("Google Workspace integration connected successfully for owner=%s", owner_sub)
    return RedirectResponse(url=_frontend_success_url(base_url), status_code=302)


# ── Startup pruning helper (called from main.py lifespan) ────────────────────

def prune_expired_google_oauth_states() -> int:
    """Delete expired GoogleUserOAuthState rows. Returns count removed."""
    db = SessionLocal()
    try:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted = (
            db.query(GoogleUserOAuthState)
            .filter(GoogleUserOAuthState.expires_at < now_naive)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Pruned %d expired Google user OAuth states", deleted)
        return deleted
    except Exception:
        logger.exception("Failed to prune expired Google user OAuth states")
        db.rollback()
        return 0
    finally:
        db.close()
