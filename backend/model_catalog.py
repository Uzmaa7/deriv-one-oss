"""
Fetch and cache the model gateway model catalog for Workspace chat.

Models are discovered from the configured gateway (not hardcoded). When the
gateway is unreachable, serves the last good cache or a minimal emergency list
built from env-configured defaults.
"""
from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

logger = logging.getLogger(__name__)


def platform_default_model() -> str:
    """Platform-wide default model slug from ``MODEL`` (empty when unset)."""
    return os.environ.get("MODEL", "").strip()


# Workspace chat — override ``MODEL`` per surface when needed.
WORKSPACE_CHAT_DEFAULT_MODEL: str = (
    os.environ.get("WORKSPACE_CHAT_DEFAULT_MODEL", "").strip() or platform_default_model()
)
WORKSPACE_CHAT_FALLBACK_MODEL: str = (
    os.environ.get("WORKSPACE_CHAT_FALLBACK_MODEL", "").strip() or platform_default_model()
)

# Internal alias for workspace chat retry logic (never a global MODEL env override).
CHAT_FALLBACK_MODEL: str = WORKSPACE_CHAT_FALLBACK_MODEL

CACHE_TTL_SECONDS: int = int(os.environ.get("MODEL_CATALOG_TTL_SECONDS", "600"))

# ``hidden`` drops the internal chat fallback slug from the picker; ``all`` shows every id.
CATALOG_FILTER_MODE: str = os.environ.get("MODEL_CATALOG_FILTER", "all").strip().lower()

# "Auto" is a gateway-side router: the caller asks for a model chosen per turn
# rather than naming one. It is reachable under two ids; this is the one the UI
# and the catalogue use, and the alias set is what recognises either form.
AUTO_MODEL_CANONICAL: str = os.environ.get("AUTO_MODEL_ID", "auto").strip() or "auto"

_AUTO_ALIASES: frozenset[str] = frozenset({"auto", AUTO_MODEL_CANONICAL})


def _emergency_ui_models() -> tuple[str, ...]:
    """Minimal picker when the gateway is down and cache is empty."""
    ids: list[str] = []
    for candidate in (WORKSPACE_CHAT_DEFAULT_MODEL, AUTO_MODEL_CANONICAL):
        if candidate and candidate not in ids:
            ids.append(candidate)
    return tuple(ids)


def normalize_workspace_model(model: str) -> str:
    """Collapse every spelling of the Auto router onto one id."""
    if model == "auto":
        return AUTO_MODEL_CANONICAL
    return model


def is_auto_workspace_model(model: str) -> bool:
    """True when the user picked Auto — routed via OpenAI /v1/chat/completions in Hub."""
    return normalize_workspace_model(model) in _AUTO_ALIASES


def gateway_openai_base_url(raw: str) -> str:
    """Base URL for the OpenAI Python SDK (must end with /v1)."""
    base = raw.strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


@dataclass
class ModelCatalogEntry:
    id: str
    primary: Optional[str] = None
    provider: Optional[str] = None
    fallbacks: list[str] = field(default_factory=list)
    routing_tiers: Optional[dict[str, str]] = None
    # USD per million tokens, converted from the model gateway's per-token `model_info` fields.
    # None means "the model gateway reported no price for this model" — not "it's free".
    input_cost_per_m: Optional[float] = None
    output_cost_per_m: Optional[float] = None
    cache_read_cost_per_m: Optional[float] = None
    cache_write_cost_per_m: Optional[float] = None


@dataclass
class ModelCatalog:
    entries: list[ModelCatalogEntry]
    source: str  # gateway | cache | emergency
    cached_at: str
    default_model: str
    # Unfiltered merge of every model the model gateway reported, including raw provider ids
    # that `entries` hides from the UI picker. Pricing lookups need this: a
    # session's `MODEL` env var or a usage log's recorded model id may be a raw
    # id that `_filter_ui_entries` would have dropped from `entries`.
    all_entries: list[ModelCatalogEntry] = field(default_factory=list)

    @property
    def model_ids(self) -> list[str]:
        return [e.id for e in self.entries]

    def to_api_payload(self) -> dict[str, Any]:
        return {
            "models": [e.id for e in self.entries],
            "models_detail": [
                {
                    "id": e.id,
                    "primary": e.primary,
                    "provider": e.provider,
                    "fallbacks": e.fallbacks,
                    **({"routing_tiers": e.routing_tiers} if e.routing_tiers else {}),
                }
                for e in self.entries
            ],
            "default": self.default_model,
            # The Auto router's id, so clients never hardcode it. It is a
            # gateway alias and therefore deployment-specific.
            "auto_model": AUTO_MODEL_CANONICAL,
            "source": self.source,
            "cached_at": self.cached_at,
            "chat_fallback_model": CHAT_FALLBACK_MODEL,
        }


