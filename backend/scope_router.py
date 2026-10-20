"""AI inference for the set of Slack bot scopes an agent needs.

Two entry points, both mirroring ``skill_router``'s shape (Anthropic SDK via the
model gateway, JSON output, threshold-gated, never raises):

* ``infer_scopes_for_agent``  — propose the *minimum* conditional scopes from the
  agent's name / description / soul / skills. Returns reasons + confidence.
* ``adjudicate_scope_request`` — when the user requests an extra scope the AI did
  not propose, decide allow/deny per scope (default deny). The catalog in
  ``slack_scope_catalog`` is the hard allowlist either way.

Both functions are advisory; the server re-validates and force-includes the
locked base set before anything is written to a manifest.
"""

from __future__ import annotations

import logging
import os
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:  # avoid a hard import-time dep on the anthropic SDK
    import anthropic

from slack_scope_catalog import (
    BASE_SCOPES,
    conditional_entry,
    is_known_scope,
    catalog_for_prompt,
)

logger = logging.getLogger(__name__)

# Reuse the same gateway/model configuration convention as skill_router. The
# model default follows the app-wide MODEL var (the one actually deployed on the
# model gateway) so a deployment that only sets MODEL gets a working router.
from model_catalog import platform_default_model

SCOPE_ROUTER_MODEL: str = (
    os.environ.get("SCOPE_ROUTER_MODEL", "").strip()
    or os.environ.get("SKILL_ROUTER_MODEL", "").strip()
    or os.environ.get("APP_CLASSIFIER_MODEL", "").strip()
    or platform_default_model()
)
SCOPE_ROUTER_THRESHOLD: float = float(os.environ.get("SCOPE_ROUTER_THRESHOLD", "0.6"))
SCOPE_ROUTER_TIMEOUT_S: float = float(os.environ.get("SCOPE_ROUTER_TIMEOUT_S", "20"))
SCOPE_ROUTER_MAX_SUGGESTIONS: int = int(os.environ.get("SCOPE_ROUTER_MAX", "12"))

# DM keyword detection — users do not @mention bots in 1-on-1 DMs.
_DM_KEYWORD_RE = re.compile(
    r"(?i)\b(?:"
    r"dm|dms|direct\s+message|direct\s+messages|"
    r"1-on-1|one-on-one|private\s+message|"
    r"via\s+dm(?:\s+only)?|chat\s+via\s+dm|dm\s+only"
    r")\b"
)

REQUIRED_DM_SCOPES: tuple[str, ...] = ("im:read", "im:history", "im:write")

# RBAC / directory email lookup detection for users:read.email.
_RBAC_EMAIL_RE = re.compile(
    r"(?i)\b(?:"
    r"rbac|role-based\s+access|access\s+control|authorization|authorised|authorized|"
    r"employee\s+lookup|user\s+verification|identity\s+verification|"
    r"enterprise\s+permissions?|okta|active\s+directory|\biam\b|directory\s+lookup|"
    r"role-restricted|user-restricted|user-specific|corporate\s+identity|"
    r"sso|user\s+emails?|email\s+match"
    r")\b"
)


def requires_dm_scopes(usecase_description: str, trigger_mode: str) -> bool:
    """Return True when the agent must operate in Slack DMs."""
    mode = (trigger_mode or "mention").strip().lower()
    if mode in ("dm", "both"):
        return True
    return bool(_DM_KEYWORD_RE.search((usecase_description or "").strip()))


def requires_users_read_email(usecase_description: str, description: str) -> bool:
    """Return True when RBAC, directory, or identity email lookup is required."""
    combined = f"{(usecase_description or '').strip()}\n{(description or '').strip()}"
    return bool(_RBAC_EMAIL_RE.search(combined))


# ── Prompts ───────────────────────────────────────────────────────────────────

