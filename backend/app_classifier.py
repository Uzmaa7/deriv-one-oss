"""AI inference for new Lab apps — mode, tech stack, and skills from name + description.

Called synchronously from ``create_app`` when the client omits explicit config.
Uses a single structured LLM call; falls back to safe defaults on failure or
low confidence.
"""

from __future__ import annotations

from app_config import APP_NAME

import logging
import os
import re
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

from apps_models import APP_TECH_STACKS
from llm_json import MODEL_GATEWAY_API_KEY, MODEL_GATEWAY_URL, extract_json
from model_catalog import platform_default_model

logger = logging.getLogger(__name__)

_SKILLS_DIR = Path(__file__).parent.parent / "src" / "data" / "skills"
_SLUG_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$")

_CLASSIFIER_MODEL = os.environ.get(
    "APP_CLASSIFIER_MODEL",
    platform_default_model(),
)
_CLASSIFIER_TIMEOUT_S = float(os.environ.get("APP_CLASSIFIER_TIMEOUT_S", "20"))

Confidence = Literal["low", "medium", "high"]
AppMode = Literal["standard"]
TechStack = Literal["static", "python", "react"]

SAFE_MODE: AppMode = "standard"
SAFE_TECH_STACK: TechStack = "python"
SAFE_SKILL_IDS: list[str] = []

LAB_FIXED_MODE: AppMode = "standard"
LAB_FIXED_TECH: TechStack = "python"

# Complexity signals that route to React stack
_REACT_SIGNALS = (
    "multi-page", "multi page", "wizard", "workflow", "several screens",
    "navigation", "routing", "react", "spa", "single-page app",
    "form wizard", "client state", "dashboard with tabs",
)


class SkillSummary(BaseModel):
    slug: str
    description: str = ""


class AppClassification(BaseModel):
    mode: AppMode
    tech_stack: TechStack
    skill_ids: list[str] = Field(default_factory=list)
    confidence: Confidence
    reasoning: str = ""

    @field_validator("skill_ids", mode="before")
    @classmethod
    def _coerce_skill_ids(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(v).strip().lower() for v in value if str(v).strip()]
        return []


_CLASSIFY_TOOL = {
    "name": "classify_app",
    "description": (
        "Return the best build configuration for a new Lab app based on its name and description."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "tech_stack": {
                "type": "string",
                "enum": ["static", "python", "react"],
                "description": (
                    "python = simple vanilla HTML/JS dashboard with Python backend; "
                    "react = multi-page interactive app with React/Vite + optional Python backend; "
                    "static = front-end only (rejected at create — use python instead)."
                ),
            },
            "skill_ids": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Skill slugs from the provided catalog that the app will need. "
                    "Pick only skills clearly relevant; prefer an empty list when unsure."
                ),
            },
            "confidence": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": (
                    "low = description too vague; medium = reasonable guess; "
                    "high = clear scope, integrations, and UI complexity."
                ),
            },
            "reasoning": {
                "type": "string",
                "description": "One or two sentences explaining the classification (for logs only).",
            },
        },
        "required": ["tech_stack", "skill_ids", "confidence", "reasoning"],
    },
}


def safe_defaults(reasoning: str = "") -> AppClassification:
    return AppClassification(
        mode=SAFE_MODE,
        tech_stack=SAFE_TECH_STACK,
        skill_ids=list(SAFE_SKILL_IDS),
        confidence="low",
        reasoning=reasoning or "Applied safe defaults.",
    )


def _parse_frontmatter_description(content: str) -> str:
    if not content.startswith("---"):
        return ""
    end = content.find("\n---", 3)
    if end == -1:
        return ""
    block = content[3:end]
    for line in block.splitlines():
        if line.strip().lower().startswith("description:"):
            _, _, raw = line.partition(":")
            desc = raw.strip()
            if desc.startswith(">"):
                return desc.lstrip(">- ").strip()
            if (desc.startswith('"') and desc.endswith('"')) or (
                desc.startswith("'") and desc.endswith("'")
            ):
                return desc[1:-1].strip()
            return desc
    return ""


def _skill_md_path(slug_dir: Path) -> Optional[Path]:
    for name in ("skill.md", "SKILL.md"):
        candidate = slug_dir / name
        if candidate.is_file():
            return candidate
    return None