_lock = threading.Lock()
_cache: Optional[ModelCatalog] = None
_cache_expires_at: float = 0.0


def _gateway_v1_base(raw: str) -> str:
    base = raw.strip().rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _sort_ui_entries(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    """Platform default first, Auto second, then stable alphabetical."""
    def _sort_key(e: ModelCatalogEntry) -> tuple[int, str]:
        if e.id == WORKSPACE_CHAT_DEFAULT_MODEL:
            return (0, e.id)
        if e.id == AUTO_MODEL_CANONICAL:
            return (1, e.id)
        return (2, e.id)

    return sorted(entries, key=_sort_key)


def _ensure_default_model_in_entries(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    """Always expose the configured workspace default in the picker."""
    default = WORKSPACE_CHAT_DEFAULT_MODEL
    if not default:
        return _sort_ui_entries([e for e in entries if e.id])
    if any(e.id == default for e in entries):
        return _sort_ui_entries(entries)
    return _sort_ui_entries([ModelCatalogEntry(id=default), *entries])


_EMBEDDING_HINTS = ("embed", "embedding", "nomic-embed")


def is_embedding_model_id(model_id: str) -> bool:
    lower = (model_id or "").lower()
    return any(hint in lower for hint in _EMBEDDING_HINTS)


def pick_workspace_chat_model(
    catalog: ModelCatalog,
    requested: Optional[str] = None,
) -> str:
    """Resolve a concrete chat model slug from the gateway catalog."""
    req = normalize_workspace_model((requested or "").strip())
    if req and req in catalog.model_ids and not is_embedding_model_id(req):
        return req

    default = normalize_workspace_model(WORKSPACE_CHAT_DEFAULT_MODEL)
    if default and default in catalog.model_ids and not is_embedding_model_id(default):
        return default

    for entry in catalog.entries:
        if entry.id and not is_embedding_model_id(entry.id):
            return entry.id
    for entry in catalog.all_entries:
        if entry.id and not is_embedding_model_id(entry.id):
            return entry.id

    raise ValueError("No chat model available from the model gateway")


def _filter_ui_entries(entries: list[ModelCatalogEntry]) -> list[ModelCatalogEntry]:
    if CATALOG_FILTER_MODE == "hidden":
        visible = [
            e for e in entries
            if not CHAT_FALLBACK_MODEL or e.id == WORKSPACE_CHAT_DEFAULT_MODEL or e.id != CHAT_FALLBACK_MODEL
        ]
    else:
        visible = list(entries)
    # Several ids can reach the Auto router; show only the configured one.
    if any(e.id == AUTO_MODEL_CANONICAL for e in visible):
        visible = [
            e for e in visible
            if e.id == AUTO_MODEL_CANONICAL or e.id not in _AUTO_ALIASES
        ]
    return _ensure_default_model_in_entries(visible)


def _per_million(mi: dict[str, Any], key: str) -> Optional[float]:
    """the model gateway's `model_info` reports cost per single token; None means unpriced,
    not free, so anything that isn't a real number is left as None rather than 0."""
    value = mi.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) * 1_000_000


def _merge_catalog(
    meta_rows: list[dict[str, Any]],
    info_rows: list[dict[str, Any]],
) -> list[ModelCatalogEntry]:
    info_by_name: dict[str, dict[str, Any]] = {}
    for row in info_rows:
        name = row.get("model_name")
        if isinstance(name, str) and name not in info_by_name:
            info_by_name[name] = row

    entries: list[ModelCatalogEntry] = []
    seen: set[str] = set()
    for row in meta_rows:
        model_id = row.get("id")
        if not isinstance(model_id, str) or model_id in seen:
            continue
        seen.add(model_id)
        info = info_by_name.get(model_id, {})
        lp = info.get("gateway_params") or {}
        mi = info.get("model_info") or {}
        crc = lp.get("complexity_router_config") or {}
        tiers = crc.get("tiers") if isinstance(crc.get("tiers"), dict) else None
        fallbacks = (row.get("metadata") or {}).get("fallbacks") or []
        if not isinstance(fallbacks, list):
            fallbacks = []
        entries.append(
            ModelCatalogEntry(
                id=model_id,
                primary=lp.get("model"),
                provider=mi.get("gateway_provider"),
                fallbacks=[str(f) for f in fallbacks],
                routing_tiers={str(k): str(v) for k, v in tiers.items()} if tiers else None,
                input_cost_per_m=_per_million(mi, "input_cost_per_token"),
                output_cost_per_m=_per_million(mi, "output_cost_per_token"),
                cache_read_cost_per_m=_per_million(mi, "cache_read_input_token_cost"),
                cache_write_cost_per_m=_per_million(mi, "cache_creation_input_token_cost"),
            )
        )
    return entries


async def fetch_model_catalog_from_gateway(
    *,
    api_key: Optional[str],
    base_url_raw: str,
    default_model: str,
) -> ModelCatalog:
    from model_gateway import gateway_auth_headers, gateway_url_configured

    if not gateway_url_configured(base_url_raw):
        raise ValueError("MODEL_GATEWAY_URL is not set")

    v1 = _gateway_v1_base(base_url_raw)
    headers = gateway_auth_headers(api_key)
    timeout = httpx.Timeout(30.0, connect=10.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        meta_resp, info_resp = await asyncio.gather(
            client.get(f"{v1}/models", params={"include_metadata": "true"}, headers=headers),
            client.get(f"{v1}/model/info", headers=headers),
        )
        meta_resp.raise_for_status()
        meta_rows = meta_resp.json().get("data") or []
        info_rows: list[dict[str, Any]] = []
        if info_resp.status_code < 400:
            try:
                info_payload = info_resp.json()
                rows = info_payload.get("data")
                if isinstance(rows, list):
                    info_rows = rows
            except Exception:
                info_rows = []

    merged = _merge_catalog(meta_rows, info_rows)
    ui_entries = _filter_ui_entries(merged)
    if not ui_entries:
        logger.warning(
            "model catalogue fetch returned no UI models after filter=%r; using emergency list",
            CATALOG_FILTER_MODE,
        )
        emergency = _emergency_ui_models()
        ui_entries = _ensure_default_model_in_entries([
            ModelCatalogEntry(id=m) for m in emergency
        ]) if emergency else []

    now = datetime.now(timezone.utc).isoformat()
    return ModelCatalog(
        entries=ui_entries,
        source="gateway",
        cached_at=now,
        default_model=default_model,
        all_entries=merged,
    )


def _emergency_catalog(default_model: str) -> ModelCatalog:
    now = datetime.now(timezone.utc).isoformat()
    emergency = _emergency_ui_models()
    entries = _ensure_default_model_in_entries([
        ModelCatalogEntry(id=m) for m in emergency
    ]) if emergency else []
    return ModelCatalog(
        entries=entries,
        source="emergency",
        cached_at=now,
        default_model=default_model,
        all_entries=entries,
    )


def _store_cache(catalog: ModelCatalog) -> ModelCatalog:
    global _cache, _cache_expires_at
    with _lock:
        _cache = catalog
        _cache_expires_at = time.monotonic() + CACHE_TTL_SECONDS
    return catalog


def get_cached_catalog_sync() -> Optional[ModelCatalog]:
    with _lock:
        if _cache is None:
            return None
        if time.monotonic() >= _cache_expires_at:
            return None
        return _cache


def _get_stale_cache_unlocked() -> Optional[ModelCatalog]:
    """Return cache even if TTL expired (caller must hold _lock)."""
    return _cache


async def get_model_catalog(
    *,
    api_key: Optional[str],
    base_url_raw: str,
    default_model: str,
    force_refresh: bool = False,
) -> ModelCatalog:
    if not force_refresh:
        cached = get_cached_catalog_sync()
        if cached is not None:
            return cached

    with _lock:
        # Double-check after acquiring lock.
        if not force_refresh and _cache is not None and time.monotonic() < _cache_expires_at:
            return _cache

    try:
        catalog = await fetch_model_catalog_from_gateway(
            api_key=api_key,
            base_url_raw=base_url_raw,
            default_model=default_model,
        )
        return _store_cache(catalog)
    except Exception:
        logger.exception("Failed to fetch the model gateway model catalog")
        with _lock:
            stale = _get_stale_cache_unlocked()
        if stale is not None:
            return ModelCatalog(
                entries=stale.entries,
                source="cache",
                cached_at=stale.cached_at,
                default_model=default_model,
                all_entries=stale.all_entries,
            )
        return _emergency_catalog(default_model)


def is_chat_model_allowed(model: str, catalog: ModelCatalog) -> bool:
    """User-selectable catalog ids plus internal fallback slug for retries."""
    model = normalize_workspace_model(model)
    if not model:
        return bool(catalog.model_ids)
    if model == CHAT_FALLBACK_MODEL:
        return True
    return model in catalog.model_ids


def is_catalog_ui_model(model: str, catalog: ModelCatalog) -> bool:
    """True when *model* is a user-selectable catalog id (not the internal fallback slug)."""
    normalized = normalize_workspace_model(model)
    return normalized in catalog.model_ids and normalized != CHAT_FALLBACK_MODEL


# Back-compat alias — prefer is_catalog_ui_model.
is_canonical_ui_model = is_catalog_ui_model
