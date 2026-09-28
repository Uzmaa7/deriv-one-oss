"""Skill index generation and metadata helpers for Slack skill routing."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Optional

import anthropic

from skill_router import _extract_text_from_response

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).parent.parent / "src" / "data" / "skills"
MAX_SKILL_INDEX_CHARS = int(os.environ.get("SKILL_INDEX_MAX_CHARS", "8000"))
from model_catalog import platform_default_model

INDEX_GENERATOR_MODEL: str = (
    os.environ.get("SKILL_INDEX_MODEL", "").strip()
    or os.environ.get("SCOPE_ROUTER_MODEL", "").strip()
    or os.environ.get("SKILL_ROUTER_MODEL", "").strip()
    or platform_default_model()
)
_ORCHESTRATOR_SLUG_HINTS = (
    "orchestrator",
    "routing",
    "router",
    "workflow",
    "main-skill",
    "main_skill",
)

INDEX_GENERATOR_SYSTEM = """
You write a compact Skill Index for a Slack AI agent.

The index helps a router pick 1-2 skills per message. It must be concise markdown
(aim for under 6000 characters). Include:

1. **When to use each skill** — one line per attached slug (use exact slug names)
2. **Common flows** — multi-step patterns (e.g. intake → investigation → delivery)
3. **Do not combine** — pairs that should not load together unless explicitly asked
4. **Overlay skills** — skills that add guidelines but are never selected alone

If an attached skill is clearly an orchestrator/router/workflow skill, distill its
workflow into section 2 instead of telling the router to load it with everything.

Rules:
- Only reference slugs from the attached list
- No API keys, secrets, or implementation code
- Be specific about triggers (alert investigation, DSAR intake, P2P case, etc.)
- Output markdown only, no preamble
""".strip()


def _skill_md_path(slug: str) -> Path | None:
    for name in ("skill.md", "SKILL.md"):
        path = SKILLS_DIR / slug / name
        if path.is_file():
            return path
    return None


def parse_skill_frontmatter(slug: str) -> dict[str, str]:
    """Return {slug, name, description} from skill frontmatter."""
    path = _skill_md_path(slug)
    if not path:
        return {"slug": slug, "name": slug, "description": ""}
    text = path.read_text(encoding="utf-8", errors="replace")
    name = slug
    description = ""
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            fm = text[3:end]
            for line in fm.splitlines():
                lower = line.lower()
                if lower.startswith("name:"):
                    name = line.split(":", 1)[1].strip()
                elif lower.startswith("description:"):
                    raw = line.split(":", 1)[1].strip()
                    if raw in (">", ">-", "|", "|-", ">"):
                        continue
                    description = raw.strip('"').strip("'")
    if not description:
        from skills_acl import get_skill_acl

        acl = get_skill_acl(slug)
        if acl and acl.description:
            description = acl.description.strip()
    if not description:
        description = slug.replace("-", " ")
    return {"slug": slug, "name": name, "description": description}


def load_skill_body_for_generation(slug: str, *, max_chars: int = 48_000) -> str:
    """Load skill.md body (+ refs) for index generation. Not for runtime routing."""
    path = _skill_md_path(slug)
    if not path:
        return ""
    text = path.read_text(encoding="utf-8", errors="replace")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4 :].strip()
    refs_dir = SKILLS_DIR / slug / "references"
    if refs_dir.is_dir():
        for ref in sorted(refs_dir.glob("*.md")):
            try:
                ref.resolve().relative_to(refs_dir.resolve())
            except ValueError:
                continue
            text += f"\n\n## {ref.stem}\n\n{ref.read_text(encoding='utf-8', errors='replace')}"
    if len(text) > max_chars:
        text = text[:max_chars] + "\n\n[... truncated for index generation ...]"
    return text


def index_is_stale(agent_skill_ids: list[str], source_skill_ids: list[str]) -> bool:
    return sorted(agent_skill_ids) != sorted(source_skill_ids)


def _is_orchestrator_slug(slug: str) -> bool:
    lowered = slug.lower()
    return any(hint in lowered for hint in _ORCHESTRATOR_SLUG_HINTS)


async def generate_skill_index_text(
    *,
    agent_name: str,
    skill_ids: list[str],
    client: anthropic.AsyncAnthropic,
) -> str:
    """Generate a compact skill index from full skill bodies (generation-time only)."""
    if not skill_ids:
        return ""

    sections: list[str] = []
    for slug in skill_ids:
        body = load_skill_body_for_generation(slug)
        meta = parse_skill_frontmatter(slug)
        label = " [orchestrator]" if _is_orchestrator_slug(slug) else ""
        sections.append(
            f"### Skill: {slug}{label}\n"
            f"Name: {meta['name']}\n"
            f"Description: {meta['description']}\n\n"
            f"{body or '(no content found)'}"
        )

    user_text = (
        f"Agent name: {agent_name or '(unnamed)'}\n\n"
        f"Attached skills ({len(skill_ids)}): {', '.join(skill_ids)}\n\n"
        "Full skill content for each attached skill:\n\n"
        + "\n\n---\n\n".join(sections)
    )

    try:
        response = await client.messages.create(
            model=INDEX_GENERATOR_MODEL,
            max_tokens=4096,
            system=INDEX_GENERATOR_SYSTEM,
            messages=[{"role": "user", "content": user_text}],
        )
    except Exception:
        logger.exception("skill_index: generation LLM call failed")
        return _fallback_index_from_metadata(skill_ids)

    raw = _extract_text_from_response(response)
    text = raw.strip()
    if not text:
        return _fallback_index_from_metadata(skill_ids)
    if len(text) > MAX_SKILL_INDEX_CHARS:
        text = text[:MAX_SKILL_INDEX_CHARS] + "\n\n[... index truncated ...]"
    return text


def _fallback_index_from_metadata(skill_ids: list[str]) -> str:
    lines = ["# Skill Index (auto-generated from metadata)", "", "## When to use each skill"]
    for slug in skill_ids:
        meta = parse_skill_frontmatter(slug)
        lines.append(f"- **{slug}**: {meta['description']}")
    return "\n".join(lines)[:MAX_SKILL_INDEX_CHARS]
