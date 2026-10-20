"""Automatic skill intent routing for workspace chat.

When a user sends a message without an explicit skill selection, classify their
intent against the catalog of skills they are permitted to access, verify the
match against the full skill instructions, and suggest the best match.
"""

from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal, Optional

import anthropic
from api_access import (
    _active_grants_for_user,
    _all_known_slugs,
    _pending_requests_for_user,
)
from database import SessionLocal
from org_directory import get_employee_by_email
from skills_acl import SkillAccessResult, is_super_user, resolve_skills_for_user

logger = logging.getLogger(__name__)

from model_catalog import platform_default_model

ROUTER_MODEL: str = (
    os.environ.get("SKILL_ROUTER_MODEL", "").strip()
    or os.environ.get("APP_CLASSIFIER_MODEL", "").strip()
    or platform_default_model()
)
DEFAULT_THRESHOLD: float = float(os.environ.get("SKILL_ROUTER_THRESHOLD", "0.55"))
# Workspace chat only: auto-apply when confidence clears this bar; otherwise disambiguate.
AUTO_SKILL_THRESHOLD: float = float(os.environ.get("SKILL_ROUTER_AUTO_THRESHOLD", "0.80"))
CANDIDATE_MIN_CONFIDENCE: float = float(os.environ.get("SKILL_ROUTER_CANDIDATE_MIN", "0.35"))
ROUTER_MAX_CATALOG_CHARS: int = 24_000
VERIFY_MAX_SKILL_CHARS: int = int(os.environ.get("SKILL_ROUTER_VERIFY_MAX_CHARS", "12000"))
ROUTER_CONTEXT_MESSAGES: int = int(os.environ.get("SKILL_ROUTER_CONTEXT_MESSAGES", "4"))
SLACK_MESSAGE_SKILL_MAX: int = int(os.environ.get("SLACK_SKILL_ROUTER_MAX", "2"))
# Agent skill inference (multi-select from an agent's description, not chat routing).
AGENT_SKILL_THRESHOLD: float = float(os.environ.get("SKILL_ROUTER_AGENT_THRESHOLD", "0.5"))
AGENT_SKILL_MAX_SUGGESTIONS: int = int(os.environ.get("SKILL_ROUTER_AGENT_MAX", "6"))
ROUTER_RANK_MAX_TOKENS: int = int(os.environ.get("SKILL_ROUTER_RANK_MAX_TOKENS", "2048"))


def normalize_gateway_base_url(url: str) -> str:
    """Strip a trailing /v1 so the Anthropic SDK posts to {base}/v1/messages, not {base}/v1/v1/messages.

    The Anthropic client appends /v1/messages itself; an OpenAI-style base URL ending
    in /v1 causes a 404 from the gateway.
    """
    raw = (url or "").strip()
    u = raw.rstrip("/")
    if u.lower().endswith("/v1"):
        u = u[: -len("/v1")].rstrip("/")
    return u or raw.rstrip("/")

ROUTER_SYSTEM = """
You are a skill router. Pick the single most relevant skill for the user's request.

Each skill entry is formatted as: slug: description

Rules:
- Use recent conversation context — short follow-ups (e.g. "what about HubSpot?" after a
  SaaS build-vs-buy question) usually continue the same skill from the prior turn
- If active_skill_hint is provided and the current message is a follow-up or continuation,
  strongly prefer that skill unless the user clearly switched to a different topic
- If the message clearly maps to one skill, return that slug with confidence >= 0.7
- If ambiguous, return the best guess with confidence between 0.5 and 0.69
- If no skill is relevant or the query is general conversation, return slug null
- Only return slugs that appear in the catalog

Respond ONLY with JSON: {"slug": "<slug>|null", "confidence": 0.0-1.0, "reason": "..."}
""".strip()

