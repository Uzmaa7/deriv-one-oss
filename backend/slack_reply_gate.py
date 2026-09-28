"""Reply-necessity and anti-loop gates for Slack agents.

Slack agents historically replied to *every* message that mentioned them (or any
DM). In a workspace with multiple agents that @-mention each other as a handoff
convention, that turns a courtesy mention ("notify @X once done") into an
infinite ping-pong: A mentions B, B must reply and mentions A, A must reply, and
so on for thousands of near-identical messages.

This module moves the decision from "was I triggered?" to "is a reply actually
needed?", with four independent, cheap-to-expensive gates. All are designed to
*fail open* (allow the reply) on error so a bug here can never silence a real
user; the deterministic depth breaker (Layer 1) is the hard guarantee that a
bot-to-bot exchange always terminates.

Layers:
  1. count_same_pair_ping_pong   — deterministic same-pair ping-pong breaker.
  2. should_respond_to_bot       — cheap LLM "does this need a reply?" classifier.
  3. NO_REPLY_SENTINEL / strip_no_reply — let the model choose silence.
  4. is_near_duplicate_reply     — suppress repeated / no-progress replies.
"""

from __future__ import annotations

import difflib
import logging
import os
import re
from typing import Any
from model_catalog import platform_default_model

logger = logging.getLogger(__name__)

# ── Tunables (env-overridable so ops can adjust without a redeploy) ────────────

# Max consecutive alternations between the *same two* bot senders in a thread
# before we stop responding to *bot* senders. Multi-bot orchestration chains
# (different bots each turn) do not count toward this limit. A human message
# resets the trailing window.
SLACK_MAX_BOT_TURNS: int = max(1, int(os.environ.get("SLACK_MAX_BOT_TURNS", "30")))

# Whether the LLM "should I respond?" gate is enabled for bot-authored mentions.
SLACK_REPLY_GATE_ENABLED: bool = os.environ.get("SLACK_REPLY_GATE_ENABLED", "1") not in ("0", "false", "False", "")

# Model used for the lightweight should-respond classification. Keep this cheap.
SLACK_REPLY_GATE_MODEL: str = os.environ.get(
    "SLACK_REPLY_GATE_MODEL", platform_default_model()
)
SLACK_REPLY_GATE_TIMEOUT_S: float = float(os.environ.get("SLACK_REPLY_GATE_TIMEOUT_S", "12"))

# Similarity ratio (0..1) above which a drafted reply counts as a near-duplicate
# of a recent message already in the thread.
SLACK_DEDUP_SIMILARITY: float = float(os.environ.get("SLACK_DEDUP_SIMILARITY", "0.92"))
# How many of the most recent thread messages to compare against for dedup.
SLACK_DEDUP_LOOKBACK: int = max(1, int(os.environ.get("SLACK_DEDUP_LOOKBACK", "6")))

# Sentinel the model emits to explicitly decline to reply.
NO_REPLY_SENTINEL: str = "__NO_REPLY__"

# Prefix the model emits when a reply must be visible only to the asker (ephemeral).
EPHEMERAL_SENTINEL: str = "__EPHEMERAL__"


# ── Layer 1: same-pair ping-pong circuit breaker ──────────────────────────────

def count_trailing_bot_turns(history: list[dict]) -> int:
    """Count consecutive bot-authored messages at the tail of a thread.

    ``history`` is the ``[{role, content, sender_id?}]`` list from
    ``_fetch_thread_history`` (chronological, oldest first), where
    ``role == "assistant"`` marks a message authored by *any* bot/agent and
    ``role == "user"`` marks a human. The tail count is the number of bot turns
    since the last human message; a human message anywhere resets it to zero
    from that point.
    """
    count = 0
    for msg in reversed(history or []):
        if msg.get("role") == "assistant":
            count += 1
        else:
            break
    return count


def trailing_bot_sender_ids(history: list[dict]) -> list[str]:
    """Return ``sender_id`` values for trailing bot messages (oldest-first)."""
    ids: list[str] = []
    for msg in reversed(history or []):
        if msg.get("role") != "assistant":
            break
        sender_id = str(msg.get("sender_id") or "").strip()
        if sender_id:
            ids.append(sender_id)
    ids.reverse()
    return ids


