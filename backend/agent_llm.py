"""Agent LLM model resolution and transport dispatch.

Agent model slugs are gateway deployment ids — configure allowlists via env:
  - ``AGENT_LLM_MODELS`` — comma-separated allowlist for agent.llm_model (optional)
  - ``AGENT_OPENAI_TRANSPORT_MODELS`` — slugs routed via OpenAI /v1/chat/completions

NULL / unset llm_model → platform default (``MODEL`` env).
"""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Awaitable, Callable, Optional

from model_catalog import (
    AUTO_MODEL_CANONICAL,
    is_auto_workspace_model,
    normalize_workspace_model,
    platform_default_model,
)

logger = logging.getLogger(__name__)

PLATFORM_DEFAULT_MODEL: str = platform_default_model()


def _env_model_list(key: str) -> frozenset[str]:
    raw = os.environ.get(key, "").strip()
    if not raw:
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def agent_allowed_models() -> Optional[frozenset[str]]:
    """Optional allowlist from ``AGENT_LLM_MODELS``. ``None`` = any slug accepted."""
    allowed = _env_model_list("AGENT_LLM_MODELS")
    return allowed or None


def openai_transport_models() -> frozenset[str]:
    """Model slugs that must use OpenAI chat/completions (includes Auto router)."""
    return {AUTO_MODEL_CANONICAL} | _env_model_list("AGENT_OPENAI_TRANSPORT_MODELS")


def is_auto_agent_model(model: str) -> bool:
    """True when the resolved model is the gateway's Auto router."""
    return is_auto_workspace_model(model)


def uses_openai_agent_transport(model: str) -> bool:
    """True when agent Slack/tool loops must use OpenAI /v1/chat/completions."""
    return normalize_workspace_model(model) in openai_transport_models()


def normalize_agent_llm_model(raw: Optional[str]) -> Optional[str]:
    """Normalize a client-supplied llm_model for storage.

    Returns:
      - None for empty / platform-default
      - normalized slug (e.g. ``auto`` → the Auto router id)

    Raises:
      ValueError when ``AGENT_LLM_MODELS`` is set and the slug is not listed.
    """
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise ValueError("llm_model must be a string or null.")
    value = raw.strip()
    if not value:
        return None
    normalized = normalize_workspace_model(value)
    allowed = agent_allowed_models()
    if allowed is not None and normalized not in allowed:
        raise ValueError(
            "llm_model must be one of: "
            + ", ".join(sorted(allowed))
            + " (or null for platform default)."
        )
    return normalized


def resolve_agent_model(agent: Any) -> str:
    """Return the model slug the agent reply path should call.

    NULL / missing llm_model → PLATFORM_DEFAULT_MODEL (env MODEL).
    """
    stored = getattr(agent, "llm_model", None)
    if stored is None or (isinstance(stored, str) and not stored.strip()):
        return PLATFORM_DEFAULT_MODEL
    try:
        normalized = normalize_agent_llm_model(stored)
    except ValueError:
        logger.warning(
            "agent_llm: agent %s has invalid llm_model=%r — falling back to platform default",
            getattr(agent, "id", "?"),
            stored,
        )
        return PLATFORM_DEFAULT_MODEL
    if normalized is None:
        return PLATFORM_DEFAULT_MODEL
    return normalized


def agent_model_transport(model: str) -> str:
    """Return ``openai`` for OpenAI-transport slugs, ``anthropic`` otherwise."""
    return "openai" if uses_openai_agent_transport(model) else "anthropic"


def is_cache_eligible_model(model: str) -> bool:
    """True for models called via the Anthropic messages transport.

    Only Anthropic-transport calls can carry ``cache_control`` blocks —
    OpenAI-transport slugs (the Auto router, gemini/grok/deepseek) go through
    the model gateway's ``/v1/chat/completions`` translation, which does not support
    Anthropic-native prompt caching.
    """
    return bool(model) and agent_model_transport(model) == "anthropic"