INFER_SYSTEM = """
You assign the MINIMUM set of Slack bot scopes an AI agent needs.

You are given the agent's stated usecase (primary signal), name, description,
personality (soul), trigger mode, and the skills attached to it, plus a catalog
of *conditional* scopes (the base runtime scopes are granted automatically — do
not include them).

Principles:
- PRECISION OVER RECALL. Default to NOT granting a scope unless required.
- The usecase_description is the authoritative statement of intent.
- DM SCOPES: If the usecase_description mentions DMs/direct messages/1-on-1 chats
  (e.g. "DM", "direct message", "via DM only", "private message"), OR trigger_mode
  is "dm" or "both", you MUST include im:read, im:history, and im:write. In Slack
  1-on-1 DMs users do not @mention the bot — app_mentions:read alone is insufficient.
- EMAIL / RBAC SCOPE: Propose users:read.email if the usecase or description involves
  RBAC, user authorization, identity verification, employee directory lookups,
  access control, Okta/Active Directory/IAM matching, or user-restricted actions
  requiring corporate identity lookup.
- CHANNEL HISTORY: Only propose channels:history or groups:history when the bot must
  read unmentioned prior channel history or summarize existing threads.
  app_mentions:read handles tagged replies in channels automatically.
- PRIVATE CHANNELS: Only propose groups:read when the usecase explicitly mentions
  private channels or private groups.
- FILES: Propose files:write ONLY if the usecase_description explicitly calls for
  uploading or generating files/PDFs/reports. Attached skills are secondary metadata
  — do NOT propose files:write based on skill capabilities alone.
- Never propose chat:write.customize under any circumstance.
- Never invent scopes. Only return scopes that appear in the catalog.
- If nothing beyond the base set is needed, return an empty array.

Respond ONLY with a JSON array:
[{"scope": "<scope>", "confidence": 0.0-1.0, "reason": "<short, why>"}]
""".strip()


ADJUDICATE_SYSTEM = """
You decide whether an AI agent should be granted an additional Slack bot scope
that a user has requested beyond the AI's initial proposal.

You are given the agent's stated usecase (primary signal), purpose (name,
description, soul, skills, trigger mode), the scope being requested, and that
scope's capability description.

Principles:
- Default to DENY when uncertain. Granting fewer scopes is safer.
- The usecase_description is the authoritative statement of intent.
- DM SCOPES: ALLOW im:read, im:history, and im:write when the usecase_description
  mentions DMs/direct messages/1-on-1 chats, OR trigger_mode is "dm" or "both".
  Users do not @mention bots in 1-on-1 DMs — deny only if the usecase is
  channel-only with no DM intent.
- EMAIL / RBAC SCOPE: ALLOW users:read.email when the usecase or description
  involves RBAC, authorization, identity verification, employee/directory lookup,
  access control, Okta/Active Directory/IAM, or user-restricted actions needing
  corporate email resolution.
- CHANNEL HISTORY: ALLOW channels:history/groups:history only when the usecase
  requires reading unmentioned prior history or summarizing threads — not merely
  to reply to @mentions (app_mentions:read covers tagged messages).
- PRIVATE CHANNELS: Deny groups:read unless the usecase mentions private channels/groups.
- FILES: Deny files:write unless the usecase_description explicitly requires
  uploading or generating files/PDFs/reports.
- Never allow chat:write.customize under any circumstance.
- Never allow a scope that does not appear in the request. You are judging one
  scope at a time.

Respond ONLY with JSON:
{"allowed": true|false, "confidence": 0.0-1.0, "reason": "<short, why>"}
""".strip()


# ── Output types ──────────────────────────────────────────────────────────────

class ScopeProposal:
    __slots__ = ("scope", "reason", "confidence", "locked")

    def __init__(self, scope: str, reason: str, confidence: float, locked: bool = False) -> None:
        self.scope = scope
        self.reason = reason
        self.confidence = confidence
        self.locked = locked

    def as_dict(self) -> dict:
        return {
            "scope": self.scope,
            "reason": self.reason,
            "confidence": round(self.confidence, 3),
            "locked": self.locked,
            # Surface the human description so the review UI can show it.
            "description": (conditional_entry(self.scope).description
                            if conditional_entry(self.scope) else ""),
        }


def _apply_required_conditional_scopes(
    conditional: list[ScopeProposal],
    usecase: str,
    description: str,
    trigger_mode: str,
) -> list[ScopeProposal]:
    """Inject MUST-have conditional scopes the LLM may miss (DM, RBAC email)."""
    seen = {p.scope for p in conditional}
    out = list(conditional)

    if requires_dm_scopes(usecase, trigger_mode):
        for scope in REQUIRED_DM_SCOPES:
            if scope in seen or not is_known_scope(scope):
                continue
            out.append(ScopeProposal(
                scope,
                "Required for DM interactions — users do not @mention the bot in 1-on-1 DMs.",
                1.0,
                locked=False,
            ))
            seen.add(scope)

    if requires_users_read_email(usecase, description):
        scope = "users:read.email"
        if scope not in seen and is_known_scope(scope):
            out.append(ScopeProposal(
                scope,
                "Required to resolve Slack users to corporate email for RBAC/directory checks.",
                1.0,
                locked=False,
            ))

    return out[:SCOPE_ROUTER_MAX_SUGGESTIONS]


