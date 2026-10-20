"""Attestation that a browser API call originated from the platform UI, not a Lab app."""

from __future__ import annotations

import hashlib
import hmac
import os

_PLATFORM_UI_MARKER = "platform-ui-v1"
_HEADER_NAME = "x-platform-client"


def _attestation_secret() -> str:
    explicit = os.environ.get("PLATFORM_UI_ATTESTATION_SECRET", "").strip()
    if explicit:
        return explicit
    # Local development fallback — production deployments should set the explicit secret.
    return "dev-only-platform-ui-attestation"


def platform_client_header_value() -> str:
    secret = _attestation_secret().encode("utf-8")
    return hmac.new(secret, _PLATFORM_UI_MARKER.encode("utf-8"), hashlib.sha256).hexdigest()


def platform_client_header_name() -> str:
    return _HEADER_NAME


def is_valid_platform_client(header_value: str | None) -> bool:
    if not header_value:
        return False
    expected = platform_client_header_value()
    return hmac.compare_digest(header_value.strip(), expected)


def attestation_enforced() -> bool:
    """When true, sensitive platform routes require a valid platform-client header."""
    if os.environ.get("PLATFORM_UI_ATTESTATION_SECRET", "").strip():
        return True
    app_env = os.environ.get("APP_ENV", "").strip().lower()
    return app_env not in ("", "development", "dev", "local", "test")