ROUTER_MULTI_SYSTEM = """
You are a skill router. Rank the best matching skills for the user's request.

Each skill entry is formatted as: slug: description

Rules:
- Use recent conversation context for follow-ups
- Return up to 3 skills, most relevant first
- confidence 0.0-1.0 reflects how well the skill fits this specific message
- If no skill is relevant (general conversation), return an empty candidates array
- Only return slugs that appear in the catalog

Respond ONLY with JSON:
{"candidates": [{"slug": "<slug>", "confidence": 0.0-1.0, "reason": "..."}]}
""".strip()

VERIFY_SYSTEM = """
You verify whether a skill can answer the user's request given its FULL instructions.

Rules:
- Read the complete skill content — capabilities, triggers, and explicit do-nots
- Use recent conversation context; follow-ups that continue the same task should fit
  if the skill domain covers the new subject (e.g. CRM build-vs-buy after support tooling)
- Return fit=false if the skill lacks the APIs, data sources, or scope needed
- Return fit=false if the skill explicitly says not to handle this type of question

Respond ONLY with JSON: {"fit": true|false, "confidence": 0.0-1.0, "reason": "..."}
""".strip()


AGENT_SKILL_ROUTER_SYSTEM = """
You assign skills to an AI agent from a catalog of available skills.

Given the agent's name and description, return ALL skills the agent will plausibly
need to do its job. Prefer precision over recall — do not include a skill unless the
description clearly calls for its domain. For example, a treasury agent that reports
daily allocations by querying the warehouse and posting a PDF summary needs
warehouse-analytics and pdf-generation; it does not need brand-guidelines unless it
produces branded material.

Rules:
- Return a JSON array of objects, each: {"slug": "<slug>", "confidence": 0.0-1.0, "reason": "..."}
- Order by relevance, most relevant first.
- Only return slugs that appear in the catalog.
- If no skill is relevant, return [].
- Cap at 6 skills.

Respond ONLY with a JSON array: [{"slug": "...", "confidence": 0.0-1.0, "reason": "..."}]
""".strip()

SLACK_MESSAGE_SKILL_ROUTER_SYSTEM = """
You route Slack messages to the most relevant skills for an AI agent.

You are given:
- The agent's Skill Index (routing map written by the agent owner)
- Recent user messages from the thread
- The current message
- A catalog of allowed skill slugs (fallback if the index is incomplete)

Rules:
- Return 0-2 skills, ordered by relevance. Most messages need exactly 1 skill.
- Return 2 only for genuine cross-domain requests.
- Only return slugs from the allowed catalog.
- Use the Skill Index flows for phase/workflow messages ("continue", "phase 1", structured intakes).
- If active_skill_hint is provided and the message is a short follow-up, strongly prefer that skill.
- Overlay/guideline skills should never be returned alone.
- If no skill fits or you are uncertain, return [].

Respond ONLY with a JSON array:
[{"slug": "<slug>", "confidence": 0.0-1.0, "reason": "..."}]
""".strip()


@dataclass
class SkillCandidate:
    slug: str
    confidence: float
    reason: str


@dataclass
class SkillRoutingResult:
    """Outcome of workspace chat skill routing."""

    mode: Literal["auto", "disambiguation", "none"]
    slug: Optional[str] = None
    confidence: float = 0.0
    reason: str = ""
    candidates: list[SkillCandidate] = field(default_factory=list)


def build_accessible_catalog(results: list[SkillAccessResult]) -> dict[str, str]:
    """Build slug -> description map for skills the user can access."""
    catalog: dict[str, str] = {}
    for result in results:
        if not result.accessible:
            continue
        description = ""
        if result.acl and result.acl.description:
            description = result.acl.description.strip()
        if not description:
            description = result.slug.replace("-", " ")
        catalog[result.slug] = description
    return catalog


def build_accessible_catalog_from_slugs(slugs: list[str]) -> dict[str, str]:
    """Build slug -> description from disk frontmatter with ACL fallback."""
    from skill_index import parse_skill_frontmatter

    catalog: dict[str, str] = {}
    for slug in slugs:
        meta = parse_skill_frontmatter(slug)
        catalog[slug] = meta.get("description") or slug.replace("-", " ")
    return catalog


