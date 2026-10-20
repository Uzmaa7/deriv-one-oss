"""Protect operator-supplied auth settings from wizard-written platform.env overrides."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import dotenv_values

logger = logging.getLogger(__name__)

# Keys the setup wizard must not override when already set by the operator (.env, K8s, etc.).
PROTECTED_AUTH_ENV_KEYS = frozenset(
    {
        "ENABLE_MOCK_AUTH",
        "ALLOW_MOCK_AUTH",
        "OIDC_ISSUER",
        "OIDC_JWKS_URL",
        "POLICY_AUD",
        "TEAM_DOMAIN",
        "ADMIN_GROUPS",
        "MEMBER_GROUPS",
        "APP_ENV",
    }
)


def _env_truthy(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in ("1", "true", "yes", "on")


def operator_configured_real_auth() -> bool:
    issuer = (
        os.environ.get("OIDC_ISSUER", "").strip()
        or os.environ.get("TEAM_DOMAIN", "").strip()
    )
    return bool(issuer and os.environ.get("POLICY_AUD", "").strip())


def mock_auth_allowed_for_setup(draft: dict) -> bool:
    """Mock auth may only be written by the setup wizard when explicitly allowed."""
    mode = str(draft.get("deployment_mode") or "").strip().lower()
    if mode != "personal":
        return False

    core = draft.get("core") if isinstance(draft.get("core"), dict) else {}
    app_env = str(core.get("app_env") or os.environ.get("APP_ENV") or "development").strip().lower()
    if app_env in ("production", "prod"):
        return False

    if operator_configured_real_auth():
        return False

    if _env_truthy("ALLOW_MOCK_AUTH"):
        return True

    # Preserve mock auth when the operator already enabled it in the base environment.
    return _env_truthy("ENABLE_MOCK_AUTH")


def load_platform_env_safely(env_path: Path) -> None:
    """Load platform.env without clobbering protected operator auth settings."""
    if not env_path.is_file():
        return
    for key, value in dotenv_values(env_path).items():
        if value is None:
            continue
        if key in PROTECTED_AUTH_ENV_KEYS and os.environ.get(key, "").strip():
            logger.info(
                "auth_env_guard: keeping operator value for %s (not overriding from platform.env)",
                key,
            )
            continue
        os.environ[key] = value


def reconcile_auth_environment() -> None:
    """Fail closed: real OIDC configuration must not coexist with mock auth."""
    if operator_configured_real_auth() and _env_truthy("ENABLE_MOCK_AUTH"):
        logger.warning(
            "auth_env_guard: disabling ENABLE_MOCK_AUTH because OIDC issuer and POLICY_AUD are configured"
        )
        os.environ["ENABLE_MOCK_AUTH"] = "false"

    app_env = os.environ.get("APP_ENV", "").strip().lower()
    if app_env in ("production", "prod") and _env_truthy("ENABLE_MOCK_AUTH"):
        logger.warning("auth_env_guard: disabling ENABLE_MOCK_AUTH because APP_ENV=production")
        os.environ["ENABLE_MOCK_AUTH"] = "false"
