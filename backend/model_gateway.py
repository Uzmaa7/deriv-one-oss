"""Shared helpers for OpenAI-compatible model gateways (cloud, LM Studio, Ollama)."""

from __future__ import annotations

import re
from typing import Optional
from urllib.parse import urlparse

import httpx

# SDKs require a non-empty api_key; local gateways ignore it.
LOCAL_GATEWAY_KEY_PLACEHOLDER = "local-no-key"

_LOCAL_HOSTS = frozenset(
    {
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
        "::1",
        "host.docker.internal",
    }
)


def gateway_url_configured(url: Optional[str]) -> bool:
    return bool((url or "").strip())


def is_likely_local_gateway(url: str) -> bool:
    """True for loopback / docker-host gateways (LM Studio, Ollama, etc.)."""
    try:
        host = (urlparse(url.strip()).hostname or "").lower()
    except Exception:
        return False
    if host in _LOCAL_HOSTS:
        return True
    if host.endswith(".local"):
        return True
    return bool(re.match(r"^192\.168\.\d{1,3}\.\d{1,3}$", host))


def effective_gateway_api_key(raw: Optional[str]) -> str:
    """Return a key suitable for Anthropic/OpenAI SDK clients."""
    key = (raw or "").strip()
    if key:
        return key
    return LOCAL_GATEWAY_KEY_PLACEHOLDER


def resolved_gateway_api_key(
    raw: Optional[str],
    gateway_url: Optional[str],
) -> Optional[str]:
    """None when no gateway URL; otherwise a key SDKs can use (placeholder if unset)."""
    if not gateway_url_configured(gateway_url):
        return None
    return effective_gateway_api_key(raw)


def gateway_auth_headers(api_key: Optional[str]) -> dict[str, str]:
    key = (api_key or "").strip()
    if not key or key == LOCAL_GATEWAY_KEY_PLACEHOLDER:
        return {}
    return {"Authorization": f"Bearer {key}"}


def resolve_models_list_url(base: str) -> str:
    """OpenAI-compatible GET /v1/models URL for a gateway base."""
    root = base.strip().rstrip("/")
    if root.endswith("/models"):
        return root
    if root.endswith("/v1"):
        return f"{root}/models"
    return f"{root}/v1/models"


def probe_gateway(url: str, api_key: Optional[str] = "") -> tuple[bool, str, int]:
    """Return (ok, message, model_count). Works without an API key for local gateways."""
    base = url.strip().rstrip("/")
    if not base:
        return False, "Gateway URL is required.", 0

    models_url = resolve_models_list_url(base)
    headers = gateway_auth_headers(api_key)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(models_url, headers=headers)
        if resp.status_code >= 400:
            return False, f"Gateway returned HTTP {resp.status_code}", 0
        data = resp.json()
        count = len(
            [
                m.get("id")
                for m in (data.get("data") or [])
                if isinstance(m, dict) and m.get("id")
            ]
        )
        if count == 0:
            return False, "Gateway responded but returned no models.", 0
        auth_note = " (no API key)" if not (api_key or "").strip() else ""
        return True, f"Connected — {count} model(s) at {models_url}{auth_note}", count
    except Exception as exc:
        return False, str(exc), 0


_LOCAL_BOILERPLATE_MARKERS = (
    "\n\n---\n\n## Creating Skills, Automations, and Labs\n",
    "\n\nThe user clicked \"Create new ",
)


def strip_local_workspace_boilerplate(system: str) -> str:
    """Drop optional workspace sections that consume context on small local models."""
    trimmed = system
    for marker in _LOCAL_BOILERPLATE_MARKERS:
        idx = trimmed.find(marker)
        if idx != -1:
            trimmed = trimmed[:idx]
    return trimmed


def _message_content_chars(content: object) -> int:
    if isinstance(content, str):
        return len(content)
    if isinstance(content, list):
        return sum(len(str(block)) for block in content)
    return len(str(content or ""))


def compact_for_local_gateway(
    system: str,
    messages: list[dict],
    *,
    max_system_chars: int = 6_000,
    max_turns: int = 4,
) -> tuple[str, list[dict]]:
    """Shrink prompt/history so small-context local models (LM Studio, Ollama) fit."""
    trimmed_system = strip_local_workspace_boilerplate(system)
    if len(trimmed_system) > max_system_chars:
        trimmed_system = (
            trimmed_system[:max_system_chars]
            + "\n\n[Note: system prompt truncated to fit local model context.]"
        )
    trimmed_messages = messages[-max_turns:] if len(messages) > max_turns else list(messages)
    return trimmed_system, trimmed_messages


def compact_openai_messages_for_local(
    messages: list[dict],
    *,
    max_messages: int = 6,
    max_content_chars: int = 1_500,
) -> list[dict]:
    """Trim an OpenAI-format turn list between tool rounds."""
    system_msgs = [m for m in messages if m.get("role") == "system"]
    other = [m for m in messages if m.get("role") != "system"]
    trimmed_other = other[-max_messages:] if len(other) > max_messages else other
    out: list[dict] = []
    if system_msgs:
        out.append(system_msgs[0])
    for msg in trimmed_other:
        copy = dict(msg)
        content = copy.get("content")
        if isinstance(content, str) and len(content) > max_content_chars:
            copy["content"] = (
                content[:max_content_chars] + "\n\n[truncated for local model context]"
            )
        out.append(copy)
    return out


