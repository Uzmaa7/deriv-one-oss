"""Shared automation report prompt defaults and per-task resolution."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from agents import AgentScheduledTask

DEFAULT_AUTOMATION_REPORT_PROMPT = (
    "You are a data analyst. You have been given real query results. "
    "Write a concise, well-formatted markdown report based on these actual numbers. "
    "Use tables, headings, and bullet points. Be direct — no filler text."
)

MAX_AUTOMATION_REPORT_PROMPT = 4000


def resolve_automation_report_prompt(task: "AgentScheduledTask") -> Optional[str]:
    """Return the report system prompt when posting is enabled for this task, else None."""
    if not getattr(task, "report_enabled", False):
        return None
    custom = (getattr(task, "report_prompt", None) or "").strip()
    return custom or DEFAULT_AUTOMATION_REPORT_PROMPT
