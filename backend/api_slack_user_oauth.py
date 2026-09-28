"""HTTP API for the Slack user-integration OAuth flow.

Endpoints
─────────
POST /api/slack-user-oauth/start
    Authenticated endpoint. Generates a UUID CSRF state, persists it in
    ``slack_user_oauth_states`` with a 15-minute TTL, and returns the Slack
    OAuth authorisation URL. The frontend navigates the browser to that URL.

GET  /api/slack-user-oauth/callback   (Slack → browser → us)
    Slack redirects here after the user approves (or denies) access.
    Validates the ``state`` param, exchanges the auth code for an ``xoxp-``
    user token, and stores it as ``SLACK_USER_TOKEN`` via the same vault path
    used by ``api_user_secrets.py``. Finally redirects to the frontend:
    - Success: ``{APP_BASE_URL}/integrations?oauth_connected=slack``
    - Error:   ``{APP_BASE_URL}/integrations?oauth_error=<reason>&oauth_integration=slack``

Security
────────
- The ``state`` UUID is validated against the DB row; mismatched or expired
  states are rejected (CSRF protection).
- The ``owner_sub`` stored in the state row is cross-checked against the JWT
  on the callback so a token cannot be injected across users.
- No token value is logged; only error codes and safe refs appear in logs.
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

from app_config import RESOURCE_PREFIX
from database import SessionLocal, SlackUserOAuthState, UserSecret
from secret_vault import (
    SecretVaultError,
    SecretVaultMisconfiguredError,
    get_vault,
    vault_path_for_user_secret,
)
from slack_oauth import (
    SlackOAuthError,
    build_user_integration_auth_url,
    exchange_user_integration_code,
)
from tenancy import request_owner_sub

logger = logging.getLogger(__name__)
router = APIRouter()

_STATE_TTL_MINUTES = 15
_SLUG = "SLACK_USER_TOKEN"


# ── Config helpers ────────────────────────────────────────────────────────────

def _get_client_id() -> str:
    v = os.environ.get("SLACK_USER_INTEGRATION_CLIENT_ID", "").strip()
    if not v:
        raise HTTPException(
            status_code=503,
            detail=(
                "Slack integration is not configured on this deployment. "
                "Set SLACK_USER_INTEGRATION_CLIENT_ID in the environment."
            ),
        )
    return v


def _get_client_secret() -> str:
    """Read the user-integration app client secret: env var first (dev), then vault."""
    env_val = os.environ.get("SLACK_USER_INTEGRATION_CLIENT_SECRET", "").strip()
    if env_val:
        return env_val
    vault = get_vault()
    try:
        return vault.read(vault.latest_version_ref(f"{RESOURCE_PREFIX}-slack-user-integration-client-secret"))
    except SecretVaultMisconfiguredError as exc:
        logger.error("Secret vault not configured: %s", exc)
        raise HTTPException(
            status_code=503,
            detail="Secret store is not configured — cannot retrieve Slack app credentials.",
        )
    except SecretVaultError:
        logger.exception("Failed to read Slack user-app client secret from vault")
        raise HTTPException(
            status_code=503,
            detail="Could not retrieve Slack app credentials.",
        )


def _app_base_url() -> str:
    return os.environ.get("APP_BASE_URL", "").rstrip("/")


def _callback_uri(base_url: str) -> str:
    return f"{base_url}/api/slack-user-oauth/callback"


def _frontend_success_url(base_url: str) -> str:
    return f"{base_url}/integrations?oauth_connected=slack"


def _frontend_error_url(base_url: str, reason: str) -> str:
    safe = reason[:120].replace("&", "").replace("=", "")
    return f"{base_url}/integrations?oauth_error={safe}&oauth_integration=slack"


# ── POST /api/slack-user-oauth/start ─────────────────────────────────────────

@router.post("/api/slack-user-oauth/start")
async def start_user_oauth(
    request: Request,
    owner_sub: str = Depends(request_owner_sub),
):
    """Generate a CSRF state token and return the Slack authorisation URL."""
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
        state_row = SlackUserOAuthState(
            owner_sub=owner_sub,
            expires_at=expires_at.replace(tzinfo=None),  # SQLite stores naive datetimes
        )
        db.add(state_row)
        db.commit()
        db.refresh(state_row)
        state_id = state_row.id
    finally:
        db.close()

    redirect_url = build_user_integration_auth_url(
        client_id=client_id,
        redirect_uri=_callback_uri(base_url),
        state=state_id,
    )
    return {"redirect_url": redirect_url}


# ── GET /api/slack-user-oauth/callback ───────────────────────────────────────

@router.get("/api/slack-user-oauth/callback")
async def user_oauth_callback(
    request: Request,
    owner_sub: str = Depends(request_owner_sub),
):
    """Slack redirects here after the user approves or denies access."""
    params = request.query_params
    state = params.get("state", "")
    code = params.get("code", "")
    error = params.get("error", "")
    base_url = _app_base_url()

    if error:
        logger.warning("Slack user OAuth returned error=%s (state=%s)", error, state)
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
            db.query(SlackUserOAuthState)
            .filter(SlackUserOAuthState.id == state)
            .first()
        )
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        if (
            not state_row
            or state_row.owner_sub != owner_sub
            or state_row.expires_at < now_naive
        ):
            logger.warning(
                "Invalid or expired Slack user OAuth state (state=%s, owner=%s)",
                state,
                owner_sub,
            )
            return RedirectResponse(
                url=_frontend_error_url(base_url, "invalid_state"),
                status_code=302,
            )
        # Consume state immediately — single use.
        db.delete(state_row)
        db.commit()
    finally:
        db.close()

    # Exchange code for user token.
    try:
        client_id = _get_client_id()
        client_secret = _get_client_secret()
    except HTTPException as exc:
        logger.error("Could not retrieve Slack app credentials during callback: %s", exc.detail)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "config_error"),
            status_code=302,
        )

    try:
        token = await exchange_user_integration_code(
            code=code,
            client_id=client_id,
            client_secret=client_secret,
            redirect_uri=_callback_uri(base_url),
        )
    except SlackOAuthError as exc:
        logger.error(
            "Slack user integration code exchange failed: slack_error=%s", exc.slack_error
        )
        return RedirectResponse(
            url=_frontend_error_url(base_url, exc.slack_error or "token_exchange_failed"),
            status_code=302,
        )
    except Exception:
        logger.exception("Unexpected error during Slack user OAuth code exchange")
        return RedirectResponse(
            url=_frontend_error_url(base_url, "token_exchange_failed"),
            status_code=302,
        )

    # Store token in the vault and upsert the DB row — identical to
    # what api_user_secrets.py does for a manual SLACK_USER_TOKEN paste.
    try:
        vault = get_vault()
    except SecretVaultMisconfiguredError:
        logger.error("Vault unavailable when storing Slack user token for owner=%s", owner_sub)
        return RedirectResponse(
            url=_frontend_error_url(base_url, "storage_unavailable"),
            status_code=302,
        )

    secret_name = vault_path_for_user_secret(owner_sub, _SLUG)
    try:
        ref = vault.write(secret_name, token)
    except SecretVaultError:
        logger.exception("vault.write failed for SLACK_USER_TOKEN owner=%s", owner_sub)
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
            existing.display_name = "Slack user token (OAuth)"
            existing.description = "Connected via Slack OAuth — grants personal workspace access."
            existing.updated_at = now
        else:
            db.add(UserSecret(
                owner_sub=owner_sub,
                slug=_SLUG,
                display_name="Slack user token (OAuth)",
                description="Connected via Slack OAuth — grants personal workspace access.",
                secret_ref=ref,
            ))
        db.commit()
    except Exception:
        logger.exception("DB upsert failed for SLACK_USER_TOKEN owner=%s", owner_sub)
        # Vault write succeeded — token is safe. DB row failure is non-fatal;
        # the integration will appear disconnected until next connect attempt.
        return RedirectResponse(
            url=_frontend_error_url(base_url, "db_error"),
            status_code=302,
        )
    finally:
        db.close()

    logger.info("Slack user integration connected successfully for owner=%s", owner_sub)
    return RedirectResponse(url=_frontend_success_url(base_url), status_code=302)


# ── Startup pruning helper (called from main.py lifespan) ─────────────────────

def prune_expired_user_oauth_states() -> int:
    """Delete expired SlackUserOAuthState rows. Returns count removed."""
    db = SessionLocal()
    try:
        now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
        deleted = (
            db.query(SlackUserOAuthState)
            .filter(SlackUserOAuthState.expires_at < now_naive)
            .delete(synchronize_session=False)
        )
        db.commit()
        if deleted:
            logger.info("Pruned %d expired Slack user OAuth states", deleted)
        return deleted
    except Exception:
        logger.exception("Failed to prune expired Slack user OAuth states")
        db.rollback()
        return 0
    finally:
        db.close()