def accessible_slugs_from_results(results: list[SkillAccessResult]) -> frozenset[str]:
    return frozenset(r.slug for r in results if r.accessible)


def recent_user_messages(
    history: list[dict],
    *,
    limit: int = ROUTER_CONTEXT_MESSAGES,
) -> list[str]:
    """Return the last N user message texts from chat history (prior turns only)."""
    texts: list[str] = []
    for entry in history:
        if entry.get("role") != "user":
            continue
        content = entry.get("content")
        if isinstance(content, str) and content.strip():
            texts.append(content.strip())
    return texts[-limit:]


async def resolve_user_skill_access(
    user_sub: str,
    user_email: Optional[str],
) -> tuple[list[SkillAccessResult], dict[str, str], frozenset[str]]:
    """Resolve ACL for the current user and return results, catalog, and slug set."""
    email = (user_email or user_sub or "").strip()
    # IAM lookup is async — do it before opening the DB session so we don't
    # hold a sync session open across an await boundary.
    employee = await get_employee_by_email(email) if email else None
    user_department = employee.department if employee else ""
    user_role = employee.role if employee else None
    is_exec = is_super_user(email) if email else False

    from skill_submission_models import REVIEW_STATUS_PASS as _PASS
    from skill_submission_models import SkillSubmission
    from skill_collaborators import collaborator_slugs_for_email

    db = SessionLocal()
    try:
        owned_slugs: frozenset[str] = frozenset(
            row.proposed_slug
            for row in db.query(SkillSubmission.proposed_slug).filter(
                SkillSubmission.user_id == user_sub,
                SkillSubmission.review_status == _PASS,
            ).all()
        )
        collaborator_slugs = collaborator_slugs_for_email(db, email or "")

        active = _active_grants_for_user(user_sub, email, db)
        pending = _pending_requests_for_user(user_sub, email, db)
    finally:
        db.close()

    results = resolve_skills_for_user(
        user_department,
        active,
        pending,
        _all_known_slugs(),
        job_title=user_role,
        is_executive=is_exec,
        owned_slugs=owned_slugs,
        collaborator_slugs=collaborator_slugs,
        email=email or None,
    )
    catalog = build_accessible_catalog(results)
    slugs = accessible_slugs_from_results(results)
    return results, catalog, slugs


def _format_catalog(catalog: dict[str, str]) -> str:
    lines = [f"{slug}: {desc}" for slug, desc in sorted(catalog.items())]
    text = "\n".join(lines)
    if len(text) <= ROUTER_MAX_CATALOG_CHARS:
        return text
    truncated: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > ROUTER_MAX_CATALOG_CHARS:
            break
        truncated.append(line)
        used += len(line) + 1
    return "\n".join(truncated)


def _format_routing_context(
    message: str,
    recent_messages: list[str],
    active_skill_hint: Optional[str],
) -> str:
    parts: list[str] = []
    if active_skill_hint:
        parts.append(f"Active skill from prior turn (auto-selected): {active_skill_hint}")
    if recent_messages:
        parts.append("Recent user messages (oldest first):")
        for idx, prior in enumerate(recent_messages, start=1):
            parts.append(f"  {idx}. {prior}")
    parts.append(f"Current user message:\n{message.strip()}")
    return "\n\n".join(parts)


def _truncate_skill_content(content: str) -> str:
    content = content.strip()
    if len(content) <= VERIFY_MAX_SKILL_CHARS:
        return content
    return content[:VERIFY_MAX_SKILL_CHARS] + "\n\n[... skill content truncated ...]"


def _extract_json_object(text: str) -> Optional[dict]:
    text = text.strip()
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
    """Best-effort extraction of a JSON array of objects from an LLM response."""
    text = text.strip()
    if not text:
        return []

    fence = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        pass

    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        parsed = json.loads(text[start : end + 1])
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    except json.JSONDecodeError:
        return []
    return []


