"""AI consolidation: episodic the blob store shards → bounded MEMORY.md."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

from model_catalog import platform_default_model
from memory_store.memory_archive import (
    list_agent_memory_archive,
    read_object_text,
    write_curated_memory_md,
)

logger = logging.getLogger(__name__)

_MAX_EPISODES = 40
_EPISODE_SNIPPET_CHARS = 1_800
_CURATED_MAX_CHARS = 14_000
_MODEL = platform_default_model()

_CONSOLIDATION_SYSTEM = (
    "You are a memory curator for an internal support-routing AI agent. "
    "You distill many episodic markdown shards into a small, durable MEMORY.md. "
    "You must aggressively exclude case-specific and personal data. "
    "Output only valid Markdown (no wrapping code fences around the document)."
)

_CONSOLIDATION_USER = """\
You are rewriting MEMORY.md for an AI agent from episodic archive files.

## What MEMORY.md is for (curated layer)
- Standing **rules**, **routing logic**, **classifiers**, **workflows**, and **channel behaviors** that should apply to *future* conversations.
- NOT a case log, ticket history, or investigation notebook.

## MUST EXCLUDE from output (even if present in episodes)
- Specific ticket/case/issue numbers (#50084, CR-00042, external ticket IDs used as examples).
- Transaction IDs, payment reference UUIDs, trace IDs, amounts, payment dates, PSP names tied to one case.
- Client/user account identifiers (account numbers, emails, balances, "pending issue for user X").
- One-off investigation outcomes ("the payments system returned no_data for case Y").
- Duplicate or near-duplicate rules — merge into one entry.

## MAY INCLUDE (generalized)
- How to classify ID types (e.g. banking-app UUID → reference_id, not trace_id).
- How to pick display IDs (external_ticket_number vs case_ref) as a **rule**, without citing real numbers.
- Status → emoji/label mappings for case summaries.
- Escalation **playbooks** (steps, which team, which channel) without naming a specific case.
- Stable Slack routing (e.g. compliance duplicate channel name, which bot role handles pickup) — use generic role labels; avoid repeating Slack user IDs unless essential for routing.
- Required skills for actions (skill slug only).
- Channel-scoped cron/digest behavior → **scope: channel** with **channel_id** set (Slack channel ID from episodes).

## Scope rules
- **agent**: applies everywhere for this agent.
- **user**: only durable preference/context for a user archetype — almost never store open escalations; prefer omitting user scope unless a clear lasting preference exists.
- **channel**: instructions tied to one Slack channel; always set **channel_id**: C… on that entry.

## Entry format (repeat per fact; target {entry_target_min}–{entry_target_max} entries total)
## entry:<uuid>
- **scope**: agent | user | channel
- **channel_id**: <Slack channel ID>   # required when scope is channel; omit otherwise
- **key**: snake_case_identifier
- **confidence**: high | medium
- **text**: one or two sentences; no PII; no specific case numbers

- Reuse entry IDs from existing MEMORY.md when the rule is unchanged; new rules get new UUIDs.
- Remove entries from existing MEMORY.md that are case-specific or obsolete.
- Keep the full document under {max_chars} characters.

## YAML front matter (required at top)
---
consolidation: ai
consolidated_at: <ISO-8601 UTC>
episode_count: {episode_count}
entry_count: <number of ## entry: blocks you wrote>
---

Then `# Curated memory` and the entries.

## Existing MEMORY.md (may be a migration draft — do not copy case dumps forward)
{existing}

## Episodic sources ({episode_count} files, newest first — extract rules only)
{episodes}

Write the complete updated MEMORY.md now.
"""


def _collect_episode_paths(archive: dict[str, Any]) -> list[str]:
    paths: list[tuple[str, str]] = []
    for ep in archive.get("hybrid_episodes") or []:
        p = ep.get("path")
        u = ep.get("updated_at") or ""
        if p:
            paths.append((u, p))
    for ep in archive.get("migration_episodes") or []:
        p = ep.get("path")
        u = ep.get("updated_at") or ""
        if p:
            paths.append((u, p))
    paths.sort(key=lambda x: x[0], reverse=True)
    seen: set[str] = set()
    ordered: list[str] = []
    for _, p in paths:
        if p in seen:
            continue
        seen.add(p)
        ordered.append(p)
        if len(ordered) >= _MAX_EPISODES:
            break
    return ordered


def consolidate_agent_memory(agent_id: str) -> dict[str, Any]:
    """Run LLM consolidation from the blob store episodes into MEMORY.md. Raises on misconfiguration."""
    api_key = (os.environ.get("MODEL_GATEWAY_API_KEY") or "").strip()
    base_url = (os.environ.get("MODEL_GATEWAY_URL") or "").strip()
    if not api_key:
        raise RuntimeError("MODEL_GATEWAY_API_KEY is not configured")

    archive = list_agent_memory_archive(agent_id)
    if not archive.get("configured"):
        raise RuntimeError("the blob store memory archive is not configured")

    episode_paths = _collect_episode_paths(archive)
    if not episode_paths:
        raise ValueError("No episodic memory files in the blob store to consolidate")

    existing = ""
    curated = archive.get("curated")
    if curated and curated.get("content"):
        existing = str(curated["content"])[:_CURATED_MAX_CHARS]

    episode_blocks: list[str] = []
    for path in episode_paths:
        body = read_object_text(agent_id, path, max_chars=_EPISODE_SNIPPET_CHARS)
        if body:
            episode_blocks.append(f"### {path}\n\n{body}")

    import anthropic

    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=base_url.rstrip("/").removesuffix("/v1") if base_url else None,
    )
    prompt = _CONSOLIDATION_USER.format(
        max_chars=_CURATED_MAX_CHARS,
        entry_target_min=8,
        entry_target_max=22,
        existing=existing or "(none)",
        episode_count=len(episode_paths),
        episodes="\n\n---\n\n".join(episode_blocks),
    )
    response = client.messages.create(
        model=_MODEL,
        max_tokens=4096,
        system=_CONSOLIDATION_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        from token_tracking import record_token_usage

        record_token_usage("memory_consolidation", _MODEL, response.usage)
    except Exception:
        pass

    text = "".join(
        b.text for b in response.content if hasattr(b, "text") and b.text
    ).strip()
    if not text:
        raise RuntimeError("Consolidation model returned empty output")
    if len(text) > _CURATED_MAX_CHARS:
        text = text[:_CURATED_MAX_CHARS] + "\n\n… [truncated to curated budget]\n"

    if not text.startswith("---") or "consolidation:" not in text[:400]:
        stamp = datetime.now(timezone.utc).isoformat()
        text = (
            f"---\nconsolidation: ai\nconsolidated_at: {stamp}\n"
            f"episode_count: {len(episode_paths)}\n---\n\n{text}"
        )

    meta = write_curated_memory_md(agent_id, text)
    if not meta:
        raise RuntimeError("Failed to write MEMORY.md to the blob store")

    return {
        "agent_id": agent_id,
        "episodes_used": len(episode_paths),
        "curated": meta,
        "chars": len(text),
    }