def list_available_skill_summaries() -> list[SkillSummary]:
    """Scan the on-disk skill registry and return slug + short description for each."""
    if not _SKILLS_DIR.is_dir():
        return []
    base = _SKILLS_DIR.resolve()
    out: list[SkillSummary] = []
    for entry in sorted(_SKILLS_DIR.iterdir()):
        try:
            resolved = entry.resolve()
            resolved.relative_to(base)
        except (ValueError, OSError):
            continue
        if not resolved.is_dir():
            continue
        slug = resolved.name
        if not slug or slug.startswith(".") or not _SLUG_RE.match(slug):
            continue
        md_path = _skill_md_path(resolved)
        description = ""
        if md_path:
            try:
                description = _parse_frontmatter_description(
                    md_path.read_text(encoding="utf-8", errors="replace")
                )
            except OSError:
                description = ""
        if not description:
            description = f"Skill package: {slug}"
        out.append(SkillSummary(slug=slug, description=description[:400]))
    return out


def _validate_classification(
    raw: dict,
    allowed_slugs: set[str],
) -> Optional[AppClassification]:
    try:
        tech_stack = raw.get("tech_stack")
        confidence = raw.get("confidence")
        if tech_stack not in APP_TECH_STACKS:
            return None
        if confidence not in ("low", "medium", "high"):
            return None
        skill_ids = [
            s for s in AppClassification._coerce_skill_ids(raw.get("skill_ids"))
            if s in allowed_slugs
        ]
        reasoning = str(raw.get("reasoning") or "").strip()[:500]
        return AppClassification(
            mode=SAFE_MODE,
            tech_stack=tech_stack,
            skill_ids=skill_ids,
            confidence=confidence,
            reasoning=reasoning,
        )
    except Exception:
        return None


def _heuristic_tech_stack(description: str) -> Optional[TechStack]:
    """Rule-based fallback when LLM confidence is low."""
    lower = description.lower()
    if any(sig in lower for sig in _REACT_SIGNALS):
        return "react"
    if len(description) > 500:
        return "react"
    return None


def _build_classifier_prompt(
    name: str,
    description: str,
    skills: list[SkillSummary],
) -> str:
    skill_lines = "\n".join(
        f"- `{s.slug}`: {s.description}" for s in skills
    ) or "(no skills available)"
    return f"""You classify new {APP_NAME} Lab apps so the platform can pick build settings automatically.

Given the app name and description, choose:
- **tech_stack**:
  - "python" = single-page dashboard, simple table/chart viewer, sheet-backed report, few screens
  - "react" = multi-page app, wizards, rich forms, client routing, complex UI state, many components
  - never "static" — use "python" for simple front-ends with optional backend
- **skill_ids**: zero or more slugs from the catalog — only clearly required skills
- **confidence**: "low" | "medium" | "high"

Rules:
- Prefer "python" for dashboards and data viewers unless the description clearly needs multi-page React.
- Pick "react" when the user mentions multiple pages, navigation, wizards, or complex interactive UI.
- Only use skill slugs from the catalog; never invent slugs.
- Rate confidence "low" if the description lacks concrete features.

App name: {name.strip()}
App description:
{description.strip()}

Available skills (slug → description):
{skill_lines}

Call the classify_app tool with your decision."""