def count_same_pair_ping_pong(sender_ids: list[str], party_a: str, party_b: str) -> int:
    """Count trailing messages that strictly alternate between two bot senders.

    Returns 0 when the tail involves a third bot (orchestration handoff) or when
    either party id is missing. A self-loop (``party_a == party_b``) counts
    consecutive messages from that sender only.
    """
    party_a = str(party_a or "").strip()
    party_b = str(party_b or "").strip()
    if not sender_ids or not party_a or not party_b:
        return 0
    if party_a == party_b:
        count = 0
        for sender_id in reversed(sender_ids):
            if sender_id == party_a:
                count += 1
            else:
                break
        return count

    allowed = {party_a, party_b}
    count = 0
    prev: str | None = None
    for sender_id in reversed(sender_ids):
        if sender_id not in allowed:
            break
        if prev is not None and sender_id == prev:
            break
        count += 1
        prev = sender_id
    return count


def bot_turn_limit_reached(
    history: list[dict],
    *,
    current_sender_id: str = "",
    agent_bot_user_id: str = "",
    limit: int | None = None,
) -> bool:
    """True when a bot sender should be circuit-broken to end a ping-pong loop.

    Only trips when the inbound bot and this agent's bot have alternated back
    and forth repeatedly at the thread tail. Multi-bot orchestration (different
    bots each turn) does not accumulate toward the limit.

    Falls back to the legacy trailing-bot count when ``sender_id`` is absent from
    history entries (older threads or fetch errors).
    """
    limit = SLACK_MAX_BOT_TURNS if limit is None else limit
    current_sender = str(current_sender_id or "").strip()
    agent_sender = str(agent_bot_user_id or "").strip()

    sender_ids = trailing_bot_sender_ids(history)
    if current_sender:
        sender_ids = [*sender_ids, current_sender]

    if not sender_ids:
        return False

    if not agent_sender or not current_sender:
        # Missing identity — conservative legacy fallback.
        return (count_trailing_bot_turns(history) + (1 if current_sender else 0)) > limit

    if not all(
        str(msg.get("sender_id") or "").strip()
        for msg in (history or [])
        if msg.get("role") == "assistant"
    ):
        return (count_trailing_bot_turns(history) + (1 if current_sender else 0)) > limit

    ping_pong = count_same_pair_ping_pong(sender_ids, current_sender, agent_sender)
    return ping_pong > limit


# ── Layer 3: model-chosen silence ─────────────────────────────────────────────

def strip_no_reply(reply_text: str) -> tuple[str, bool]:
    """Return ``(clean_text, abstained)``.

    ``abstained`` is True when the model chose to stay silent — i.e. the reply is
    exactly the sentinel, or the sentinel with no other meaningful content.
    """
    if reply_text is None:
        return "", False
    stripped = reply_text.strip()
    if NO_REPLY_SENTINEL not in stripped:
        return reply_text, False
    # Remove the sentinel and see whether anything substantive remains.
    remainder = stripped.replace(NO_REPLY_SENTINEL, "").strip()
    if not remainder:
        return "", True
    # Sentinel appeared alongside real content — treat as a normal reply but drop
    # the sentinel token so it never leaks to the channel.
    return remainder, False


def strip_ephemeral_flag(reply_text: str) -> tuple[str, bool]:
    """Return ``(clean_text, ephemeral)``.

    ``ephemeral`` is True when the model prefixed its reply with
    :data:`EPHEMERAL_SENTINEL` so the dispatcher should use ``chat.postEphemeral``.
    """
    if reply_text is None:
        return "", False
    stripped = reply_text.strip()
    if not stripped.startswith(EPHEMERAL_SENTINEL):
        return reply_text, False
    remainder = stripped[len(EPHEMERAL_SENTINEL) :].strip()
    return remainder, True


# ── Layer 4: near-duplicate / no-progress suppression ─────────────────────────

_MENTION_RE = re.compile(r"<@[A-Z0-9]+>")
_EMOJI_RE = re.compile(r":[a-z0-9_+\-]+:")
_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    t = (text or "").lower()
    t = _MENTION_RE.sub("", t)
    t = _EMOJI_RE.sub("", t)
    t = _WS_RE.sub(" ", t)
    return t.strip()


def is_near_duplicate_reply(
    reply_text: str,
    history: list[dict],
    *,
    threshold: float | None = None,
    lookback: int | None = None,
) -> bool:
    """True when *reply_text* is ~identical to a recent bot message in the thread.

    Targets the "same status posted 1000 times" failure mode. Only compares
    against recent *assistant* (bot) messages; human turns are ignored.
    """
    threshold = SLACK_DEDUP_SIMILARITY if threshold is None else threshold
    lookback = SLACK_DEDUP_LOOKBACK if lookback is None else lookback
    candidate = _normalize(reply_text)
    if len(candidate) < 40:
        # Very short replies (e.g. "Done", "Acknowledged") are not worth
        # dup-suppressing — they rarely drive multi-thousand-message loops and
        # blocking them risks silencing legitimate brief answers.
        return False
    recent_bot = [m.get("content", "") for m in (history or []) if m.get("role") == "assistant"]
    for prior in recent_bot[-lookback:]:
        prior_norm = _normalize(prior)
        if not prior_norm:
            continue
        ratio = difflib.SequenceMatcher(None, candidate, prior_norm).ratio()
        if ratio >= threshold:
            logger.info("slack dedup: drafted reply ~%.2f similar to a recent thread message", ratio)
            return True
    return False