def cached_system(
    base_system: str,
    *,
    model: str,
    volatile_suffix: str | None = None,
) -> list[dict] | str:
    """Build a ``system`` value for ``messages.create()``, caching the stable prefix.

    For cache-eligible models, ``base_system`` is wrapped in a block carrying
    an ephemeral ``cache_control`` breakpoint. Any per-turn dynamic text (e.g.
    a wind-down instruction only appended in the last few tool turns) must be
    passed as ``volatile_suffix`` rather than concatenated into
    ``base_system`` — it is appended as a SEPARATE, uncached block after the
    cached one, so it never invalidates the cached prefix. For models that
    can't carry cache_control, everything collapses back into a single plain
    string.
    """
    if not is_cache_eligible_model(model):
        return base_system + volatile_suffix if volatile_suffix else base_system

    blocks: list[dict] = [
        {"type": "text", "text": base_system, "cache_control": {"type": "ephemeral"}},
    ]
    if volatile_suffix:
        blocks.append({"type": "text", "text": volatile_suffix})
    return blocks


def cached_tools(tools: list[dict], *, model: str) -> list[dict]:
    """Return a NEW list with ``cache_control`` on a copy of the last tool.

    Never mutates ``tools``. A no-op for non-cache-eligible models or an
    empty tool list.
    """
    if not tools or not is_cache_eligible_model(model):
        return tools
    new_tools = list(tools)
    new_tools[-1] = {**new_tools[-1], "cache_control": {"type": "ephemeral"}}
    return new_tools


