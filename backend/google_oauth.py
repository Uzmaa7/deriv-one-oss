"""Google OAuth 2.0 helpers for the Google Workspace user-integration flow.

Issues a refresh token (access_type=offline, prompt=consent) so agents can
act as the user indefinitely without re-authorisation.

The stored credential is a JSON blob compatible with
``google.oauth2.credentials.Credentials.from_authorized_user_info()``::

    {
        "client_id": "...",
        "client_secret": "...",
        "refresh_token": "...",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": [...]
    }

Security posture
────────────────
- No token or secret value ever appears in a log line.
- ``prompt=consent`` ensures a refresh_token is returned on every
  authorisation, even if the user previously granted the same scopes.
- The ``state`` parameter equals the session UUID, validated on every
  callback to prevent CSRF and cross-user injection.
"""

from __future__ import annotations

import json
import logging
from typing import Optional
from urllib.parse import urlencode

import httpx

logger = logging.getLogger(__name__)

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

_HTTP_TIMEOUT_SECS = 15.0

# Personal user scopes — read-only across Gmail, Calendar, and Drive.
# Agents act as the authorising user; they can read but not mutate data.
SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


class GoogleOAuthError(RuntimeError):
    """Raised when a Google OAuth call fails. Never includes raw token values."""

    def __init__(self, message: str, google_error: Optional[str] = None):
        super().__init__(message)
        self.google_error = google_error


def build_google_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Return the Google OAuth authorisation URL.

    ``access_type=offline`` requests a refresh token.
    ``prompt=consent`` forces the consent screen every time so a fresh
    refresh token is always issued (required for reconnect flows).
    """
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_google_code(
    code: str,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
) -> dict:
    """Exchange an authorisation code for tokens.

    Returns a dict with keys: ``access_token``, ``refresh_token``,
    ``expires_in``, ``scope``, ``token_type``.

    Raises ``GoogleOAuthError`` on failure. Never logs secret values.
    """
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECS) as client:
        resp = await client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": client_id,
                "client_secret": client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
        )
    resp.raise_for_status()
    data = resp.json()
    if "error" in data:
        err = data.get("error", "unknown")
        logger.error("Google token exchange failed: error=%s", err)
        raise GoogleOAuthError("Google token exchange failed", google_error=err)

    refresh_token = data.get("refresh_token", "")
    if not refresh_token:
        logger.error("Google token exchange response missing refresh_token — prompt=consent may not have been set")
        raise GoogleOAuthError("Google OAuth response contained no refresh token")

    return data


def build_credentials_blob(
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> str:
    """Build the JSON credentials blob stored as GOOGLE_OAUTH_CREDENTIALS.

    Compatible with ``google.oauth2.credentials.Credentials.from_authorized_user_info()``.
    Never logs any of the secret values.
    """
    return json.dumps({
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "token_uri": GOOGLE_TOKEN_URL,
        "scopes": SCOPES,
    })


GOOGLE_REVOKE_URL = "https://oauth2.googleapis.com/revoke"


async def revoke_refresh_token(refresh_token: str) -> bool:
    """Revoke a refresh token at Google. Best-effort; returns True on success."""
    if not refresh_token:
        return False
    try:
        async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECS) as client:
            resp = await client.post(
                GOOGLE_REVOKE_URL,
                data={"token": refresh_token},
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )
        return resp.status_code == 200
    except Exception:
        logger.exception("Google token revoke request failed")
        return False