# ── LLM helpers (mirror skill_router._call_router_*) ──────────────────────────

def _extract_text(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        btype = getattr(block, "type", None)
        if btype == "text":
            parts.append(block.text)
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


def _extract_json_object(text: str) -> Optional[dict]:
    import json
    import re
    text = (text or "").strip()
    if not text:
        return None
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


def _extract_json_array(text: str) -> list[dict]:
    import json
    import re
    text = (text or "").strip()
    if not text:
        return []
    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [i for i in parsed if isinstance(i, dict)]
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, list):
            return [i for i in parsed if isinstance(i, dict)]
    except json.JSONDecodeError:
        return []


def _describe_llm_error(exc: Exception) -> str:
    """Return a short, log-safe description of an Anthropic SDK / HTTP error.

    Never includes the API key. Captures the gateway's HTTP status and body so
    404/500s from the model gateway are diagnosable instead of appearing as a
    bare 'LLM call failed'.
    """
    status = getattr(exc, "status_code", None)
    body = ""
    # anthropic.APIStatusError carries the response body on `.body` / `.message`.
    for attr in ("body", "message"):
        val = getattr(exc, attr, None)
        if val:
            body = str(val)[:300]
            break
    if status is not None:
        return f"LLM call failed: HTTP {status} {type(exc).__name__} body={body or '(empty)'}"
    return f"LLM call failed: {type(exc).__name__}: {str(exc)[:300]}"