def with_trailing_cache_breakpoint(messages: list[dict], *, model: str) -> list[dict]:
    """Return a NEW messages list with a fresh cache_control on the last content block.

    For multi-turn tool loops: each turn's request reuses the previous turn's
    cached prefix (system + tools + prior messages), so only the newest
    breakpoint needs to move forward. Never mutates ``messages`` or its
    nested content — stored/replayed history must stay free of
    ``cache_control`` (avoids breakpoint-budget creep across turns, and the
    ``cache_control=None`` stripping issue already handled by
    ``_serialize_assistant_block`` in api_slack_events.py).
    """
    if not messages or not is_cache_eligible_model(model):
        return messages
    new_messages = list(messages)
    last = dict(new_messages[-1])
    content = last.get("content")
    if isinstance(content, str):
        last["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif isinstance(content, list) and content:
        new_content = list(content)
        new_content[-1] = {**new_content[-1], "cache_control": {"type": "ephemeral"}}
        last["content"] = new_content
    else:
        return messages
    new_messages[-1] = last
    return new_messages


def validate_gateway_api_key(raw: Optional[str]) -> bool:
    """Allowlist shape for BYOK gateway keys (write-only; never logged)."""
    if not isinstance(raw, str):
        return False
    key = raw.strip()
    if not (8 <= len(key) <= 512):
        return False
    # Reject whitespace / control chars so we never store multiline paste accidents.
    if any(ch.isspace() for ch in key):
        return False
    return True


def resolve_agent_gateway_api_key(
    agent: Any,
    *,
    platform_key: Optional[str],
    read_secret: Callable[[Optional[str]], Optional[str]],
) -> tuple[Optional[str], str]:
    """Resolve the gateway API key for an agent reply.

    Preference order:
      1. Agent BYOK vault ref (``gateway_api_key_ref``) when readable
      2. Platform ``MODEL_GATEWAY_API_KEY``
      3. None

    Returns ``(key, source)`` where source is ``byok`` | ``platform`` | ``none``.
    Unreadable BYOK falls back to platform (agent keeps working; ops can fix vault).
    """
    ref = getattr(agent, "gateway_api_key_ref", None)
    if ref:
        byok = read_secret(ref)
        if byok:
            return byok, "byok"
        logger.warning(
            "agent_llm: agent %s has gateway_api_key_ref but vault read failed — "
            "falling back to platform key",
            getattr(agent, "id", "?"),
        )
    if platform_key:
        return platform_key, "platform"
    return None, "none"


ExecuteCodeFn = Callable[[str, dict[str, str]], Awaitable[str]]
ExecuteArtifactFn = Callable[[str, dict], Awaitable[str]]
ExecuteActivateSkillFn = Callable[[str], Awaitable[str]]
# search_knowledge_base takes only a query — the data store is resolved
# server-side from the agent row, never from the model (see
# agent_knowledge_base_tool.py for why).
ExecuteSearchKbFn = Callable[[str], Awaitable[str]]


def _current_system_prompt(system_prompt: str, system_prompt_ref: list[str] | None) -> str:
    if system_prompt_ref:
        return system_prompt_ref[0]
    return system_prompt


def _sync_openai_system_message(
    openai_messages: list[dict],
    system_prompt_ref: list[str] | None,
    *,
    system_prompt: str,
) -> None:
    if not openai_messages or openai_messages[0].get("role") != "system":
        return
    openai_messages[0]["content"] = _current_system_prompt(system_prompt, system_prompt_ref)


def _build_openai_tools(
    *,
    run_python_tool_openai: dict,
    call_artifact_tool_openai: dict | None,
    execute_artifact: ExecuteArtifactFn | None,
    offer_call_artifact_ref: list[bool] | None,
    activate_skill_tool_openai: dict | None,
    execute_activate_skill: ExecuteActivateSkillFn | None,
    search_kb_tool_openai: dict | None = None,
    execute_search_kb: ExecuteSearchKbFn | None = None,
) -> list[dict]:
    tools: list[dict] = [run_python_tool_openai]
    offer_artifacts = (
        execute_artifact is not None
        and call_artifact_tool_openai is not None
        and (offer_call_artifact_ref is None or offer_call_artifact_ref[0])
    )
    if offer_artifacts:
        tools.append(call_artifact_tool_openai)
    if execute_activate_skill and activate_skill_tool_openai:
        tools.append(activate_skill_tool_openai)
    if execute_search_kb and search_kb_tool_openai:
        tools.append(search_kb_tool_openai)
    return tools


async def run_agent_openai_tool_loop(
    *,
    model: str,
    system_prompt: str,
    messages: list[dict],
    api_key: str,
    base_url_raw: str,
    max_tokens: int,
    max_tool_turns: int,
    skill_secrets: dict[str, str],
    execute_code: ExecuteCodeFn,
    usage_acc: Any,
    tool_audit: list | None,
    agent_id: str,
    run_python_tool_openai: dict,
    tool_time_budget_secs: float | None = None,
    call_artifact_tool_openai: dict | None = None,
    execute_artifact: ExecuteArtifactFn | None = None,
    system_prompt_ref: list[str] | None = None,
    offer_call_artifact_ref: list[bool] | None = None,
    activate_skill_tool_openai: dict | None = None,
    execute_activate_skill: ExecuteActivateSkillFn | None = None,
    search_kb_tool_openai: dict | None = None,
    execute_search_kb: ExecuteSearchKbFn | None = None,
) -> str:
    """OpenAI chat/completions tool loop for Auto agents.

    Mirrors Workspace Auto transport: the model gateway Auto routing breaks on Anthropic
    multi-turn history, so Auto must use /v1/chat/completions.
    """
    from openai import AsyncOpenAI

    from model_catalog import gateway_openai_base_url

    client = AsyncOpenAI(
        api_key=api_key,
        base_url=gateway_openai_base_url(base_url_raw),
    )

    openai_messages: list[dict] = [{
        "role": "system",
        "content": _current_system_prompt(system_prompt, system_prompt_ref),
    }]
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")
        if role == "user":
            if isinstance(content, str):
                openai_messages.append({"role": "user", "content": content})
            elif isinstance(content, list):
                # Flatten Anthropic-shaped multimodal / tool_result history into text.
                parts: list[str] = []
                for block in content:
                    if isinstance(block, dict):
                        if block.get("type") == "text":
                            parts.append(str(block.get("text") or ""))
                        elif block.get("type") == "tool_result":
                            parts.append(str(block.get("content") or ""))
                        else:
                            parts.append(str(block.get("text") or block.get("content") or ""))
                    else:
                        parts.append(str(block))
                openai_messages.append({"role": "user", "content": "\n".join(p for p in parts if p)})
            else:
                openai_messages.append({"role": "user", "content": str(content)})
        elif role == "assistant":
            if isinstance(content, str):
                openai_messages.append({"role": "assistant", "content": content})
            elif isinstance(content, list):
                texts = [
                    b.get("text", "")
                    for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                ]
                openai_messages.append({
                    "role": "assistant",
                    "content": "\n".join(t for t in texts if t).strip() or None,
                })
            else:
                openai_messages.append({"role": "assistant", "content": str(content)})

    tools: list[dict] | None = _build_openai_tools(
        run_python_tool_openai=run_python_tool_openai,
        call_artifact_tool_openai=call_artifact_tool_openai,
        execute_artifact=execute_artifact,
        offer_call_artifact_ref=offer_call_artifact_ref,
        activate_skill_tool_openai=activate_skill_tool_openai,
        execute_activate_skill=execute_activate_skill,
    )
    if max_tool_turns <= 0:
        tools = None
    tool_errors = 0
    _WIND_DOWN_TURNS_BEFORE = 2
    tool_loop_started = time.monotonic()
    synthesis_reason = f"tool budget exhausted ({max_tool_turns} turns)"

    try:
        for turn in range(max_tool_turns if max_tool_turns > 0 else 1):
            if (
                max_tool_turns > 0
                and turn > 0
                and tool_time_budget_secs is not None
            ):
                elapsed = time.monotonic() - tool_loop_started
                if elapsed >= tool_time_budget_secs:
                    synthesis_reason = (
                        f"tool time budget exhausted "
                        f"({elapsed:.1f}s/{tool_time_budget_secs:.1f}s)"
                    )
                    break

            turns_remaining = max_tool_turns - turn if max_tool_turns > 0 else 0
            current_system = _current_system_prompt(system_prompt, system_prompt_ref)
            call_messages = list(openai_messages)
            if max_tool_turns > 0 and 0 < turns_remaining <= _WIND_DOWN_TURNS_BEFORE:
                call_messages = list(openai_messages)
                # Soft wind-down via an extra system note (OpenAI has no separate system param per turn).
                call_messages[0] = {
                    "role": "system",
                    "content": (
                        current_system
                        + f"\n\n[INSTRUCTION: You have {turns_remaining} tool call(s) remaining. "
                        "Unless a tool call is absolutely essential, write your final reply now "
                        "based on the information already gathered. Do not make unnecessary calls.]"
                    ),
                }
            else:
                _sync_openai_system_message(
                    openai_messages,
                    system_prompt_ref,
                    system_prompt=system_prompt,
                )
                call_messages = list(openai_messages)

            turn_tools = _build_openai_tools(
                run_python_tool_openai=run_python_tool_openai,
                call_artifact_tool_openai=call_artifact_tool_openai,
                execute_artifact=execute_artifact,
                offer_call_artifact_ref=offer_call_artifact_ref,
                activate_skill_tool_openai=activate_skill_tool_openai,
                execute_activate_skill=execute_activate_skill,
                search_kb_tool_openai=search_kb_tool_openai,
                execute_search_kb=execute_search_kb,
            ) if tools is not None else None

            kwargs: dict[str, Any] = {
                "model": model,
                "messages": call_messages,
                "max_tokens": max_tokens if max_tool_turns > 0 else 2048,
            }
            if turn_tools:
                kwargs["tools"] = turn_tools

            response = await client.chat.completions.create(**kwargs)
            if response.usage:
                _usage_from_openai(response.usage, usage_acc)

            choice = response.choices[0] if response.choices else None
            if choice is None:
                synthesis_reason = f"empty response turn={turn + 1}"
                break

            msg = choice.message
            finish = choice.finish_reason
            tool_calls = list(msg.tool_calls or [])

            if tool_calls and turn_tools:
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": msg.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments or "{}",
                            },
                        }
                        for tc in tool_calls
                    ],
                }
                openai_messages.append(assistant_msg)

                for tc in tool_calls:
                    name = tc.function.name
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}

                    if name == "run_python":
                        code = args.get("code", "") if isinstance(args, dict) else ""
                        logger.info(
                            "Agent %s running tool code via OpenAI path (turn %d)",
                            agent_id,
                            turn + 1,
                        )
                        output = await execute_code(code, skill_secrets)
                        ok = not (output or "").startswith("Error")
                        if tool_audit is not None:
                            tool_audit.append({
                                "tool": "run_python",
                                "code_len": len(code),
                                "output_len": len(output or ""),
                                "ok": ok,
                            })
                        if not ok:
                            tool_errors += 1
                        else:
                            tool_errors = 0
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output or "(no output)",
                        })
                    elif name == "call_artifact" and execute_artifact:
                        art_name = (args.get("name") or "").strip() if isinstance(args, dict) else ""
                        art_params = args.get("params") if isinstance(args, dict) else {}
                        output = await execute_artifact(
                            art_name,
                            art_params if isinstance(art_params, dict) else {},
                        )
                        ok = not (output or "").startswith("[error]")
                        if tool_audit is not None:
                            tool_audit.append({
                                "tool": "call_artifact",
                                "artifact": art_name,
                                "output_len": len(output or ""),
                                "ok": ok,
                            })
                        if not ok:
                            tool_errors += 1
                        else:
                            tool_errors = 0
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output or "(no output)",
                        })
                    elif name == "activate_skill" and execute_activate_skill:
                        act_slug = (args.get("slug") or "").strip() if isinstance(args, dict) else ""
                        output = await execute_activate_skill(act_slug)
                        ok = not (output or "").startswith("[error]")
                        if tool_audit is not None:
                            tool_audit.append({
                                "tool": "activate_skill",
                                "slug": act_slug,
                                "output_len": len(output or ""),
                                "ok": ok,
                            })
                        _sync_openai_system_message(
                            openai_messages,
                            system_prompt_ref,
                            system_prompt=system_prompt,
                        )
                        if not ok:
                            tool_errors += 1
                        else:
                            tool_errors = 0
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output or "(no output)",
                        })
                    elif name == "search_knowledge_base" and execute_search_kb:
                        kb_query = (args.get("query") or "").strip() if isinstance(args, dict) else ""
                        output = await execute_search_kb(kb_query)
                        ok = not (output or "").startswith("[error]")
                        if tool_audit is not None:
                            tool_audit.append({
                                "tool": "search_knowledge_base",
                                "query": kb_query[:500],
                                "result_chars": len(output or ""),
                                "ok": ok,
                            })
                        if not ok:
                            tool_errors += 1
                        else:
                            tool_errors = 0
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": output or "(no output)",
                        })
                    else:
                        tool_errors += 1
                        openai_messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": f"Unknown tool: {name}",
                        })

                if tool_errors >= 5:
                    err_text = (
                        "The query could not be executed — the tool returned errors "
                        "on multiple attempts. Please check that the required credentials are configured."
                    )
                    logger.warning(
                        "Agent %s OpenAI tool circuit-breaker tripped (tool_errors=%d)",
                        agent_id,
                        tool_errors,
                    )
                    return err_text
                continue

            text = (msg.content or "").strip()
            if text:
                return text
            if finish and finish != "tool_calls":
                synthesis_reason = (
                    f"empty response finish_reason={finish} turn={turn + 1}"
                )
                break

        # Retry once without tools after an empty response or after the elapsed/
        # turn budget is reached. Keep the existing conversation intact and put
        # the final-only instruction in the trusted system message.
        logger.warning(
            "Agent %s OpenAI path forcing no-tools final synthesis reason=%s",
            agent_id,
            synthesis_reason,
        )
        synthesis_messages = list(openai_messages)
        synthesis_messages[0] = {
            "role": "system",
            "content": (
                _current_system_prompt(system_prompt, system_prompt_ref)
                + "\n\n[FINAL RESPONSE REQUIRED: Do not call tools. Using only "
                "information already available in the conversation, write the "
                "user-facing answer now. Return only the final answer, with no "
                "internal reasoning. Be concise (at most 600 words) and prioritise "
                "the actionable conclusion.]"
            ),
        }
        response = await client.chat.completions.create(
            model=model,
            messages=synthesis_messages,
            max_tokens=max_tokens,
        )
        if response.usage:
            _usage_from_openai(response.usage, usage_acc)
        if response.choices and response.choices[0].message.content:
            return response.choices[0].message.content.strip()
        logger.error(
            "Agent %s OpenAI no-tools synthesis returned no text reason=%s",
            agent_id,
            synthesis_reason,
        )
        return (
            "I completed the investigation but could not format the final response. "
            "Please ask me for a concise summary."
        )
    finally:
        await client.close()


def _usage_from_openai(usage: Any, acc: Any) -> None:
    if usage is None:
        return
    input_tokens = getattr(usage, "prompt_tokens", None) or getattr(usage, "input_tokens", 0)
    output_tokens = getattr(usage, "completion_tokens", None) or getattr(usage, "output_tokens", 0)

    class _Usage:
        pass

    u = _Usage()
    u.input_tokens = int(input_tokens or 0)
    u.output_tokens = int(output_tokens or 0)
    acc.add(u)
