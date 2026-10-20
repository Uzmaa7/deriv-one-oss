"""
Per-model LLM token pricing.

Live rates come from the model gateway's `/model/info` response, already
fetched and cached by `model_catalog.py` (used for the Workspace chat model
picker). `pricing_for_model()` reads whatever's currently cached there — no
network call — and only falls back to the static table below for a model
the model gateway hasn't reported a price for (e.g. unreachable proxy, cold cache, or a
genuinely unpriced/self-hosted model).

Used by the admin usage endpoint (backend/api_usage.py) to compute historical
spend.
"""
import os
from dataclasses import dataclass
from typing import Optional

import model_catalog
from model_catalog import platform_default_model


@dataclass
class Pricing:
    input_per_m:       float
    output_per_m:      float
    cache_read_per_m:  float = 0.0
    cache_write_per_m: float = 0.0  # cache_creation_tokens


# USD per million tokens. Last-resort values, used only when the model gateway has never
# reported a price for the model (proxy unreachable, cold cache, or the model
# genuinely isn't in the model gateway's cost map).
_STATIC_FALLBACK_PRICING: dict[str, Pricing] = {
    "claude-sonnet-4-6":       Pricing(3.00,   15.00,  0.30,  3.75),
    "gemini-3.5-flash":        Pricing(1.50,    9.00,  0.150, 0.0),
    "gemini-3.1-pro-preview":  Pricing(2.00,   12.00,  0.200, 0.0),
    "deepseek-v4-pro":         Pricing(0.435,   0.870, 0.0,   0.0),
    "gpt-5.5":                 Pricing(5.00,   30.00,  0.500, 0.0),
    "gpt-5.4-mini":            Pricing(0.750,   4.50,  0.075, 0.0),
    "kimi-k2.6":               Pricing(0.730,   3.49,  0.0,   0.0),
    "qwen3.6-27b-self":        Pricing(0.0,     0.0,   0.0,   0.0),  # self-hosted, free
    "gemma-4-26b-a4b-it-self": Pricing(0.0,     0.0,   0.0,   0.0),  # self-hosted, free
}

# Fallback for any model not explicitly listed anywhere (use Claude Sonnet pricing)
DEFAULT_PRICING = Pricing(3.00, 15.00, 0.30, 3.75)


def _dynamic_pricing_for_model(model: str) -> Optional[Pricing]:
    """Look up `model`'s rates in the already-cached model catalogue, if any.

    Never makes a network call — `model_catalog.get_cached_catalog_sync()`
    only returns what a prior `get_model_catalog()` await already fetched.
    """
    if not model:
        return None
    catalog = model_catalog.get_cached_catalog_sync()
    if catalog is None:
        return None
    entries = catalog.all_entries or catalog.entries
    for entry in entries:
        if entry.id != model:
            continue
        if entry.input_cost_per_m is None or entry.output_cost_per_m is None:
            return None
        return Pricing(
            input_per_m=entry.input_cost_per_m,
            output_per_m=entry.output_cost_per_m,
            cache_read_per_m=entry.cache_read_cost_per_m or 0.0,
            cache_write_per_m=entry.cache_write_cost_per_m or 0.0,
        )
    return None


def pricing_for_model(model: str) -> Pricing:
    dynamic = _dynamic_pricing_for_model(model)
    if dynamic is not None:
        return dynamic
    return _STATIC_FALLBACK_PRICING.get(model or "", DEFAULT_PRICING)


def cost_for_model(model: str, inp: int, out: int, cr: int, cc: int) -> float:
    p = pricing_for_model(model)
    return (
        inp / 1_000_000 * p.input_per_m
        + out / 1_000_000 * p.output_per_m
        + cr  / 1_000_000 * p.cache_read_per_m
        + cc  / 1_000_000 * p.cache_write_per_m
    )


async def refresh_pricing_cache(*, force_refresh: bool = False) -> None:
    """Warm the model catalogue cache so `pricing_for_model()` reflects live
    rates. Best-effort: `get_model_catalog()` already swallows fetch failures
    (falls back to a stale cache or an emergency list), so this never raises.
    """
    api_key = os.environ.get("MODEL_GATEWAY_API_KEY", "") or ""
    base_url_raw = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000/v1")
    default_model = platform_default_model()
    await model_catalog.get_model_catalog(
        api_key=api_key,
        base_url_raw=base_url_raw,
        default_model=default_model,
        force_refresh=force_refresh,
    )
