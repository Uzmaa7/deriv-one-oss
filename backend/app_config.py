"""Deployment-level naming and environment.

Anything that names a resource outside the application — a secret, a bucket, an
index — is built from ``RESOURCE_PREFIX`` so that several deployments can share
one backing account without colliding, and so that no vendor's naming
convention leaks into application code.

``APP_ENV`` names the deployment environment. It defaults to ``production``
deliberately: an unset value must never silently resolve to a *different*
environment's resources, and failing safe here means failing towards the
environment whose data you least want to touch by accident.
"""

from __future__ import annotations

import os
import re

#: Prefix for every externally-named resource this deployment owns.
RESOURCE_PREFIX: str = (os.environ.get("RESOURCE_PREFIX") or "platform").strip("-") or "platform"

PRODUCTION_ENV = "production"

# ── White-label branding ──────────────────────────────────────────────────────

APP_NAME: str = (os.environ.get("APP_NAME") or "Platform").strip() or "Platform"
APP_SHORT_NAME: str = (os.environ.get("APP_SHORT_NAME") or APP_NAME).strip() or APP_NAME
APP_LOGO_URL: str = (
    os.environ.get("APP_LOGO_URL") or "/app-logo-icon.svg"
).strip() or "/app-logo-icon.svg"
APP_BASE_URL: str = (
    os.environ.get("APP_BASE_URL") or "http://localhost:8080"
).strip().rstrip("/")
ORG_NOUN: str = (os.environ.get("ORG_NOUN") or "employee").strip() or "employee"
BRAND_COLOR: str = (os.environ.get("BRAND_COLOR") or "#FF444F").strip() or "#FF444F"
BRAND_COLOR_HOVER: str = (
    os.environ.get("BRAND_COLOR_HOVER") or "#e03d47"
).strip() or "#e03d47"
BRAND_COLOR_SOFT: str = (
    os.environ.get("BRAND_COLOR_SOFT") or "#FFF0F0"
).strip() or "#FFF0F0"


def http_user_agent(component: str = "") -> str:
    """Build a safe HTTP User-Agent string from deployment branding."""
    slug = re.sub(r"[^A-Za-z0-9]+", "", APP_NAME) or "Platform"
    suffix = f"; {component}" if component else ""
    return f"Mozilla/5.0 (compatible; {slug}/1.0{suffix}; +{APP_BASE_URL})"


def current_env() -> str:
    """Normalised deployment environment: ``production``, ``staging``, ..."""
    return (os.environ.get("APP_ENV") or PRODUCTION_ENV).strip().lower() or PRODUCTION_ENV


def env_suffix() -> str:
    """``""`` in production, ``-<env>`` elsewhere, for suffixing resource names."""
    env = current_env()
    return "" if env == PRODUCTION_ENV else f"-{env}"


def resource_name(*parts: str) -> str:
    """Join ``RESOURCE_PREFIX`` and ``parts`` into one external resource name."""
    cleaned = [p.strip("-") for p in parts if p and p.strip("-")]
    return "-".join([RESOURCE_PREFIX, *cleaned])