def local_gateway_max_output_tokens(
    system: str,
    messages: list[dict],
    *,
    context_limit: Optional[int] = None,
    tools_json_chars: int = 0,
) -> int:
    """Conservative max_tokens for gateways with small context windows."""
    import os

    limit = context_limit or int(os.environ.get("LOCAL_GATEWAY_CONTEXT_LIMIT", "4096"))
    configured_cap = int(os.environ.get("LOCAL_GATEWAY_MAX_OUTPUT_TOKENS", "768"))
    input_chars = len(system) + tools_json_chars
    for msg in messages:
        input_chars += _message_content_chars(msg.get("content"))
        tool_calls = msg.get("tool_calls")
        if isinstance(tool_calls, list):
            input_chars += sum(len(str(tc)) for tc in tool_calls)
    input_tokens_est = input_chars // 4
    headroom = limit - input_tokens_est - 192
    return max(128, min(configured_cap, headroom))


def local_gateway_context_limit() -> int:
    import os

    return int(os.environ.get("LOCAL_GATEWAY_CONTEXT_LIMIT", "4096"))


def gateway_use_openai_transport(url: Optional[str]) -> bool:
    """True when the gateway is OpenAI-compatible only (LM Studio, Ollama, etc.)."""
    return is_likely_local_gateway(url or "")


def list_gateway_model_ids(url: str, api_key: Optional[str] = None) -> list[str]:
    """Return model ids advertised by an OpenAI-compatible gateway."""
    models_url = resolve_models_list_url(url)
    headers = gateway_auth_headers(api_key)
    try:
        with httpx.Client(timeout=15.0) as client:
            resp = client.get(models_url, headers=headers)
        if resp.status_code >= 400:
            return []
        data = resp.json()
        return [
            str(m.get("id"))
            for m in (data.get("data") or [])
            if isinstance(m, dict) and m.get("id")
        ]
    except Exception:
        return []


def default_gateway_chat_model(explicit: Optional[str] = None) -> str:
    """Resolve a chat model slug from env or the live gateway catalog."""
    import os

    from model_catalog import is_embedding_model_id

    model = (
        (explicit or "").strip()
        or os.environ.get("MODEL", "").strip()
        or os.environ.get("WORKSPACE_CHAT_DEFAULT_MODEL", "").strip()
    )
    if model:
        return model

    url = os.environ.get("MODEL_GATEWAY_URL", "").strip()
    if not url:
        return ""

    for mid in list_gateway_model_ids(url, os.environ.get("MODEL_GATEWAY_API_KEY")):
        if mid and not is_embedding_model_id(mid):
            return mid
    return ""


def _system_prompt_to_string(system: list[dict] | str) -> str:
    if isinstance(system, str):
        return system
    parts = [
        str(block.get("text", ""))
        for block in system
        if isinstance(block, dict) and block.get("text")
    ]
    return "\n\n".join(parts)


def extract_anthropic_text(response: object) -> str:
    content = getattr(response, "content", None) or []
    parts: list[str] = []
    for block in content:
        if getattr(block, "type", None) == "text":
            text = getattr(block, "text", "") or ""
            if text:
                parts.append(text)
    return "\n".join(parts).strip()


def gateway_text_completion(
    *,
    api_key: Optional[str],
    base_url_raw: str,
    model: str,
    system: list[dict] | str,
    user_message: str,
    max_tokens: int = 768,
) -> tuple[str, object]:
    """Single-turn completion using Anthropic or OpenAI transport as appropriate."""
    from model_catalog import gateway_openai_base_url

    key = effective_gateway_api_key(api_key)
    resolved_model = (model or default_gateway_chat_model()).strip()
    if not resolved_model:
        raise ValueError("No model configured for gateway completion")

    system_text = _system_prompt_to_string(system)

    if gateway_use_openai_transport(base_url_raw):
        from openai import OpenAI

        client = OpenAI(
            api_key=key,
            base_url=gateway_openai_base_url(base_url_raw),
        )
        response = client.chat.completions.create(
            model=resolved_model,
            messages=[
                {"role": "system", "content": system_text},
                {"role": "user", "content": user_message},
            ],
            max_tokens=max_tokens,
        )
        text = (response.choices[0].message.content or "").strip()
        return text, response.usage

    import anthropic

    anthropic_base = base_url_raw.strip().rstrip("/")
    if anthropic_base.lower().endswith("/v1"):
        anthropic_base = anthropic_base[:-3].rstrip("/")
    client = anthropic.Anthropic(api_key=key, base_url=anthropic_base)
    response = client.messages.create(
        model=resolved_model,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user_message}],
    )
    return extract_anthropic_text(response), response.usage


def apply_gateway_env_defaults() -> None:
    """After loading env files: allow local gateways without a stored API key."""
    import os

    url = os.environ.get("MODEL_GATEWAY_URL", "").strip()
    if not url:
        return
    raw = os.environ.get("MODEL_GATEWAY_API_KEY", "").strip()
    if raw:
        return
    if is_likely_local_gateway(url):
        os.environ["MODEL_GATEWAY_API_KEY"] = LOCAL_GATEWAY_KEY_PLACEHOLDER
