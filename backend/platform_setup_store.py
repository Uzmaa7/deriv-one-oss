"""Load, save, and apply first-run platform setup."""

from __future__ import annotations

import base64
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from database import SessionLocal
from setup_models import PlatformSetup

logger = logging.getLogger(__name__)

_RUNTIME_BRANDING: dict[str, str] = {}
_SETUP_ROW_CACHE: Optional[dict[str, Any]] = None

SETUP_ID = "default"


def _platform_env_path() -> Path:
    explicit = os.environ.get("PLATFORM_ENV_PATH", "").strip()
    if explicit:
        return Path(explicit).resolve()
    hub = os.environ.get("HUB_DATABASE_PATH", "").strip()
    if hub:
        return Path(hub).resolve().parent / "platform.env"
    return Path(__file__).resolve().parent / "platform.env"


def _directory_file_path() -> Path:
    raw = os.environ.get("DIRECTORY_FILE", "data/directory.yaml").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent.parent / raw
    return path.resolve()


def load_platform_env_file() -> None:
    """Merge persisted wizard env overrides (called during app startup)."""
    path = _platform_env_path()
    if not path.is_file():
        return
    try:
        from dotenv import dotenv_values

        for key, value in dotenv_values(path).items():
            if value is not None and key not in os.environ:
                os.environ[key] = value
        from model_gateway import apply_gateway_env_defaults

        apply_gateway_env_defaults()
        logger.info("Loaded platform env overrides from %s", path)
    except Exception as exc:
        logger.warning("Could not load %s: %s", path, exc)