def _extract_text_from_response(response: object) -> str:
    parts: list[str] = []
    for block in getattr(response, "content", []) or []:
        block_type = getattr(block, "type", None)
        if block_type == "text":
            parts.append(block.text)
        elif block_type == "thinking":
            continue
        elif hasattr(block, "text"):
            parts.append(block.text)
    return "\n".join(parts).strip()


async def _call_router_json(
    client: anthropic.AsyncAnthropic,
    system: str,
    user_text: str,
    *,
    model: Optional[str] = None,
    max_tokens: int = 512,
) -> Optional[dict]:
    from agent_llm import cached_system

    router_model = (model or ROUTER_MODEL or "").strip()
    if not router_model:
        return None

    for attempt in range(2):
        try:
            response = await client.messages.create(
                model=router_model,
                max_tokens=max_tokens,
                system=cached_system(system, model=router_model),
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception:
            logger.warning("skill_router: LLM call failed", exc_info=True)
            return None

        raw = _extract_text_from_response(response)
        payload = _extract_json_object(raw)
        if payload is not None:
            return payload
        if raw:
            logger.warning("skill_router: could not parse router response: %r", raw[:200])
        else:
            logger.warning("skill_router: empty LLM response (attempt %d)", attempt + 1)
    return None


async def _call_router_array(
    client: anthropic.AsyncAnthropic,
    system: str,
    user_text: str,
    *,
    model: Optional[str] = None,
) -> list[dict]:
    """Like `_call_router_json` but expects a JSON array response.

    Uses a larger max_tokens budget than the single-object router: Gemini via the
    model gateway has been observed to return stop=max_tokens with zero content
    blocks at 512 tokens for array responses, producing an empty result. 2048 leaves
    comfortable headroom for the array plus per-skill reasoning text.
    """
    from agent_llm import cached_system

    router_model = (model or ROUTER_MODEL or "").strip()
    if not router_model:
        return []

    for attempt in range(2):
        try:
            response = await client.messages.create(
                model=router_model,
                max_tokens=ROUTER_RANK_MAX_TOKENS,
                system=cached_system(system, model=router_model),
                messages=[{"role": "user", "content": user_text}],
            )
        except Exception:
            logger.warning("skill_router: LLM call failed", exc_info=True)
            return []

        raw = _extract_text_from_response(response)
        items = _extract_json_array(raw)
        if items:
            return items
        if raw:
            logger.warning("skill_router: could not parse array response: %r", raw[:200])
        else:
            logger.warning("skill_router: empty LLM response (attempt %d)", attempt + 1)
    return []


def _normalize_router_response(
    payload: dict,
    accessible_catalog: dict[str, str],
    threshold: float,
) -> tuple[Optional[str], float, str]:
    raw_slug = payload.get("slug")
    slug: Optional[str]
    if raw_slug in (None, "", "null", "none"):
        slug = None
    elif isinstance(raw_slug, str):
        slug = raw_slug.strip() or None
    else:
        slug = None

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))

    reason = str(payload.get("reason") or "").strip()

    if slug is not None and slug not in accessible_catalog:
        logger.warning("skill_router: model returned inaccessible slug %r", slug)
        return None, confidence, reason or "Suggested skill is not accessible."

    if slug is None or confidence < threshold:
        return None, confidence, reason

    return slug, confidence, reason


def _normalize_verify_response(
    payload: dict,
    threshold: float,
) -> tuple[bool, float, str]:
    fit = bool(payload.get("fit"))
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    reason = str(payload.get("reason") or "").strip()
    return fit and confidence >= threshold, confidence, reason


async def _verify_skill_fit(
    message: str,
    recent_messages: list[str],
    active_skill_hint: Optional[str],
    slug: str,
    skill_content: str,
    client: anthropic.AsyncAnthropic,
    threshold: float,
) -> tuple[bool, float, str]:
    context = _format_routing_context(message, recent_messages, active_skill_hint)
    user_text = (
        f"Skill slug: {slug}\n\n"
        f"Full skill instructions:\n{_truncate_skill_content(skill_content)}\n\n"
        f"Conversation:\n{context}"
    )
    payload = await _call_router_json(client, VERIFY_SYSTEM, user_text)
    if payload is None:
        logger.warning("skill_router: verify step failed for slug=%r", slug)
        return False, 0.0, ""
    fit, confidence, reason = _normalize_verify_response(payload, threshold)
    logger.info(
        "skill_router: verify slug=%s fit=%s confidence=%.2f reason=%r",
        slug,
        fit,
        confidence,
        reason[:120],
    )
    return fit, confidence, reason


