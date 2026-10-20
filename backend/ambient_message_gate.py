"""Router gate for agents configured to process ALL messages in a channel.

An agent can be toggled, per channel, into "ambient" mode via
``AgentChannelConfig.join_behavior.proactivity == "ambient"`` (see
``agent_channel_configs.py``). In that mode every message in the channel
reaches the dispatcher, not just @-mentions — but it must never go straight
to the agent's full (expensive) model. This module is the cheap/fast gate in
between: it decides whether a given ambient message actually warrants the
agent's attention.

Unlike ``slack_reply_gate.should_respond_to_bot`` — which fails *open*
because silencing a legitimate bot-to-bot request is worse than an extra
reply — this gate fails **closed**. Most traffic in an all-messages channel
is not directed at the agent, so on any error, timeout, or malformed output
the safe default is to do nothing rather than risk replying to (or burning
tokens on) every message during an outage.
"""

from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# ── Tunables (env-overridable) ──────────────────────────────────────────────

AMBIENT_GATE_ENABLED: bool = os.environ.get("AMBIENT_GATE_ENABLED", "1") not in ("0", "false", "False", "")

from model_catalog import platform_default_model

# Keep this cheap and fast — it runs on every message in an ambient channel.
AMBIENT_GATE_MODEL: str = os.environ.get("AMBIENT_GATE_MODEL", "").strip() or platform_default_model()
AMBIENT_GATE_TIMEOUT_S: float = float(os.environ.get("AMBIENT_GATE_TIMEOUT_S", "8"))

_GATE_TOOL = {
    "name": "decide_handle",
    "description": "Decide whether the agent should process this Slack channel message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "handle": {
                "type": "boolean",
                "description": "true only if this specific message is a clear, specific request or question this agent should act on right now.",
            },
            "reason": {"type": "string", "description": "Short justification."},
        },
        "required": ["handle"],
    },
}


def _build_gate_prompt(agent_name: str, channel_name: str, user_text: str, history: list[dict]) -> str:
    hist_lines = []
    for m in (history or [])[-8:]:
        who = "AGENT" if m.get("role") == "assistant" else "USER"
        content = (m.get("content") or "").strip().replace("\n", " ")
        if len(content) > 400:
            content = content[:400] + "…"
        hist_lines.append(f"[{who}] {content}")
    hist_block = "\n".join(hist_lines) or "(no prior messages)"
    return f"""You are a gatekeeper deciding whether the Slack agent "{agent_name}" should process a message.

"{agent_name}" has been configured to watch every message in #{channel_name or 'this channel'}, not just messages that @-mention it. Most of that traffic is ordinary conversation between humans and is NOT meant for the agent. Your job is to filter out everything that isn't.

Return handle=false when the message is any of:
- casual conversation, banter, or a reply between humans that doesn't involve the agent;
- a status update, FYI, or discussion that doesn't ask for anything;
- a question or request clearly directed at a specific person, not the agent;
- anything you are not confident is meant for "{agent_name}".

Return handle=true ONLY when the message is a clear, specific request or question this agent should act on — even without an explicit @-mention (e.g. "can someone check the deploy status", "what's the latest on X" in a channel this agent owns).

When in doubt, prefer handle=false.

Recent channel history (oldest first):
{hist_block}

New message:
{user_text.strip()[:1500]}

Call decide_handle with your decision."""


def should_handle_ambient_message(
    agent_name: str,
    channel_name: str,
    user_text: str,
    history: list[dict],
) -> tuple[bool, str]:
    """Cheap LLM gate for ambient (non-mention) channel messages.

    Returns ``(handle, reason)``. Fails CLOSED (returns False) on any error,
    timeout, missing config, or malformed output — the safe default when the
    agent was never explicitly invited into the conversation.
    """
    if not AMBIENT_GATE_ENABLED:
        return False, "gate disabled"
    try:
        from llm_json import MODEL_GATEWAY_API_KEY, MODEL_GATEWAY_URL, extract_json
    except Exception:
        return False, "gate deps unavailable (fail-closed)"
    if not MODEL_GATEWAY_API_KEY:
        return False, "llm not configured (fail-closed)"

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)
        response = client.messages.create(
            model=AMBIENT_GATE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": _build_gate_prompt(agent_name, channel_name, user_text, history)}],
            tools=[_GATE_TOOL],
            tool_choice={"type": "tool", "name": "decide_handle"},
            timeout=AMBIENT_GATE_TIMEOUT_S,
        )
    except Exception:
        logger.warning("should_handle_ambient_message: LLM gate failed agent=%s — failing closed", agent_name, exc_info=True)
        return False, "gate error (fail-closed)"

    raw: dict[str, Any] | None = None
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "decide_handle":
            raw = block.input if isinstance(block.input, dict) else None
            break
        if getattr(block, "type", "") == "text":
            parsed = extract_json(getattr(block, "text", "") or "")
            if isinstance(parsed, dict):
                raw = parsed

    if not isinstance(raw, dict) or "handle" not in raw:
        return False, "malformed gate output (fail-closed)"
    handle = bool(raw.get("handle"))
    reason = str(raw.get("reason") or "")[:200]
    return handle, reason
