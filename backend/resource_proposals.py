"""Shared helper for the in-chat propose/approve Skill|Automation|Lab flow.

Used by both dispatch paths - main.py's Claude-direct tool loop and
workspace_openai_chat.py's Auto-model tool loop - so proposal creation and the
reload-survival marker format never drift between the two.
"""
from __future__ import annotations

import json

from database import ResourceProposal, SessionLocal


def create_resource_proposal(
    *, chat_id: str, owner_sub: str, kind: str, name: str, content: dict,
) -> ResourceProposal:
    db = SessionLocal()
    try:
        row = ResourceProposal(
            chat_id=chat_id,
            owner_sub=owner_sub,
            kind=kind,
            name=(name or "Untitled")[:300],
            content=json.dumps(content),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def proposal_marker(proposal_id: str, kind: str, name: str) -> str:
    """Hidden marker appended to the assistant message so the ProposalCard
    survives page reloads (DB only stores raw message text) - mirrors the
    `<!-- GENFILE:{...} -->` convention already used for generated files.
    """
    payload = json.dumps({"i": proposal_id, "k": kind, "n": name})
    return f"\n<!-- PROPOSAL:{payload} -->"


def content_for_kind(kind: str, name: str, tool_input: dict) -> dict:
    """Build the kind-specific `content` JSON from raw tool-call input.

    Note the deliberate asymmetry with the frontend types (ProposalCard.tsx):
    SkillContent has no `name` field; AutomationContent and LabContent both
    duplicate `name` inside content. Keep this exact shape or the frontend
    render branches will silently show blank fields.
    """
    if kind == "skill":
        return {
            "skill_md": tool_input.get("skill_md", ""),
            "api_ref_url": tool_input.get("api_ref_url"),
        }
    if kind == "automation":
        return {
            "name": name,
            "instructions": tool_input.get("instructions", ""),
            "schedule": tool_input.get("schedule", ""),
            "is_active": tool_input.get("is_active", True),
            "skill_id": tool_input.get("skill_id"),
            "slack_channel": tool_input.get("slack_channel"),
        }
    return {
        "name": name,
        "description": tool_input.get("description", ""),
        "skill_ids": tool_input.get("skill_ids") or [],
    }
