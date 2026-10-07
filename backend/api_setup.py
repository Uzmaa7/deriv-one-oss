"""First-run platform setup wizard API (admin only)."""

from __future__ import annotations

import logging
from typing import Any, Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from cf_access import resolve_jwks_uri
from platform_setup_store import (
    complete_setup,
    generate_encryption_key,
    get_setup_row,
    health_snapshot,
    is_setup_complete,
    save_draft,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _auth(request: Request):
    from cf_access import build_auth_context

    try:
        return build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _admin(request: Request):
    ctx = _auth(request)
    if not ctx.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required.")
    return ctx


class SetupDraftBody(BaseModel):
    deployment_mode: Optional[str] = None
    draft: dict[str, Any] = Field(default_factory=dict)


class ValidateModelsBody(BaseModel):
    gateway_url: str
    gateway_api_key: str = ""


class ValidateOidcBody(BaseModel):
    oidc_issuer: str
    policy_aud: str = ""
    oidc_jwks_url: str = ""


class CompleteSetupBody(BaseModel):
    draft: dict[str, Any]


@router.get("/api/admin/setup/status")
def setup_status(_: Any = Depends(_admin)) -> dict[str, Any]:
    row = get_setup_row()
    return {
        "completed": is_setup_complete(),
        "row": row,
        "health": health_snapshot(),
    }


@router.get("/api/platform/setup-status")
def public_setup_status() -> dict[str, bool]:
    """Lightweight flag for UI routing (no auth)."""
    return {"setup_complete": is_setup_complete()}


@router.post("/api/admin/setup/draft")
def save_setup_draft(body: SetupDraftBody, _: Any = Depends(_admin)) -> dict[str, Any]:
    return save_draft(body.draft, body.deployment_mode)


@router.post("/api/admin/setup/validate/models")
async def validate_models(body: ValidateModelsBody, _: Any = Depends(_admin)) -> dict[str, Any]:
    url = body.gateway_url.strip().rstrip("/")
    if not url:
        raise HTTPException(status_code=422, detail="gateway_url is required")
    from model_gateway import gateway_auth_headers, resolve_models_list_url
    from url_fetch_guard import async_fetch_response, validate_fetch_url

    models_url = resolve_models_list_url(url)
    ok, err = validate_fetch_url(models_url)
    if not ok:
        return {"ok": False, "error": err, "models_url": models_url}

    try:
        resp = await async_fetch_response(
            models_url,
            headers=gateway_auth_headers(body.gateway_api_key),
            timeout=15.0,
        )
        if resp.status_code >= 400:
            return {
                "ok": False,
                "error": f"Gateway returned HTTP {resp.status_code}",
                "models_url": models_url,
            }
        data = resp.json()
        model_ids = [
            m.get("id")
            for m in (data.get("data") or [])
            if isinstance(m, dict) and m.get("id")
        ]
        return {
            "ok": True,
            "models_url": models_url,
            "model_count": len(model_ids),
            "sample_models": model_ids[:8],
        }
    except Exception as exc:
        logger.warning("setup model gateway validation failed: %s", exc)
        return {"ok": False, "error": str(exc), "models_url": models_url}


@router.post("/api/admin/setup/validate/oidc")
def validate_oidc(body: ValidateOidcBody, _: Any = Depends(_admin)) -> dict[str, Any]:
    issuer = body.oidc_issuer.strip().rstrip("/")
    if not issuer:
        raise HTTPException(status_code=422, detail="oidc_issuer is required")

    prev_issuer = __import__("os").environ.get("OIDC_ISSUER")
    prev_team = __import__("os").environ.get("TEAM_DOMAIN")
    prev_jwks = __import__("os").environ.get("OIDC_JWKS_URL")
    import os

    try:
        os.environ["OIDC_ISSUER"] = issuer
        os.environ["TEAM_DOMAIN"] = issuer
        if body.oidc_jwks_url.strip():
            os.environ["OIDC_JWKS_URL"] = body.oidc_jwks_url.strip()
        import cf_access

        cf_access._resolved_jwks_uri = None
        jwks_uri = resolve_jwks_uri()
        return {"ok": True, "jwks_uri": jwks_uri, "issuer": issuer}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "issuer": issuer}
    finally:
        import cf_access

        cf_access._resolved_jwks_uri = None
        for key, val in (
            ("OIDC_ISSUER", prev_issuer),
            ("TEAM_DOMAIN", prev_team),
            ("OIDC_JWKS_URL", prev_jwks),
        ):
            if val is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = val


@router.post("/api/admin/setup/generate-encryption-key")
def setup_generate_key(_: Any = Depends(_admin)) -> dict[str, str]:
    return {"encryption_key": generate_encryption_key()}