def _catalog_pick_to_candidate(
    payload: dict,
    accessible_catalog: dict[str, str],
) -> Optional[SkillCandidate]:
    """Turn a single-slug router JSON object into a disambiguation candidate."""
    raw_slug = payload.get("slug")
    if raw_slug in (None, "", "null", "none"):
        return None
    if not isinstance(raw_slug, str):
        return None
    slug = raw_slug.strip()
    if not slug or slug not in accessible_catalog:
        return None
    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    if confidence < CANDIDATE_MIN_CONFIDENCE:
        return None
    reason = str(payload.get("reason") or "").strip() or "Best catalog match."
    return SkillCandidate(slug=slug, confidence=confidence, reason=reason)


def _normalize_multi_candidates(
    payload: dict,
    accessible_catalog: dict[str, str],
    *,
    max_items: int = 3,
) -> list[SkillCandidate]:
    raw = payload.get("candidates")
    if not isinstance(raw, list):
        return []

    out: list[SkillCandidate] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        raw_slug = item.get("slug")
        if not isinstance(raw_slug, str):
            continue
        slug = raw_slug.strip()
        if not slug or slug in seen or slug not in accessible_catalog:
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if confidence < CANDIDATE_MIN_CONFIDENCE:
            continue
        reason = str(item.get("reason") or "").strip()
        seen.add(slug)
        out.append(SkillCandidate(slug=slug, confidence=confidence, reason=reason))

    out.sort(key=lambda c: c.confidence, reverse=True)
    return out[:max_items]


async def _rank_catalog_candidates(
    message: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    *,
    router_model: Optional[str] = None,
    recent_messages: Optional[list[str]] = None,
    active_skill_hint: Optional[str] = None,
) -> list[SkillCandidate]:
    prior = recent_messages or []
    context = _format_routing_context(message, prior, active_skill_hint)
    payload = await _call_router_json(
        client,
        ROUTER_MULTI_SYSTEM,
        f"{context}\n\nAvailable skills:\n{_format_catalog(accessible_catalog)}",
        model=router_model,
        max_tokens=ROUTER_RANK_MAX_TOKENS,
    )
    if payload is None:
        logger.warning("skill_router: multi-router response missing or unparseable")
        return []
    ranked = _normalize_multi_candidates(payload, accessible_catalog)
    if not ranked:
        logger.info("skill_router: multi-router returned no candidates above min confidence")
    return ranked


async def _fallback_catalog_pick_candidates(
    message: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    *,
    router_model: Optional[str] = None,
    recent_messages: Optional[list[str]] = None,
    active_skill_hint: Optional[str] = None,
) -> list[SkillCandidate]:
    """Second-stage LLM pick when the multi-ranker returns nothing."""
    prior = recent_messages or []
    context = _format_routing_context(message, prior, active_skill_hint)
    payload = await _call_router_json(
        client,
        ROUTER_SYSTEM,
        f"{context}\n\nAvailable skills:\n{_format_catalog(accessible_catalog)}",
        model=router_model,
        max_tokens=ROUTER_RANK_MAX_TOKENS,
    )
    if payload is None:
        return []
    candidate = _catalog_pick_to_candidate(payload, accessible_catalog)
    if candidate is None:
        return []
    logger.info(
        "skill_router: single-slug fallback picked %s (%.2f)",
        candidate.slug,
        candidate.confidence,
    )
    return [candidate]