def _row_to_dict(row: PlatformSetup) -> dict[str, Any]:
    return {
        "completed": bool(row.completed),
        "deployment_mode": row.deployment_mode,
        "draft": row.draft_json if isinstance(row.draft_json, dict) else {},
        "completed_at": row.completed_at.isoformat() if row.completed_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def get_setup_row() -> Optional[dict[str, Any]]:
    global _SETUP_ROW_CACHE
    if _SETUP_ROW_CACHE is not None:
        return _SETUP_ROW_CACHE
    db = SessionLocal()
    try:
        row = db.get(PlatformSetup, SETUP_ID)
        if row is None:
            return None
        _SETUP_ROW_CACHE = _row_to_dict(row)
        return _SETUP_ROW_CACHE
    finally:
        db.close()


def invalidate_setup_cache() -> None:
    global _SETUP_ROW_CACHE
    _SETUP_ROW_CACHE = None


def is_setup_complete() -> bool:
    if os.environ.get("PLATFORM_SETUP_COMPLETE", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return True

    row = get_setup_row()
    if row and row.get("completed"):
        return True

    # Existing deployments that already configured production auth + models.
    mock = os.environ.get("ENABLE_MOCK_AUTH", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if not mock:
        issuer = (
            os.environ.get("OIDC_ISSUER", "").strip()
            or os.environ.get("TEAM_DOMAIN", "").strip()
        )
        if (
            issuer
            and os.environ.get("POLICY_AUD", "").strip()
            and os.environ.get("MODEL_GATEWAY_URL", "").strip()
            and os.environ.get("SECRET_ENCRYPTION_KEY", "").strip()
        ):
            return True

    return False


def save_draft(draft: dict[str, Any], deployment_mode: Optional[str] = None) -> dict[str, Any]:
    invalidate_setup_cache()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        row = db.get(PlatformSetup, SETUP_ID)
        if row is None:
            row = PlatformSetup(id=SETUP_ID)
            db.add(row)
        row.draft_json = draft
        if deployment_mode:
            row.deployment_mode = deployment_mode
        row.updated_at = now
        db.commit()
        db.refresh(row)
        result = _row_to_dict(row)
        _SETUP_ROW_CACHE = result
        return result
    finally:
        db.close()


def apply_runtime_branding(draft: dict[str, Any]) -> None:
    global _RUNTIME_BRANDING
    branding = draft.get("branding") if isinstance(draft.get("branding"), dict) else {}
    mapping = {
        "app_name": branding.get("app_name"),
        "app_short_name": branding.get("app_short_name"),
        "app_logo_url": branding.get("app_logo_url"),
        "org_noun": branding.get("org_noun"),
        "brand_color": branding.get("brand_color"),
        "brand_color_hover": branding.get("brand_color_hover"),
        "brand_color_soft": branding.get("brand_color_soft"),
    }
    _RUNTIME_BRANDING = {k: str(v).strip() for k, v in mapping.items() if v}


def get_runtime_branding() -> dict[str, str]:
    if _RUNTIME_BRANDING:
        return dict(_RUNTIME_BRANDING)
    row = get_setup_row()
    if row and isinstance(row.get("draft"), dict):
        apply_runtime_branding(row["draft"])
    return dict(_RUNTIME_BRANDING)


def branding_value(key: str, env_default: str) -> str:
    runtime = get_runtime_branding()
    env_key = {
        "app_name": "APP_NAME",
        "app_short_name": "APP_SHORT_NAME",
        "app_logo_url": "APP_LOGO_URL",
        "org_noun": "ORG_NOUN",
        "brand_color": "BRAND_COLOR",
        "brand_color_hover": "BRAND_COLOR_HOVER",
        "brand_color_soft": "BRAND_COLOR_SOFT",
    }.get(key, "")
    if key in runtime and runtime[key]:
        return runtime[key]
    if env_key:
        val = os.environ.get(env_key, "").strip()
        if val:
            return val
    return env_default


def _write_env_file(values: dict[str, str]) -> Path:
    path = _platform_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Written by the platform setup wizard or setup_cli.py. Restart the backend to apply all values.",
        f"# Generated at {datetime.now(timezone.utc).isoformat()}",
        "",
    ]
    for key in sorted(values):
        val = values[key]
        if val == "":
            continue
        safe = val.replace("\n", "").replace("\r", "")
        if any(c in safe for c in ('"', " ", "#")):
            lines.append(f'{key}="{safe}"')
        else:
            lines.append(f"{key}={safe}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_directory_file(content: str) -> Path:
    path = _directory_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def build_env_from_draft(draft: dict[str, Any]) -> dict[str, str]:
    branding = draft.get("branding") if isinstance(draft.get("branding"), dict) else {}
    models = draft.get("models") if isinstance(draft.get("models"), dict) else {}
    identity = draft.get("identity") if isinstance(draft.get("identity"), dict) else {}
    directory = draft.get("directory") if isinstance(draft.get("directory"), dict) else {}
    slack = draft.get("slack") if isinstance(draft.get("slack"), dict) else {}
    core = draft.get("core") if isinstance(draft.get("core"), dict) else {}

    mode = str(draft.get("deployment_mode") or "personal")
    env: dict[str, str] = {}

    if branding.get("app_name"):
        env["APP_NAME"] = str(branding["app_name"]).strip()
    if branding.get("app_short_name"):
        env["APP_SHORT_NAME"] = str(branding["app_short_name"]).strip()
    if branding.get("app_logo_url"):
        env["APP_LOGO_URL"] = str(branding["app_logo_url"]).strip()
    if branding.get("org_noun"):
        env["ORG_NOUN"] = str(branding["org_noun"]).strip()
    if branding.get("brand_color"):
        env["BRAND_COLOR"] = str(branding["brand_color"]).strip()
    if branding.get("brand_color_hover"):
        env["BRAND_COLOR_HOVER"] = str(branding["brand_color_hover"]).strip()
    if branding.get("brand_color_soft"):
        env["BRAND_COLOR_SOFT"] = str(branding["brand_color_soft"]).strip()

    if core.get("app_base_url"):
        env["APP_BASE_URL"] = str(core["app_base_url"]).strip().rstrip("/")
    if core.get("resource_prefix"):
        env["RESOURCE_PREFIX"] = str(core["resource_prefix"]).strip()

    if models.get("gateway_url"):
        gateway_url = str(models["gateway_url"]).strip()
        env["MODEL_GATEWAY_URL"] = gateway_url
        gateway_key = str(models.get("gateway_api_key") or "").strip()
        if gateway_key:
            env["MODEL_GATEWAY_API_KEY"] = gateway_key
        else:
            from model_gateway import LOCAL_GATEWAY_KEY_PLACEHOLDER, is_likely_local_gateway

            if is_likely_local_gateway(gateway_url):
                env["MODEL_GATEWAY_API_KEY"] = LOCAL_GATEWAY_KEY_PLACEHOLDER
    if models.get("encryption_key"):
        env["SECRET_ENCRYPTION_KEY"] = str(models["encryption_key"]).strip()

    if mode == "personal":
        from auth_env_guard import mock_auth_allowed_for_setup

        env["ENABLE_MOCK_AUTH"] = "true" if mock_auth_allowed_for_setup(draft) else "false"
        env["APP_ENV"] = str(core.get("app_env") or "development")
        if identity.get("mock_user_email"):
            env["MOCK_USER_EMAIL"] = str(identity["mock_user_email"]).strip()
        if identity.get("mock_user_groups"):
            env["MOCK_USER_GROUPS"] = str(identity["mock_user_groups"]).strip()
    else:
        env["ENABLE_MOCK_AUTH"] = "false"
        env["APP_ENV"] = str(core.get("app_env") or "production")
        if identity.get("oidc_issuer"):
            issuer = str(identity["oidc_issuer"]).strip().rstrip("/")
            env["OIDC_ISSUER"] = issuer
            env["TEAM_DOMAIN"] = issuer
        if identity.get("policy_aud"):
            env["POLICY_AUD"] = str(identity["policy_aud"]).strip()
        if identity.get("oidc_jwks_url"):
            env["OIDC_JWKS_URL"] = str(identity["oidc_jwks_url"]).strip()
        if identity.get("admin_groups"):
            env["ADMIN_GROUPS"] = str(identity["admin_groups"]).strip()
        if identity.get("member_groups"):
            env["MEMBER_GROUPS"] = str(identity["member_groups"]).strip()

    provider = str(directory.get("provider") or "claims").strip()
    if provider == "file" and not str(directory.get("file_content") or "").strip():
        provider = "claims"
    env["DIRECTORY_PROVIDER"] = provider
    if provider == "file":
        env["DIRECTORY_FILE"] = str(
            directory.get("file_path") or os.environ.get("DIRECTORY_FILE") or "data/directory.yaml"
        )

    slack_mode = str(slack.get("mode") or "skip")
    if slack_mode == "auto_create":
        if slack.get("config_collaborator"):
            env["SLACK_CONFIG_COLLABORATOR"] = str(slack["config_collaborator"]).strip()
        if slack.get("config_access_token"):
            env["SLACK_APP_CONFIGURATION_ACCESS_TOKEN"] = str(slack["config_access_token"]).strip()
        if slack.get("config_refresh_token"):
            env["SLACK_APP_CONFIGURATION_REFRESH_TOKEN"] = str(
                slack["config_refresh_token"]
            ).strip()
        if slack.get("ip_allowlist"):
            env["SLACK_MANIFEST_IP_ALLOWLIST"] = str(slack["ip_allowlist"]).strip()

    env["PLATFORM_SETUP_COMPLETE"] = "1"
    return env


def complete_setup(draft: dict[str, Any]) -> dict[str, Any]:
    """Persist wizard completion, write env/directory files, apply branding."""
    invalidate_setup_cache()
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    env_values = build_env_from_draft(draft)

    directory = draft.get("directory") if isinstance(draft.get("directory"), dict) else {}
    if str(directory.get("provider") or "") == "file" and directory.get("file_content"):
        dir_path = _write_directory_file(str(directory["file_content"]))
        env_values["DIRECTORY_FILE"] = str(dir_path)

    env_path = _write_env_file(env_values)
    apply_runtime_branding(draft)

    for key, value in env_values.items():
        if key.endswith("_API_KEY") or key.endswith("_TOKEN") or "SECRET" in key:
            continue
        os.environ[key] = value

    from auth_env_guard import reconcile_auth_environment
    from cf_access import reload_auth_env_flags

    reconcile_auth_environment()
    reload_auth_env_flags()

    db = SessionLocal()
    try:
        row = db.get(PlatformSetup, SETUP_ID)
        if row is None:
            row = PlatformSetup(id=SETUP_ID)
            db.add(row)
        row.completed = True
        row.completed_at = now
        row.updated_at = now
        row.deployment_mode = str(draft.get("deployment_mode") or "personal")
        row.draft_json = draft
        db.commit()
        db.refresh(row)
        result = _row_to_dict(row)
        _SETUP_ROW_CACHE = result
    finally:
        db.close()

    directory_path = (
        str(_directory_file_path())
        if str(directory.get("provider") or "") == "file"
        else None
    )
    return _build_completion_payload(draft, env_path, directory_path)


def _detect_runtime_environment() -> str:
    if Path("/.dockerenv").is_file():
        return "docker"
    return "local"


def _build_completion_payload(
    draft: dict[str, Any],
    env_path: Path,
    directory_path: Optional[str] = None,
) -> dict[str, Any]:
    mode = str(draft.get("deployment_mode") or "personal")
    runtime = _detect_runtime_environment()
    branding = draft.get("branding") if isinstance(draft.get("branding"), dict) else {}
    app_name = str(branding.get("app_name") or "the platform").strip()

    applied_immediately = [
        "Platform branding and name",
        "Setup completion flag in the database",
    ]
    if directory_path:
        applied_immediately.append("Org directory file")

    requires_restart_for = [
        "Model gateway connection",
        "Secret encryption key",
        "Identity and auth settings",
    ]
    slack = draft.get("slack") if isinstance(draft.get("slack"), dict) else {}
    if str(slack.get("mode") or "") == "auto_create":
        requires_restart_for.append("Slack integration tokens")

    if runtime == "docker":
        restart_commands = {
            "primary": "docker compose restart backend",
            "note": "Wait for the backend health check to pass, then refresh this page.",
        }
    else:
        restart_commands = {
            "backend": (
                "cd backend && .venv/bin/python -m uvicorn main:app "
                "--reload --host 127.0.0.1 --port 8001"
            ),
            "frontend": "DEV_API_PORT=8001 npm run dev",
            "note": (
                "Stop and restart the backend process so it loads platform.env. "
                "Keep the frontend running, or restart it if the API proxy was down."
            ),
        }

    next_steps: list[dict[str, str]] = [
        {
            "id": "restart",
            "title": "Restart the backend",
            "description": (
                "Configuration was saved to platform.env. The running process must "
                "restart to load model gateway, encryption, and auth settings."
            ),
        },
        {
            "id": "refresh",
            "title": "Refresh this page",
            "description": (
                "After the backend is back, refresh so branding updates and Workspace "
                "can create persisted chats."
            ),
        },
        {
            "id": "workspace",
            "title": "Open Workspace and send a message",
            "description": (
                f"Create a new chat in {app_name} to confirm the model gateway is working."
            ),
        },
    ]
    if mode == "team":
        next_steps.insert(
            2,
            {
                "id": "identity",
                "title": "Sign in through your identity provider",
                "description": (
                    "Team mode uses OIDC — access the app through your configured "
                    "identity proxy or SSO front door."
                ),
            },
        )

    return {
        "completed": True,
        "env_path": str(env_path),
        "directory_path": directory_path,
        "deployment_mode": mode,
        "runtime_environment": runtime,
        "restart_required": True,
        "applied_immediately": applied_immediately,
        "requires_restart_for": requires_restart_for,
        "restart_commands": restart_commands,
        "next_steps": next_steps,
    }


def generate_encryption_key() -> str:
    return base64.b64encode(secrets.token_bytes(32)).decode("ascii")


def health_snapshot() -> dict[str, Any]:
    blob_store = (os.environ.get("BLOB_STORE", "local") or "local").strip()
    sandbox_provider = (os.environ.get("SANDBOX_PROVIDER", "container") or "container").strip()
    slack_auto = bool(
        os.environ.get("SLACK_APP_CONFIGURATION_ACCESS_TOKEN", "").strip()
        and os.environ.get("SLACK_APP_CONFIGURATION_REFRESH_TOKEN", "").strip()
    )
    slack_collaborator = bool(os.environ.get("SLACK_CONFIG_COLLABORATOR", "").strip())
    directory_provider = (os.environ.get("DIRECTORY_PROVIDER", "claims") or "claims").strip()
    return {
        "model_gateway_configured": bool(os.environ.get("MODEL_GATEWAY_URL", "").strip()),
        "encryption_key_configured": bool(os.environ.get("SECRET_ENCRYPTION_KEY", "").strip()),
        "mock_auth": os.environ.get("ENABLE_MOCK_AUTH", "").strip().lower()
        in ("1", "true", "yes", "on"),
        "oidc_configured": bool(
            (
                os.environ.get("OIDC_ISSUER", "").strip()
                or os.environ.get("TEAM_DOMAIN", "").strip()
            )
            and os.environ.get("POLICY_AUD", "").strip()
        ),
        "blob_store": blob_store,
        "blob_configured": bool(blob_store),
        "sandbox_provider": sandbox_provider,
        "sandbox_configured": sandbox_provider in {"container", "subprocess", "k8s"},
        "slack_auto_create_configured": slack_auto,
        "slack_collaborator_configured": slack_collaborator,
        "directory_provider": directory_provider,
        "platform_env_path": str(_platform_env_path()),
        "platform_env_exists": _platform_env_path().is_file(),
        "runtime_environment": _detect_runtime_environment(),
    }