async def _call_array(client: anthropic.AsyncAnthropic, system: str, user_text: str) -> tuple[list[dict], str]:
    # 2048-token budget — same rationale as skill_router._call_router_array:
    # Gemini via model gateway has been observed to return stop=max_tokens with empty
    # content at 512 for array responses.
    # Returns (items, error_note). error_note is "" on success or a clean empty
    # result; it is non-empty ONLY when the LLM call itself failed (so callers
    # can distinguish "AI said no extras" from "gateway broken").
    last_err = ""
    for _attempt in range(2):
        try:
            response = await client.messages.create(
                model=SCOPE_ROUTER_MODEL,
                max_tokens=2048,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as exc:
            last_err = _describe_llm_error(exc)
            logger.warning("scope_router: %s", last_err)
            continue
        items = _extract_json_array(_extract_text(response))
        if items:
            return items, ""
    if last_err:
        logger.warning("scope_router: giving up after retries: %s", last_err)
        return [], last_err
    return [], ""  # LLM returned a valid (but empty) response


async def _call_object(client: anthropic.AsyncAnthropic, system: str, user_text: str) -> tuple[Optional[dict], str]:
    last_err = ""
    for _attempt in range(2):
        try:
            response = await client.messages.create(
                model=SCOPE_ROUTER_MODEL,
                max_tokens=512,
                system=system,
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception as exc:
            last_err = _describe_llm_error(exc)
            logger.warning("scope_router: %s", last_err)
            continue
        payload = _extract_json_object(_extract_text(response))
        if payload is not None:
            return payload, ""
    if last_err:
        logger.warning("scope_router: giving up after retries: %s", last_err)
        return None, last_err
    return None, ""


# ── Public entry points ───────────────────────────────────────────────────────

def _build_agent_context(
    name: str,
    description: str,
    usecase_description: str,
    soul_text: str,
    skill_ids: list[str],
    trigger_mode: str = "mention",
) -> str:
    skills = ", ".join(skill_ids) if skill_ids else "(none)"
    usecase = (usecase_description or "").strip() or "(none)"
    return (
        f"Stated usecase (primary signal for scoping):\n{usecase}\n\n"
        f"Agent name: {(name or '').strip() or '(unnamed)'}\n\n"
        f"Agent description:\n{(description or '').strip() or '(none)'}\n\n"
        f"Agent personality / soul:\n{(soul_text or '').strip() or '(none)'}\n\n"
        f"Trigger mode: {(trigger_mode or 'mention').strip()}\n\n"
        f"Attached skills: {skills}\n\n"
        f"Conditional scope catalog (base scopes are granted automatically — do not include them):\n"
        f"{catalog_for_prompt()}"
    )


def base_proposals() -> list[ScopeProposal]:
    """The locked base scopes, presented as proposals for the review UI."""
    return [
        ScopeProposal(
            s,
            "Required for a basic @mention bot (receive mentions, reply in public channels, resolve user names).",
            1.0,
            locked=True,
        )
        for s in BASE_SCOPES
    ]


async def infer_scopes_for_agent(
    name: str,
    description: str,
    usecase_description: str,
    soul_text: str,
    skill_ids: list[str],
    trigger_mode: str,
    client: anthropic.AsyncAnthropic,
    threshold: float = SCOPE_ROUTER_THRESHOLD,
) -> tuple[list[ScopeProposal], str]:
    """Return (proposals, notes).

    ``proposals`` always includes the locked base set first, then the AI-proposed
    conditional scopes (confidence >= threshold, de-duplicated, capped). Never
    raises — on any failure returns base-only with an explanatory note.
    """
    proposals = base_proposals()

    desc = (description or "").strip()
    usecase = (usecase_description or "").strip()
    soul = (soul_text or "").strip()
    if not usecase and not desc and not soul and not (name or "").strip():
        return proposals, "Nothing to analyze — using the required base scopes only."

    items, err = await _call_array(
        client,
        INFER_SYSTEM,
        _build_agent_context(name, desc, usecase, soul, skill_ids or [], trigger_mode),
    )
    if err:
        # Gateway/LLM failure — distinguish from a clean "nothing needed".
        return proposals, (
            "Scope analysis is unavailable right now (the AI gateway could not be "
            "reached). Only the required base scopes will be requested; you can still "
            "request additional scopes below."
        )

    seen: set[str] = set()
    conditional: list[ScopeProposal] = []
    for item in items or []:
        raw_scope = item.get("scope")
        if not isinstance(raw_scope, str):
            continue
        scope = raw_scope.strip()
        if not scope or scope in seen or not is_known_scope(scope):
            logger.warning("scope_router: model returned unknown/dupe scope %r", raw_scope)
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if confidence < threshold:
            continue
        reason = str(item.get("reason") or "").strip() or "Required by the agent's purpose."
        seen.add(scope)
        conditional.append(ScopeProposal(scope, reason, confidence, locked=False))

    conditional = _apply_required_conditional_scopes(conditional, usecase, desc, trigger_mode)
    note = (
        "AI proposed the minimum scopes required for this agent's stated purpose."
        if conditional else "No additional scopes were deemed necessary."
    )
    return proposals + conditional, note


async def adjudicate_scope_request(
    scope: str,
    name: str,
    description: str,
    usecase_description: str,
    soul_text: str,
    skill_ids: list[str],
    trigger_mode: str,
    client: anthropic.AsyncAnthropic,
    threshold: float = SCOPE_ROUTER_THRESHOLD,
) -> tuple[bool, str, float]:
    """Decide whether ``scope`` may be added to the approved set.

    Returns ``(allowed, reason, confidence)``. Defaults to deny on any error or
    unknown scope. Never raises.
    """
    scope = (scope or "").strip()
    if not scope:
        return False, "No scope requested.", 0.0
    if not is_known_scope(scope):
        return False, f"{scope} is not an allowed scope.", 0.0

    if scope in REQUIRED_DM_SCOPES and requires_dm_scopes(usecase_description, trigger_mode):
        return True, "Required for DM interactions stated in the usecase.", 1.0
    if scope == "users:read.email" and requires_users_read_email(usecase_description, description):
        return True, "Required for RBAC/directory email lookup stated in the usecase.", 1.0

    entry = conditional_entry(scope)
    capability = entry.capability if entry else ""

    user_text = (
        f"{_build_agent_context(name, description, usecase_description, soul_text, skill_ids, trigger_mode)}\n\n"
        f"Requested scope: {scope}\n"
        f"Capability: {capability}\n\n"
        f"Should this agent be granted {scope}? Remember: default to deny unless clearly required."
    )

    payload, err = await _call_object(client, ADJUDICATE_SYSTEM, user_text)

    if err:
        # Gateway/LLM failure — NOT an AI denial. Tell the user so they can retry
        # rather than reading it as "AI said no".
        return (
            False,
            "Could not reach the AI gateway to evaluate this request — not granted (try again). "
            "The team has been notified via logs.",
            0.0,
        )
    if not payload:
        return False, "The AI returned no decision — denied by default.", 0.0

    allowed = bool(payload.get("allowed"))
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(payload.get("reason") or "").strip() or ("Allowed by AI review." if allowed else "Denied by AI review.")

    # Require the threshold for a grant; denials always pass through.
    if allowed and confidence < threshold:
        return False, f"Confidence too low to grant ({reason}).", confidence
    return allowed, reason, confidence
