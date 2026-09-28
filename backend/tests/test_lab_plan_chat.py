"""Tests for Lab plan-mode chat prompts."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_plan_system_prompt_includes_platform_defaults():
    from lab_plan_chat import _plan_system_prompt

    app = SimpleNamespace(
        id="app-test-1",
        name="Interact",
        description="Customer search app",
        tech_stack="react",
        skill_ids="[]",
        classification_meta=None,
    )
    prompt = _plan_system_prompt(app)
    assert "GET /api/apps/{app_id}/me" in prompt
    assert "Share dialog" in prompt or "Share panel" in prompt
    assert "FORBIDDEN open_questions" in prompt
    assert "NEVER read Cf-Access" in prompt


def test_plan_mode_rules_forbid_login_and_admin_infra_questions():
    from lab_prompts import PLAN_MODE_PLATFORM_RULES

    assert "NEVER ask" in PLAN_MODE_PLATFORM_RULES
    assert "login system" in PLAN_MODE_PLATFORM_RULES.lower() or "login page" in PLAN_MODE_PLATFORM_RULES
    assert "config file" in PLAN_MODE_PLATFORM_RULES.lower()