# ── Layer 2: "should I respond?" classifier (bot-authored mentions) ───────────

_GATE_TOOL = {
    "name": "decide_reply",
    "description": "Decide whether the agent should post a reply to this Slack message.",
    "input_schema": {
        "type": "object",
        "properties": {
            "respond": {
                "type": "boolean",
                "description": "true only if a substantive reply or a concrete action is genuinely required from THIS agent now.",
            },
            "reason": {"type": "string", "description": "Short justification."},
        },
        "required": ["respond"],
    },
}


def _build_gate_prompt(agent_name: str, sender_name: str, user_text: str, history: list[dict]) -> str:
    hist_lines = []
    for m in (history or [])[-8:]:
        who = "AGENT/BOT" if m.get("role") == "assistant" else "HUMAN"
        content = (m.get("content") or "").strip().replace("\n", " ")
        if len(content) > 400:
            content = content[:400] + "…"
        hist_lines.append(f"[{who}] {content}")
    hist_block = "\n".join(hist_lines) or "(no prior messages)"
    return f"""You are a gatekeeper deciding whether the Slack agent "{agent_name}" should reply.

The incoming message was posted by ANOTHER BOT/AGENT ({sender_name}), not a human. In this workspace, agents @-mention each other as a convention, which can create endless reply loops where nothing new is said. Your job is to stop that.

Return respond=false when the message is any of:
- an acknowledgement, thanks, or confirmation ("Acknowledged", "Understood", "Noted");
- a status echo or restatement of information already in the thread with no new facts;
- an FYI / courtesy mention (e.g. "notify @{agent_name} once done") that requires no action right now;
- a handoff to a human team, or a statement that something must be done manually;
- anything that would make "{agent_name}" repeat a message it (or another agent) already posted.

Return respond=true ONLY when there is a concrete, unanswered question or a specific actionable request directed at "{agent_name}", or genuinely new information "{agent_name}" must act on.

When in doubt about whether a reply adds value in a bot-to-bot exchange, prefer respond=false.

Recent thread (oldest first):
{hist_block}

New message from {sender_name}:
{user_text.strip()[:1500]}

Call decide_reply with your decision."""


def should_respond_to_bot(
    agent_name: str,
    sender_name: str,
    user_text: str,
    history: list[dict],
) -> tuple[bool, str]:
    """Cheap LLM gate for BOT-authored mentions. Returns ``(respond, reason)``.

    Fails OPEN (returns True) on any error or missing config: the deterministic
    depth breaker guarantees termination, so this gate is allowed to be lenient
    rather than risk silencing a legitimate request.
    """
    if not SLACK_REPLY_GATE_ENABLED:
        return True, "gate disabled"
    try:
        from llm_json import MODEL_GATEWAY_API_KEY, MODEL_GATEWAY_URL, extract_json
    except Exception:
        return True, "gate deps unavailable"
    if not MODEL_GATEWAY_API_KEY:
        return True, "llm not configured"

    try:
        import anthropic

        client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)
        response = client.messages.create(
            model=SLACK_REPLY_GATE_MODEL,
            max_tokens=256,
            messages=[{"role": "user", "content": _build_gate_prompt(agent_name, sender_name, user_text, history)}],
            tools=[_GATE_TOOL],
            tool_choice={"type": "tool", "name": "decide_reply"},
            timeout=SLACK_REPLY_GATE_TIMEOUT_S,
        )
    except Exception:
        logger.warning("should_respond_to_bot: LLM gate failed agent=%s — failing open", agent_name, exc_info=True)
        return True, "gate error (fail-open)"

    raw: dict[str, Any] | None = None
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "tool_use" and getattr(block, "name", "") == "decide_reply":
            raw = block.input if isinstance(block.input, dict) else None
            break
        if getattr(block, "type", "") == "text":
            parsed = extract_json(getattr(block, "text", "") or "")
            if isinstance(parsed, dict):
                raw = parsed

    if not isinstance(raw, dict) or "respond" not in raw:
        return True, "malformed gate output (fail-open)"
    respond = bool(raw.get("respond"))
    reason = str(raw.get("reason") or "")[:200]
    return respond, reason
