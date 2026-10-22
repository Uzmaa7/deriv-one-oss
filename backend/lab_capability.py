"""Short-lived capability for Lab app runtime calls.

A Lab document is an opaque origin. It cannot send the platform session cookie.
The HTML response embeds a token that authorizes only that viewer's calls to
that app's data and run routes.
"""

from __future__ import annotations

import os
import time
from typing import Any

import jwt

from auth_policy import AuthContext

_PURPOSE = "lab-app-runtime"
_TTL_SECONDS = 8 * 60 * 60
_HEADER = "x-lab-capability"


def lab_capability_header_name() -> str:
    return _HEADER


def _secret() -> str:
    for key in (
        "LAB_APP_CAPABILITY_SECRET",
        "INTERNAL_SIGNING_SECRET",
        "SECRET_ENCRYPTION_KEY",
        "PLATFORM_UI_ATTESTATION_SECRET",
    ):
        value = os.environ.get(key, "").strip()
        if value:
            return value
    if os.environ.get("APP_ENV", "").strip().lower() in ("production", "prod"):
        raise RuntimeError("Lab app capability secret is not configured.")
    return "dev-only-lab-capability"


def issue_lab_capability(auth: AuthContext, app_id: str) -> str:
    now = int(time.time())
    groups = [g.strip() for g in auth.groups if g and g.strip()][:20]
    payload = {
        "purpose": _PURPOSE,
        "app_id": app_id,
        "sub": auth.sub,
        "email": auth.email,
        "groups": groups,
        "iat": now,
        "exp": now + _TTL_SECONDS,
    }
    return jwt.encode(payload, _secret(), algorithm="HS256")


def verify_lab_capability(token: str, app_id: str) -> AuthContext | None:
    if not token or not app_id:
        return None
    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            _secret(),
            algorithms=["HS256"],
        )
    except jwt.PyJWTError:
        return None
    if claims.get("purpose") != _PURPOSE:
        return None
    if claims.get("app_id") != app_id:
        return None
    sub = str(claims.get("sub") or "")
    email = str(claims.get("email") or "")
    if not sub or not email:
        return None
    groups = claims.get("groups") or []
    if not isinstance(groups, list):
        groups = []
    return AuthContext(
        sub=sub,
        email=email,
        groups=tuple(str(g) for g in groups if isinstance(g, str)),
    )