def classify_new_app(
    name: str,
    description: str,
    available_skills: Optional[list[SkillSummary]] = None,
) -> AppClassification:
    """Infer mode, tech_stack, and skill_ids. Never raises — returns safe defaults on failure."""
    desc = (description or "").strip()
    if not desc:
        return safe_defaults("Empty description — using safe defaults.")

    skills = available_skills if available_skills is not None else list_available_skill_summaries()
    allowed_slugs = {s.slug for s in skills}

    if not MODEL_GATEWAY_API_KEY:
        logger.warning("classify_new_app: LLM not configured — using safe defaults")
        heuristic = _heuristic_tech_stack(desc)
        return AppClassification(
            mode=SAFE_MODE,
            tech_stack=heuristic or SAFE_TECH_STACK,
            skill_ids=[],
            confidence="low",
            reasoning="LLM not configured.",
        )

    import anthropic

    client = anthropic.Anthropic(api_key=MODEL_GATEWAY_API_KEY, base_url=MODEL_GATEWAY_URL)
    prompt = _build_classifier_prompt(name, desc, skills)

    try:
        response = client.messages.create(
            model=_CLASSIFIER_MODEL,
            max_tokens=512,
            messages=[{"role": "user", "content": prompt}],
            tools=[_CLASSIFY_TOOL],
            tool_choice={"type": "tool", "name": "classify_app"},
            timeout=_CLASSIFIER_TIMEOUT_S,
        )
    except Exception:
        logger.exception("classify_new_app: LLM call failed for app %r", name)
        return safe_defaults("LLM call failed.")

    raw: Optional[dict] = None
    for block in response.content:
        block_type = getattr(block, "type", "")
        if block_type == "tool_use" and getattr(block, "name", "") == "classify_app":
            raw = block.input if isinstance(block.input, dict) else None
            break
        if block_type == "text":
            parsed = extract_json(getattr(block, "text", "") or "")
            if isinstance(parsed, dict):
                raw = parsed

    if not raw:
        logger.warning("classify_new_app: no structured output for app %r", name)
        return safe_defaults("Malformed LLM output.")

    validated = _validate_classification(raw, allowed_slugs)
    if not validated:
        logger.warning("classify_new_app: invalid classification payload for app %r", name)
        return safe_defaults("Invalid classification payload.")

    return validated


def resolve_app_config(
    *,
    name: str,
    description: str,
    explicit_mode: Optional[str],
    explicit_tech_stack: Optional[str],
    explicit_skill_ids: Optional[list[str]],
) -> tuple[str, str, list[str], AppClassification]:
    """Merge explicit client values with AI inference. Low confidence → safe defaults for inferred fields."""
    needs_mode = explicit_mode is None
    needs_tech = explicit_tech_stack is None
    needs_skills = explicit_skill_ids is None

    if not (needs_mode or needs_tech or needs_skills):
        mode, tech_stack, skill_ids = _apply_lab_policy(
            explicit_mode or LAB_FIXED_MODE,
            explicit_tech_stack or LAB_FIXED_TECH,
            list(explicit_skill_ids or []),
        )
        return (
            mode,
            tech_stack,
            skill_ids,
            AppClassification(
                mode=mode,
                tech_stack=tech_stack,
                skill_ids=skill_ids,
                confidence="high",
                reasoning="All config fields supplied explicitly.",
            ),
        )

    classification = classify_new_app(name, description)
    use_inference = classification.confidence in ("medium", "high")

    mode = explicit_mode if not needs_mode else (
        classification.mode if use_inference else SAFE_MODE
    )
    tech_stack = explicit_tech_stack if not needs_tech else (
        classification.tech_stack if use_inference else SAFE_TECH_STACK
    )
    skill_ids = (
        list(explicit_skill_ids)
        if not needs_skills
        else (classification.skill_ids if use_inference else list(SAFE_SKILL_IDS))
    )

    # Low confidence: apply heuristic for react routing
    if needs_tech and not use_inference:
        heuristic = _heuristic_tech_stack(description)
        if heuristic:
            tech_stack = heuristic

    mode, tech_stack, skill_ids = _apply_lab_policy(mode, tech_stack, skill_ids)
    return mode, tech_stack, skill_ids, classification


def _apply_lab_policy(mode: str, tech_stack: str, skill_ids: list[str]) -> tuple[str, str, list[str]]:
    """Enforce Lab product constraints: standard mode, hybrid python|react stacks."""
    del mode  # overnight disabled — always standard
    if tech_stack == "static":
        tech_stack = "python"
    if tech_stack not in ("python", "react"):
        tech_stack = LAB_FIXED_TECH
    return LAB_FIXED_MODE, tech_stack, skill_ids


def classification_meta_payload(
    classification: AppClassification,
    *,
    mode: str,
    tech_stack: str,
    skill_ids: list[str],
) -> dict:
    """Serializable metadata stored on the App row (multi-replica safe)."""
    return {
        "classification_confidence": classification.confidence,
        "inferred_mode": mode,
        "inferred_tech_stack": tech_stack,
        "inferred_skill_ids": skill_ids,
        "reasoning": (classification.reasoning or "")[:500],
    }
