"""Regression tests for automation skill ACL enforcement (F-02)."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def _mock_accessible_only_allowed(user_sub: str, user_email):
    return [], {}, frozenset({"allowed-skill"})


def test_ensure_skill_granted_allows_accessible_skill():
    from automation_skill_access import ensure_skill_granted

    with patch(
        "skill_router.resolve_user_skill_access",
        side_effect=_mock_accessible_only_allowed,
    ):
        ensure_skill_granted("member-sub", "member@example.com", "allowed-skill")


def test_ensure_skill_granted_blocks_restricted_skill():
    from automation_skill_access import ensure_skill_granted

    with patch(
        "skill_router.resolve_user_skill_access",
        side_effect=_mock_accessible_only_allowed,
    ):
        with pytest.raises(HTTPException) as exc:
            ensure_skill_granted(
                "member-sub",
                "member@example.com",
                "live-canary-restricted",
            )
    assert exc.value.status_code == 403
    assert "live-canary-restricted" in exc.value.detail


def test_ensure_skill_granted_allows_missing_skill_id():
    from automation_skill_access import ensure_skill_granted

    ensure_skill_granted("member-sub", "member@example.com", None)


def test_create_automation_rejects_restricted_skill_id():
    from main import AutomationCreate, create_automation

    body = AutomationCreate(
        name="Canary automation",
        instructions="print secret",
        skill_id="live-canary-restricted",
        schedule="0 9 * * *",
        is_active=False,
    )
    request = SimpleNamespace(headers={})

    with patch(
        "skill_router.resolve_user_skill_access",
        side_effect=_mock_accessible_only_allowed,
    ):
        with patch(
            "main.build_auth_context",
            return_value=SimpleNamespace(email="member@example.com"),
        ):
            with pytest.raises(HTTPException) as exc:
                create_automation(body=body, request=request, owner_sub="member-sub")
    assert exc.value.status_code == 403
    assert "live-canary-restricted" in exc.value.detail


def test_run_automation_job_skips_secret_load_when_skill_denied():
    from database import Automation, SessionLocal, init_db
    from main import _run_automation_job

    init_db()
    db = SessionLocal()
    try:
        auto = Automation(
            name="Denied skill auto",
            instructions="noop",
            skill_id="live-canary-restricted",
            schedule="0 9 * * *",
            is_active=True,
            owner_sub="member-sub",
        )
        db.add(auto)
        db.commit()
        db.refresh(auto)
        auto_id = auto.id
    finally:
        db.close()

    with patch("automation_skill_access.user_can_access_skill", return_value=False):
        with patch("main._load_skill_secrets") as load_secrets:
            _run_automation_job(auto_id, manual=True)
            load_secrets.assert_not_called()

    db = SessionLocal()
    try:
        from database import AutomationRun

        runs = (
            db.query(AutomationRun)
            .filter(AutomationRun.automation_id == auto_id)
            .all()
        )
        assert len(runs) == 1
        assert runs[0].status == "error"
        assert runs[0].error == "Skill access denied"
    finally:
        db.close()
        db = SessionLocal()
        try:
            row = db.get(Automation, auto_id)
            if row:
                db.delete(row)
                db.commit()
        finally:
            db.close()
