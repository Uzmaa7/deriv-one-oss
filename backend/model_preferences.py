"""User and platform model allowlists for workspace/agent pickers."""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

from model_catalog import (
    ModelCatalog,
    ModelCatalogEntry,
    is_embedding_model_id,
    pick_workspace_chat_model,
)

logger = logging.getLogger(__name__)


def parse_model_id_list(raw: Optional[str]) -> Optional[list[str]]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    if text.startswith("["):
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except (TypeError, ValueError):
            logger.warning("Could not parse model id JSON list")
    return [part.strip() for part in text.split(",") if part.strip()]


def env_model_allowlist(key: str) -> Optional[frozenset[str]]:
    ids = parse_model_id_list(__import__("os").environ.get(key))
    if not ids:
        return None
    return frozenset(ids)


def platform_enabled_models() -> Optional[frozenset[str]]:
    """Optional deployment-wide allowlist from ``WORKSPACE_ENABLED_MODELS``."""
    return env_model_allowlist("WORKSPACE_ENABLED_MODELS")


def catalog_chat_model_ids(
    catalog: ModelCatalog,
    *,
    include_embeddings: bool = False,
) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    for entry in catalog.all_entries or catalog.entries:
        mid = entry.id
        if not mid or mid in seen:
            continue
        if not include_embeddings and is_embedding_model_id(mid):
            continue
        seen.add(mid)
        ids.append(mid)
    return ids


def filter_catalog(
    catalog: ModelCatalog,
    *,
    allowlist: Optional[frozenset[str]] = None,
    exclude_embeddings: bool = True,
) -> ModelCatalog:
    """Return a catalog view restricted to an allowlist (if set)."""
    allowed = allowlist
    filtered: list[ModelCatalogEntry] = []
    for entry in catalog.entries:
        if not entry.id:
            continue
        if exclude_embeddings and is_embedding_model_id(entry.id):
            continue
        if allowed is not None and entry.id not in allowed:
            continue
        filtered.append(entry)

    all_filtered: list[ModelCatalogEntry] = []
    for entry in catalog.all_entries or catalog.entries:
        if not entry.id:
            continue
        if exclude_embeddings and is_embedding_model_id(entry.id):
            continue
        if allowed is not None and entry.id not in allowed:
            continue
        all_filtered.append(entry)

    if not filtered and all_filtered:
        filtered = list(all_filtered)

    default = catalog.default_model
    if default and allowed is not None and default not in allowed:
        try:
            default = pick_workspace_chat_model(
                ModelCatalog(
                    entries=filtered,
                    source=catalog.source,
                    cached_at=catalog.cached_at,
                    default_model="",
                    all_entries=all_filtered,
                ),
                None,
            )
        except ValueError:
            default = filtered[0].id if filtered else ""

    return ModelCatalog(
        entries=filtered,
        source=catalog.source,
        cached_at=catalog.cached_at,
        default_model=default,
        all_entries=all_filtered,
    )


def resolve_user_enabled_models(profile: Any) -> Optional[frozenset[str]]:
    raw = getattr(profile, "enabled_models_json", None) if profile else None
    ids = parse_model_id_list(raw)
    if not ids:
        return None
    return frozenset(ids)


def model_preferences_payload(
    catalog: ModelCatalog,
    *,
    profile: Any = None,
) -> dict[str, Any]:
    platform_allowed = platform_enabled_models()
    catalog_ids = catalog_chat_model_ids(catalog)
    if platform_allowed is not None:
        catalog_ids = [mid for mid in catalog_ids if mid in platform_allowed]

    user_enabled = resolve_user_enabled_models(profile)
    if user_enabled is not None:
        enabled_for_picker = [mid for mid in catalog_ids if mid in user_enabled]
    else:
        enabled_for_picker = catalog_ids

    preferred = (getattr(profile, "preferred_model", None) or "").strip() if profile else ""
    if preferred and preferred not in enabled_for_picker:
        preferred = ""

    picker_allowlist: Optional[frozenset[str]] = None
    if user_enabled is not None:
        picker_allowlist = frozenset(enabled_for_picker) if enabled_for_picker else frozenset()
    elif platform_allowed is not None:
        picker_allowlist = platform_allowed

    picker_catalog = filter_catalog(catalog, allowlist=picker_allowlist)

    default_model = preferred or picker_catalog.default_model
    if not default_model and enabled_for_picker:
        try:
            default_model = pick_workspace_chat_model(
                filter_catalog(
                    catalog,
                    allowlist=frozenset(catalog_ids) if catalog_ids else None,
                ),
                None,
            )
        except ValueError:
            default_model = enabled_for_picker[0]

    return {
        "catalog_models": catalog_ids,
        "enabled_models": enabled_for_picker,
        "user_has_custom_selection": user_enabled is not None,
        "platform_allowlist": sorted(platform_allowed) if platform_allowed else None,
        "preferred_model": preferred or None,
        "picker": picker_catalog.to_api_payload()
        | {"default": default_model},
    }
