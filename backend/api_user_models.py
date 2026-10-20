"""Per-user workspace model preferences."""

from __future__ import annotations

import json
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from cf_access import AuthContext, build_auth_context
from fastapi import Request
from model_catalog import WORKSPACE_CHAT_DEFAULT_MODEL, get_model_catalog
from model_preferences import (
    catalog_chat_model_ids,
    model_preferences_payload,
    platform_enabled_models,
    resolve_user_enabled_models,
)
from user_profile import get_or_create_user_profile, get_user_profile

logger = logging.getLogger(__name__)
router = APIRouter()


def _auth(request: Request) -> AuthContext:
    try:
        return build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


async def _gateway_catalog():
    import os

    from model_gateway import resolved_gateway_api_key

    api_key = resolved_gateway_api_key(
        os.environ.get("MODEL_GATEWAY_API_KEY"),
        os.environ.get("MODEL_GATEWAY_URL"),
    )
    base_url = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000/v1")
    return await get_model_catalog(
        api_key=api_key,
        base_url_raw=base_url,
        default_model=WORKSPACE_CHAT_DEFAULT_MODEL,
        force_refresh=False,
    )


class ModelPreferencesBody(BaseModel):
    enabled_models: list[str] = Field(default_factory=list)
    preferred_model: Optional[str] = None

    @field_validator("enabled_models")
    @classmethod
    def validate_enabled_models(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for raw in value:
            mid = str(raw).strip()
            if not mid or mid in seen:
                continue
            if len(mid) > 256:
                raise ValueError("model id is too long")
            seen.add(mid)
            out.append(mid)
        return out

    @field_validator("preferred_model")
    @classmethod
    def validate_preferred_model(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        trimmed = value.strip()
        if not trimmed:
            return None
        if len(trimmed) > 256:
            raise ValueError("preferred_model is too long")
        return trimmed


@router.get("/api/me/model-preferences")
async def get_model_preferences(auth: AuthContext = Depends(_auth)) -> dict:
    catalog = await _gateway_catalog()
    profile = get_user_profile(auth.sub)
    return model_preferences_payload(catalog, profile=profile)


@router.put("/api/me/model-preferences")
async def update_model_preferences(
    body: ModelPreferencesBody,
    auth: AuthContext = Depends(_auth),
) -> dict:
    catalog = await _gateway_catalog()
    allowed_ids = set(catalog_chat_model_ids(catalog))
    platform_allowed = platform_enabled_models()
    if platform_allowed is not None:
        allowed_ids &= platform_allowed

    if not body.enabled_models:
        raise HTTPException(status_code=422, detail="Select at least one model.")

    unknown = [mid for mid in body.enabled_models if mid not in allowed_ids]
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown or unavailable models: {', '.join(unknown[:5])}",
        )

    preferred = body.preferred_model
    if preferred and preferred not in body.enabled_models:
        raise HTTPException(
            status_code=422,
            detail="preferred_model must be one of the enabled models.",
        )

    from datetime import datetime

    from database import SessionLocal, UserProfile

    db = SessionLocal()
    try:
        row = db.get(UserProfile, auth.sub)
        if row is None:
            row = get_or_create_user_profile(auth.sub)
            row = db.get(UserProfile, auth.sub)
        if row is None:
            raise HTTPException(status_code=500, detail="Could not load user profile.")
        row.enabled_models_json = json.dumps(body.enabled_models)
        row.preferred_model = preferred
        row.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(row)
        db_profile = row
    finally:
        db.close()

    return model_preferences_payload(catalog, profile=db_profile)


@router.delete("/api/me/model-preferences")
async def reset_model_preferences(auth: AuthContext = Depends(_auth)) -> dict:
    """Clear custom selection — show every model allowed by the platform."""
    catalog = await _gateway_catalog()
    from datetime import datetime

    from database import SessionLocal, UserProfile

    profile = get_user_profile(auth.sub)
    db = SessionLocal()
    try:
        row = db.get(UserProfile, auth.sub)
        if row is not None:
            row.enabled_models_json = None
            row.preferred_model = None
            row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
            profile = row
    finally:
        db.close()

    return model_preferences_payload(catalog, profile=profile)