async def route_skill_for_chat(
    message: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    router_model: Optional[str] = None,
    recent_messages: Optional[list[str]] = None,
    active_skill_hint: Optional[str] = None,
    load_skill_content: Optional[Callable[[str], str]] = None,
    auto_threshold: float = AUTO_SKILL_THRESHOLD,
) -> SkillRoutingResult:
    """Route workspace chat: auto-apply one skill, ask user to pick, or general mode.

    Slack agents must continue to use ``suggest_skills_for_message`` — this path is
    workspace-chat only (single skill + optional disambiguation UI).
    """
    if not accessible_catalog:
        return SkillRoutingResult(mode="none")

    prior = recent_messages or []
    is_likely_followup = len(message.split()) <= 12 and bool(prior or active_skill_hint)

    ranked = await _rank_catalog_candidates(
        message,
        accessible_catalog,
        client,
        router_model=router_model,
        recent_messages=prior,
        active_skill_hint=active_skill_hint,
    )
    if not ranked:
        ranked = await _fallback_catalog_pick_candidates(
            message,
            accessible_catalog,
            client,
            router_model=router_model,
            recent_messages=prior,
            active_skill_hint=active_skill_hint,
        )

    async def _try_auto(
        slug: str,
        catalog_confidence: float,
        catalog_reason: str,
    ) -> Optional[SkillRoutingResult]:
        if not load_skill_content:
            if catalog_confidence >= auto_threshold:
                return SkillRoutingResult(
                    mode="auto",
                    slug=slug,
                    confidence=catalog_confidence,
                    reason=catalog_reason or "High-confidence catalog match.",
                )
            return None
        skill_content = load_skill_content(slug)
        if not skill_content or len(skill_content.strip()) < 80:
            return None
        fit, verify_conf, verify_reason = await _verify_skill_fit(
            message,
            prior,
            active_skill_hint,
            slug,
            skill_content,
            client,
            threshold,
        )
        if not fit:
            return None
        confidence = max(catalog_confidence, verify_conf)
        if confidence < auto_threshold:
            return None
        return SkillRoutingResult(
            mode="auto",
            slug=slug,
            confidence=confidence,
            reason=verify_reason or catalog_reason,
        )

    if is_likely_followup and active_skill_hint and active_skill_hint in accessible_catalog:
        hint_rank = next((c for c in ranked if c.slug == active_skill_hint), None)
        hint_conf = hint_rank.confidence if hint_rank else threshold
        hint_reason = hint_rank.reason if hint_rank else "Continuing prior skill."
        auto_hint = await _try_auto(active_skill_hint, hint_conf, hint_reason)
        if auto_hint is not None:
            return auto_hint

    if ranked:
        auto_best = await _try_auto(ranked[0].slug, ranked[0].confidence, ranked[0].reason)
        if auto_best is not None:
            return auto_best
        return SkillRoutingResult(mode="disambiguation", candidates=ranked)

    return SkillRoutingResult(mode="none")


async def suggest_skill(
    message: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    recent_messages: Optional[list[str]] = None,
    active_skill_hint: Optional[str] = None,
    load_skill_content: Optional[Callable[[str], str]] = None,
) -> tuple[Optional[str], float, str]:
    """Legacy single-skill classify+verify helper.

    Workspace chat uses ``route_skill_for_chat`` instead. Kept for callers/tests
    that still expect the older (slug, confidence, reason) tuple API.
    """
    routing = await route_skill_for_chat(
        message,
        accessible_catalog,
        client,
        threshold,
        recent_messages=recent_messages,
        active_skill_hint=active_skill_hint,
        load_skill_content=load_skill_content,
    )
    if routing.mode == "auto" and routing.slug:
        return routing.slug, routing.confidence, routing.reason
    return None, routing.confidence, routing.reason


