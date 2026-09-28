"""URLs used for service-to-service calls inside a deployment."""

from __future__ import annotations

import os

from app_config import APP_BASE_URL


def platform_internal_base_url() -> str:
    """Base URL for internal agent/skill subprocess callbacks.

    Prefer ``PLATFORM_INTERNAL_BASE_URL`` (reachable from sandbox containers).
    ``DERIV_ONE_BASE_URL`` is accepted as a legacy alias. Falls back to
    ``APP_BASE_URL`` and then localhost for local development.
    """
    raw = (
        os.environ.get("PLATFORM_INTERNAL_BASE_URL")
        or os.environ.get("DERIV_ONE_BASE_URL")
        or os.environ.get("APP_BASE_URL")
        or APP_BASE_URL
        or "http://localhost:8000"
    )
    return raw.strip().rstrip("/")
