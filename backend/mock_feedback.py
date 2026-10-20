"""In-memory feedback samples for ENABLE_MOCK_AUTH local UI — never written to DB."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

_UTC = timezone.utc


def _utcnow() -> datetime:
    return datetime.now(_UTC).replace(tzinfo=None)


def _build_store() -> dict[str, dict[str, Any]]:
    now = _utcnow()
    samples = [
        {
            "id": "mock-feedback-001",
            "created_at": (now - timedelta(hours=0)).isoformat(),
            "created_by_email": "alex.morgan@example.com",
            "route": "/workspace",
            "element_context": {
                "label": "Message input",
                "selector": "textarea[placeholder='Ask anything…']",
            },
            "user_message": "Chat input loses focus when switching skills mid-conversation.",
            "category": "bug",
            "status": "open",
            "assignee": None,
            "priority": "high",
            "resolution_note": None,
            "triaged_at": None,
        },
        {
            "id": "mock-feedback-002",
            "created_at": (now - timedelta(hours=6)).isoformat(),
            "created_by_email": "priya.shah@example.com",
            "route": "/admin",
            "element_context": {
                "label": "Approve button",
                "selector": "button.approve-grant",
            },
            "user_message": "Add bulk approve for pending skill access requests.",
            "category": "feature",
            "status": "triaged",
            "assignee": "sejal.chopra@example.com",
            "priority": "medium",
            "resolution_note": None,
            "triaged_at": (now - timedelta(hours=5)).isoformat(),
        },
        {
            "id": "mock-feedback-003",
            "created_at": (now - timedelta(hours=12)).isoformat(),
            "created_by_email": "james.lee@example.com",
            "route": "/lab",
            "element_context": {
                "label": "Validation errors",
                "selector": ".validation-sheet",
            },
            "user_message": "Lab validation sheet errors are hard to scan — need clearer row highlighting.",
            "category": "ux",
            "status": "in_progress",
            "assignee": "platform-team@example.com",
            "priority": "urgent",
            "resolution_note": None,
            "triaged_at": (now - timedelta(hours=11)).isoformat(),
        },
        {
            "id": "mock-feedback-004",
            "created_at": (now - timedelta(hours=18)).isoformat(),
            "created_by_email": "nina.kowalski@example.com",
            "route": "/skills",
            "element_context": {"label": "Skill card", "selector": ".skill-card"},
            "user_message": "Skill cards could show last-updated timestamp.",
            "category": "other",
            "status": "done",
            "assignee": "sejal.chopra@example.com",
            "priority": "low",
            "resolution_note": "Shipped in v0.4 — timestamp shown on hover.",
            "triaged_at": (now - timedelta(hours=17)).isoformat(),
        },
        {
            "id": "mock-feedback-005",
            "created_at": (now - timedelta(hours=24)).isoformat(),
            "created_by_email": "tom.hughes@example.com",
            "route": "/agents",
            "element_context": {
                "label": "Canvas viewport",
                "selector": ".react-flow__viewport",
            },
            "user_message": "Agent canvas zoom resets on every node drag.",
            "category": "bug",
            "status": "wont_do",
            "assignee": "alex.morgan@example.com",
            "priority": "medium",
            "resolution_note": "Browser-specific; not reproducible on supported browsers.",
            "triaged_at": (now - timedelta(hours=23)).isoformat(),
        },
    ]
    return {row["id"]: row for row in samples}


_store: dict[str, dict[str, Any]] = _build_store()


def list_mock_feedback() -> list[dict[str, Any]]:
    return sorted(
        (deepcopy(r) for r in _store.values()),
        key=lambda r: r["created_at"],
        reverse=True,
    )


def patch_mock_feedback(feedback_id: str, patch: dict[str, Any]) -> dict[str, Any] | None:
    row = _store.get(feedback_id)
    if not row:
        return None
    if patch.get("status") is not None:
        row["status"] = patch["status"]
        if patch["status"] in ("triaged", "in_progress", "done") and not row.get("triaged_at"):
            row["triaged_at"] = _utcnow().isoformat()
    if patch.get("resolution_note") is not None:
        row["resolution_note"] = patch["resolution_note"]
    if patch.get("assignee") is not None:
        row["assignee"] = patch["assignee"]
    if patch.get("priority") is not None:
        row["priority"] = patch["priority"]
    return deepcopy(row)


def create_mock_feedback(payload: dict[str, Any]) -> dict[str, Any]:
    row = {
        "id": f"mock-feedback-{uuid4()}",
        "created_at": _utcnow().isoformat(),
        "created_by_email": payload["email"],
        "route": payload["route"],
        "element_context": payload["element_context"],
        "user_message": payload["user_message"],
        "category": payload["category"],
        "status": "open",
        "assignee": None,
        "priority": None,
        "resolution_note": None,
        "triaged_at": None,
    }
    _store[row["id"]] = row
    return deepcopy(row)