def _normalize_agent_skill_response(
    items: list[dict],
    accessible_catalog: dict[str, str],
    threshold: float,
    max_suggestions: int = AGENT_SKILL_MAX_SUGGESTIONS,
) -> list[tuple[str, float, str]]:
    """Normalize the array response into (slug, confidence, reason) tuples.

    Drops slugs not in the accessible catalog, anything below the threshold,
    and dedupes by slug (first occurrence wins, since the model orders by relevance).
    """
    seen: set[str] = set()
    out: list[tuple[str, float, str]] = []
    for item in items:
        raw_slug = item.get("slug")
        if not isinstance(raw_slug, str):
            continue
        slug = raw_slug.strip()
        if not slug or slug in seen:
            continue
        if slug not in accessible_catalog:
            logger.warning("skill_router: agent router returned inaccessible slug %r", slug)
            continue
        try:
            confidence = float(item.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if confidence < threshold:
            continue
        reason = str(item.get("reason") or "").strip()
        seen.add(slug)
        out.append((slug, confidence, reason))
    return out[:max_suggestions]


def _normalize_slack_message_skills(
    items: list[dict],
    accessible_catalog: dict[str, str],
    threshold: float,
    max_skills: int = SLACK_MESSAGE_SKILL_MAX,
) -> list[tuple[str, float, str]]:
    return _normalize_agent_skill_response(
        items,
        accessible_catalog,
        threshold,
        max_suggestions=max_skills,
    )


async def suggest_skills_for_message(
    message: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    recent_messages: Optional[list[str]] = None,
    active_skill_hint: Optional[str] = None,
    skill_index_text: Optional[str] = None,
    max_skills: int = SLACK_MESSAGE_SKILL_MAX,
) -> tuple[list[str], float, str, bool]:
    """Pick up to *max_skills* slugs for a Slack turn.

    Returns (selected_slugs, confidence, reason, used_fallback).
    When used_fallback is True the caller should load all accessible skills.
    """
    if not accessible_catalog:
        return [], 0.0, "", True

    prior = recent_messages or []
    context = _format_routing_context(message, prior, active_skill_hint)
    index_block = (skill_index_text or "").strip()
    if not index_block:
        index_block = "(no skill index — use catalog descriptions only)"

    # Short follow-up: prefer prior skill without a router call when confident enough.
    is_likely_followup = len(message.split()) <= 12 and bool(prior or active_skill_hint)
    if (
        is_likely_followup
        and active_skill_hint
        and active_skill_hint in accessible_catalog
    ):
        return [active_skill_hint], threshold, "Continuing prior skill for follow-up.", False

    user_text = (
        f"{context}\n\n"
        f"## Skill Index\n{index_block}\n\n"
        f"Allowed skills:\n{_format_catalog(accessible_catalog)}"
    )
    items = await _call_router_array(client, SLACK_MESSAGE_SKILL_ROUTER_SYSTEM, user_text)
    if not items:
        return [], 0.0, "Router returned no selection.", True

    normalized = _normalize_slack_message_skills(
        items,
        accessible_catalog,
        threshold,
        max_skills=max_skills,
    )
    if not normalized:
        return [], 0.0, "No skill met confidence threshold.", True

    slugs = [slug for slug, _, _ in normalized]
    top_conf = normalized[0][1]
    reason = normalized[0][2] or "Selected by skill router."
    return slugs, top_conf, reason, False


async def suggest_skills_for_agent(
    name: str,
    description: str,
    accessible_catalog: dict[str, str],
    client: anthropic.AsyncAnthropic,
    threshold: float = AGENT_SKILL_THRESHOLD,
) -> list[tuple[str, float, str]]:
    """Infer which skills an agent needs from its name + description.

    Unlike `suggest_skill` (chat routing, single best match, two-stage verify), this is
    a single LLM pass over the catalog that returns multiple relevant skills. The caller's
    ACL has already filtered `accessible_catalog`, so only skills the user can access are
    ever suggested.
    """
    desc = (description or "").strip()
    if not desc or not accessible_catalog:
        return []

    user_text = (
        f"Agent name: {(name or '').strip() or '(unnamed)'}\n\n"
        f"Agent description:\n{desc}\n\n"
        f"Available skills:\n{_format_catalog(accessible_catalog)}"
    )
    items = await _call_router_array(client, AGENT_SKILL_ROUTER_SYSTEM, user_text)
    if not items:
        return []
    return _normalize_agent_skill_response(items, accessible_catalog, threshold)
