"""Plan-then-build for complex Lab apps."""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any, Optional

from llm_json import MODEL_GATEWAY_API_KEY, MODEL_GATEWAY_URL, extract_json
from model_catalog import platform_default_model

if TYPE_CHECKING:
    from apps_models import App

logger = logging.getLogger(__name__)

_PLAN_MODEL = os.environ.get("LAB_PLAN_MODEL", platform_default_model())


def load_build_plan(meta: dict) -> Optional[dict]:
    plan = meta.get("build_plan")
    return plan if isinstance(plan, dict) else None


def is_plan_ready(meta: dict) -> bool:
    return bool(meta.get("plan_ready"))


def is_plan_implemented(meta: dict) -> bool:
    return bool(meta.get("plan_implemented"))


def set_plan_ready(app_id: str, ready: bool) -> None:
    _update_plan_meta(app_id, {"plan_ready": ready})


def set_plan_implemented(app_id: str, implemented: bool) -> None:
    _update_plan_meta(app_id, {"plan_implemented": implemented})


def store_plan_meta(app_id: str, updates: dict) -> None:
    _update_plan_meta(app_id, updates)


def _update_plan_meta(app_id: str, updates: dict) -> None:
    from api_apps import _load_classification_meta
    from database import SessionLocal
    from apps_models import App

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            return
        meta = _load_classification_meta(app)
        meta.update(updates)
        app.classification_meta = json.dumps(meta, separators=(",", ":"))
        db.commit()
    finally:
        db.close()


def set_lab_mode(app_id: str, mode: str) -> None:
    from apps_models import App, LAB_MODES
    from database import SessionLocal

    if mode not in LAB_MODES:
        raise ValueError(f"lab_mode must be one of {LAB_MODES}")
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if app:
            app.lab_mode = mode
            db.commit()
    finally:
        db.close()


def should_plan_first(app: "App", history: list, message: str) -> bool:
    """Deprecated — plan mode handles planning before agent implementation."""
    del app, history, message
    return False


async def generate_build_plan(app: "App", user_message: str) -> dict:
    """Generate a feature checklist for plan-then-build."""
    if not MODEL_GATEWAY_API_KEY:
        return _fallback_plan(app, user_message)

    import anthropic

    tech = getattr(app, "tech_stack", "python") or "python"
    client = anthropic.AsyncAnthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)

    prompt = f"""Create an implementation plan for a Lab app.

App name: {app.name}
Description: {app.description or ""}
Tech stack: {tech}
User request: {user_message}

Return ONLY JSON:
{{
  "features": [
    {{
      "title": "short feature name",
      "description": "what to implement",
      "files": ["paths to create or modify"]
    }}
  ],
  "api_functions": ["server.py function names if any"]
}}

Keep 2-6 features, ordered for incremental delivery. First feature should be shell/layout."""

    try:
        response = await client.messages.create(
            model=_PLAN_MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if getattr(block, "type", "") == "text":
                parsed = extract_json(block.text or "")
                if isinstance(parsed, dict) and isinstance(parsed.get("features"), list):
                    return _normalize_plan(parsed)
    except Exception:
        logger.exception("generate_build_plan failed for app %s", app.id)

    return _fallback_plan(app, user_message)


def _fallback_plan(app: "App", user_message: str) -> dict:
    tech = getattr(app, "tech_stack", "python") or "python"
    if tech == "react":
        return {
            "features": [
                {
                    "title": "App shell and routing",
                    "description": "Set up layout, navigation, and route structure.",
                    "files": ["__src__/src/App.tsx"],
                },
                {
                    "title": "Core functionality",
                    "description": user_message[:500],
                    "files": ["__src__/src/components/"],
                },
            ],
            "api_functions": ["health_check"],
        }
    return {
        "features": [
            {
                "title": "App layout",
                "description": "Build index.html shell and basic styling.",
                "files": ["index.html", "style.css"],
            },
            {
                "title": "Core functionality",
                "description": user_message[:500],
                "files": ["app.js", "server.py"],
            },
        ],
        "api_functions": ["health_check"],
    }


def _normalize_plan(raw: dict) -> dict:
    features = []
    for item in raw.get("features") or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "Feature").strip()[:120]
        desc = str(item.get("description") or "").strip()[:800]
        files = [str(f).strip() for f in (item.get("files") or []) if str(f).strip()][:20]
        features.append({"title": title, "description": desc, "files": files})
    if not features:
        features = [{"title": "Implement app", "description": "Build the requested app.", "files": []}]
    api_fns = [str(f).strip() for f in (raw.get("api_functions") or []) if str(f).strip()][:20]
    return {"features": features[:8], "api_functions": api_fns}


def store_build_plan(app_id: str, plan: dict) -> None:
    from api_apps import _load_classification_meta
    from database import SessionLocal
    from apps_models import App

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            return
        meta = _load_classification_meta(app)
        meta["build_plan"] = plan
        app.classification_meta = json.dumps(meta, separators=(",", ":"))
        db.commit()
    finally:
        db.close()


def feature_user_message(
    feature: dict[str, Any],
    plan: dict,
    original_message: str,
    *,
    feature_index: int,
    total_features: int,
) -> str:
    files_hint = ", ".join(feature.get("files") or []) or "(see plan)"
    api_fns = ", ".join(plan.get("api_functions") or []) or "none"
    return (
        f"[Feature {feature_index + 1}/{total_features}: {feature.get('title', 'Feature')}]\n"
        f"{feature.get('description', '')}\n\n"
        f"Target files: {files_hint}\n"
        f"Planned API functions: {api_fns}\n\n"
        f"Original request: {original_message}\n\n"
        "Implement ONLY this feature. Do not remove work from previous features."
    )
