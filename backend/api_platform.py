"""Public platform configuration for white-label branding and feature flags."""

from __future__ import annotations

from fastapi import APIRouter

import os

from app_config import (
    APP_LOGO_URL,
    APP_NAME,
    APP_SHORT_NAME,
    BRAND_COLOR,
    BRAND_COLOR_HOVER,
    BRAND_COLOR_SOFT,
    ORG_NOUN,
)
from platform_setup_store import branding_value, is_setup_complete
from slack_oauth import slack_config_collaborator, slack_platform_tokens_configured

router = APIRouter()


@router.get("/api/platform/config")
def platform_config() -> dict:
    """Runtime branding and integration flags for the web UI (no auth required)."""
    return {
        "app_name": branding_value("app_name", APP_NAME),
        "app_short_name": branding_value("app_short_name", APP_SHORT_NAME),
        "app_logo_url": branding_value("app_logo_url", APP_LOGO_URL),
        "brand_color": branding_value("brand_color", BRAND_COLOR),
        "brand_color_hover": branding_value("brand_color_hover", BRAND_COLOR_HOVER),
        "brand_color_soft": branding_value("brand_color_soft", BRAND_COLOR_SOFT),
        "org_noun": branding_value("org_noun", ORG_NOUN),
        "slack_auto_create_enabled": slack_platform_tokens_configured(),
        "slack_config_collaborator": slack_config_collaborator(),
        "setup_complete": is_setup_complete(),
        "directory_provider": (
            os.environ.get("DIRECTORY_PROVIDER", "claims") or "claims"
        ).strip(),
    }