@router.post("/api/admin/setup/verify")
async def verify_setup(_: Any = Depends(_admin)) -> dict[str, Any]:
    """Check whether a restarted backend has loaded platform.env and can reach the model gateway."""
    health = health_snapshot()
    models_reachable = False
    models_error: Optional[str] = None
    model_count = 0

    from model_gateway import gateway_auth_headers, resolve_models_list_url

    gateway_url = __import__("os").environ.get("MODEL_GATEWAY_URL", "").strip().rstrip("/")
    gateway_key = __import__("os").environ.get("MODEL_GATEWAY_API_KEY", "").strip()
    if gateway_url:
        models_url = resolve_models_list_url(gateway_url)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    models_url,
                    headers=gateway_auth_headers(gateway_key),
                )
            if resp.status_code < 400:
                data = resp.json()
                model_count = len(
                    [
                        m.get("id")
                        for m in (data.get("data") or [])
                        if isinstance(m, dict) and m.get("id")
                    ]
                )
                models_reachable = model_count > 0
                if not models_reachable:
                    models_error = "Gateway responded but returned no models."
            else:
                models_error = f"Gateway returned HTTP {resp.status_code}"
        except Exception as exc:
            models_error = str(exc)
    elif not health["model_gateway_configured"]:
        models_error = "Model gateway is not loaded yet — restart the backend."

    identity_ready = health["mock_auth"] or health["oidc_configured"]
    checks = {
        "gateway": {
            "status": "ok" if models_reachable else "fail",
            "label": "Model gateway",
            "detail": (
                f"{model_count} models reachable"
                if models_reachable
                else (models_error or "Gateway not reachable")
            ),
        },
        "encryption": {
            "status": "ok" if health["encryption_key_configured"] else "fail",
            "label": "Secret encryption key",
            "detail": "Loaded from environment" if health["encryption_key_configured"] else "Missing SECRET_ENCRYPTION_KEY",
        },
        "identity": {
            "status": "ok" if identity_ready else "fail",
            "label": "Authentication",
            "detail": (
                "Mock auth (development)"
                if health["mock_auth"]
                else ("OIDC issuer + audience configured" if health["oidc_configured"] else "Configure mock auth or OIDC")
            ),
        },
        "sandbox": {
            "status": "ok" if health.get("sandbox_configured") else "warn",
            "label": "Code sandbox",
            "detail": f"Provider: {health.get('sandbox_provider', 'unknown')}",
        },
        "blob": {
            "status": "ok" if health.get("blob_configured") else "fail",
            "label": "Blob storage",
            "detail": f"Provider: {health.get('blob_store', 'unknown')}",
        },
        "slack": {
            "status": (
                "ok"
                if health.get("slack_auto_create_configured") or health.get("slack_collaborator_configured")
                else "skip"
            ),
            "label": "Slack auto-create",
            "detail": (
                "Org tokens + collaborator configured"
                if health.get("slack_auto_create_configured") and health.get("slack_collaborator_configured")
                else (
                    "Collaborator configured"
                    if health.get("slack_collaborator_configured")
                    else (
                        "Platform tokens configured"
                        if health.get("slack_auto_create_configured")
                        else "Optional — manual Slack apps work without this"
                    )
                )
            ),
        },
        "directory": {
            "status": "ok",
            "label": "Org directory",
            "detail": f"Provider: {health.get('directory_provider', 'claims')}",
        },
    }
    ready = (
        health["model_gateway_configured"]
        and health["encryption_key_configured"]
        and identity_ready
        and models_reachable
        and health.get("blob_configured", True)
    )
    return {
        "ready": ready,
        "health": health,
        "checks": checks,
        "models_reachable": models_reachable,
        "model_count": model_count,
        "models_error": models_error,
    }


@router.post("/api/admin/setup/complete")
def setup_complete(body: CompleteSetupBody, _: Any = Depends(_admin)) -> dict[str, Any]:
    draft = body.draft
    mode = str(draft.get("deployment_mode") or "personal")

    models = draft.get("models") if isinstance(draft.get("models"), dict) else {}
    if not str(models.get("gateway_url") or "").strip():
        raise HTTPException(status_code=422, detail="Model gateway URL is required")
    gateway_url = str(models.get("gateway_url") or "").strip()
    gateway_key = str(models.get("gateway_api_key") or "").strip()
    if not gateway_key:
        from model_gateway import probe_gateway

        ok, msg, _ = probe_gateway(gateway_url, "")
        if not ok:
            raise HTTPException(
                status_code=422,
                detail=(
                    "Model gateway API key is required for this URL, or the gateway must be "
                    f"reachable without auth. ({msg})"
                ),
            )
    if not str(models.get("encryption_key") or "").strip():
        raise HTTPException(status_code=422, detail="Secret encryption key is required")

    identity = draft.get("identity") if isinstance(draft.get("identity"), dict) else {}
    if mode == "team":
        if not str(identity.get("oidc_issuer") or "").strip():
            raise HTTPException(status_code=422, detail="OIDC issuer is required for team mode")
        if not str(identity.get("policy_aud") or "").strip():
            raise HTTPException(status_code=422, detail="Policy audience is required for team mode")

    directory = draft.get("directory") if isinstance(draft.get("directory"), dict) else {}
    if str(directory.get("provider") or "") == "file" and not str(
        directory.get("file_content") or ""
    ).strip():
        raise HTTPException(
            status_code=422,
            detail="Directory file content is required when using file provider",
        )

    try:
        return complete_setup(draft)
    except Exception as exc:
        logger.exception("setup complete failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

