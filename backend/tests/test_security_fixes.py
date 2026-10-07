"""Regression tests for Codex security report remediations."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_validate_fetch_url_blocks_private_ip():
    from url_fetch_guard import validate_fetch_url

    ok, err = validate_fetch_url("http://127.0.0.1/")
    assert not ok
    assert "not permitted" in err.lower()


def test_validate_fetch_url_blocks_metadata_host():
    from url_fetch_guard import validate_fetch_url

    ok, err = validate_fetch_url("http://metadata.google.internal/computeMetadata/v1/")
    assert not ok


def _fake_db():
    class _Query:
        def filter(self, *args, **kwargs):
            return self

        def first(self):
            return None

    return SimpleNamespace(query=lambda *args, **kwargs: _Query())


def test_empty_email_does_not_grant_ownership():
    from api_agents import _is_owner_or_collaborator

    agent = SimpleNamespace(
        id="agent-1",
        owner_sub="sub-a",
        creator_email="",
    )
    assert not _is_owner_or_collaborator(agent, "sub-b", "", db=_fake_db())


def test_nonempty_creator_email_still_grants_owner():
    from api_agents import _is_owner_or_collaborator

    agent = SimpleNamespace(
        id="agent-1",
        owner_sub="sub-other",
        creator_email="owner@example.com",
    )
    assert _is_owner_or_collaborator(agent, "sub-x", "owner@example.com", db=None)


def test_filter_visible_app_files_hides_records_for_shared_reader():
    from api_apps import _filter_visible_app_files

    app = SimpleNamespace(owner_sub="owner-sub")
    files = [
        "index.html",
        "_records/notes/user-a.json",
        "server.py",
    ]
    visible = _filter_visible_app_files(files, app, "viewer-sub")
    assert "index.html" in visible
    assert "server.py" in visible
    assert "_records/notes/user-a.json" not in visible


def test_filter_visible_app_files_shows_records_for_owner():
    from api_apps import _filter_visible_app_files

    app = SimpleNamespace(owner_sub="owner-sub")
    files = ["_records/notes/user-a.json"]
    visible = _filter_visible_app_files(files, app, "owner-sub")
    assert visible == files


def test_lab_app_api_guard_blocks_non_apps_api():
    from lab_app_api_guard import lab_app_api_path_allowed, request_from_lab_app_sandbox

    assert request_from_lab_app_sandbox(None, "https://platform.example/apps/demo/")
    assert not lab_app_api_path_allowed("/api/agents")
    assert lab_app_api_path_allowed("/api/apps/abc/run")


def test_slack_registration_claim_blocks_other_owner():
    from access_models import SlackAppRegistrationClaim

    claim = SlackAppRegistrationClaim(slack_app_id="A01234567", owner_sub="sub-a")
    assert claim.owner_sub != "sub-b"


def test_validate_zip_archive_rejects_many_members():
    import zipfile
    from io import BytesIO

    from document_extract import MAX_ZIP_MEMBERS, validate_zip_archive

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for i in range(MAX_ZIP_MEMBERS + 1):
            zf.writestr(f"f{i}.txt", "x")
    ok, reason = validate_zip_archive(buf.getvalue())
    assert not ok
    assert "members" in reason


def test_lab_app_csp_restricts_connect_src():
    from lab_app_api_guard import LAB_APP_CONTENT_SECURITY_POLICY

    assert "connect-src 'self' /api/apps/" in LAB_APP_CONTENT_SECURITY_POLICY


def test_untrusted_same_origin_blocks_admin_without_platform_client():
    from lab_app_api_guard import untrusted_same_origin_api_call
    from platform_client_guard import platform_client_header_value

    assert untrusted_same_origin_api_call(
        path="/api/admin/setup/status",
        origin=None,
        referer=None,
        platform_client_header=None,
        sec_fetch_site="same-origin",
        sec_fetch_mode="cors",
    )
    assert not untrusted_same_origin_api_call(
        path="/api/admin/setup/status",
        origin=None,
        referer=None,
        platform_client_header=platform_client_header_value(),
        sec_fetch_site="same-origin",
        sec_fetch_mode="cors",
    )


def test_platform_client_header_roundtrip():
    from platform_client_guard import is_valid_platform_client, platform_client_header_value

    token = platform_client_header_value()
    assert is_valid_platform_client(token)
    assert not is_valid_platform_client("invalid")


def test_reconcile_auth_disables_mock_when_oidc_configured(monkeypatch):
    from auth_env_guard import reconcile_auth_environment

    monkeypatch.setenv("OIDC_ISSUER", "https://idp.example.com")
    monkeypatch.setenv("POLICY_AUD", "audience")
    monkeypatch.setenv("ENABLE_MOCK_AUTH", "true")
    reconcile_auth_environment()
    assert os.environ.get("ENABLE_MOCK_AUTH") == "false"


def test_mock_auth_not_written_by_personal_setup_without_allow(monkeypatch):
    from auth_env_guard import mock_auth_allowed_for_setup
    from platform_setup_store import build_env_from_draft

    monkeypatch.delenv("ALLOW_MOCK_AUTH", raising=False)
    monkeypatch.delenv("ENABLE_MOCK_AUTH", raising=False)
    monkeypatch.delenv("OIDC_ISSUER", raising=False)
    monkeypatch.delenv("POLICY_AUD", raising=False)

    draft = {
        "deployment_mode": "personal",
        "core": {"app_env": "development"},
        "identity": {},
        "models": {},
        "directory": {"provider": "claims"},
        "slack": {"mode": "skip"},
        "branding": {},
    }
    assert not mock_auth_allowed_for_setup(draft)
    env = build_env_from_draft(draft)
    assert env.get("ENABLE_MOCK_AUTH") == "false"


def test_validate_fetch_url_blocks_cgnat():
    from url_fetch_guard import validate_fetch_url

    ok, err = validate_fetch_url("http://100.64.1.1/")
    assert not ok
    assert "not permitted" in err.lower()


def test_agent_public_dict_hides_sensitive_fields():
    from agents import agent_to_dict, agent_to_public_dict

    agent = SimpleNamespace(
        id="agent-1",
        name="Test",
        description="desc",
        usecase_description=None,
        avatar_url=None,
        creator_email="owner@example.com",
        creator_department="eng",
        slack_app_id="A1",
        slack_bot_user_id="U1",
        slack_team_id="T1",
        xoxb_secret_ref="vault/xoxb",
        xapp_secret_ref="vault/xapp",
        signing_secret_ref="vault/signing",
        gateway_api_key_ref="vault/gw",
        creation_method="manual",
        manifest_access_verified=False,
        skill_ids='["secret-skill"]',
        soul_text="hidden instructions",
        channel_ids="[]",
        trigger_mode="mention",
        agent_type="slack",
        llm_model=None,
        artifacts_enabled=False,
        skill_routing_enabled=False,
        skill_index_text=None,
        skill_index_generated_at=None,
        skill_index_source_skill_ids=None,
        can_invoke="[]",
        can_be_invoked_by="[]",
        guardrails=["do not share"],
        bot_scopes=None,
        pending_scopes="[]",
        status="active",
        created_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        updated_at=SimpleNamespace(isoformat=lambda: "2026-01-01T00:00:00"),
        activated_at=None,
        paused_at=None,
        decommissioned_at=None,
        last_active_at=None,
        session_count=0,
        total_messages=0,
        scheduled_tasks=[],
        is_platform_agent=False,
    )
    public = agent_to_public_dict(agent)
    full = agent_to_dict(agent)
    assert "soul_text" not in public
    assert "signing_secret_ref" not in public
    assert public["has_signing_secret"] is True
    assert full["soul_text"] == "hidden instructions"


def test_artifact_gate_blocks_os_system():
    from artifact_gate import review

    report = review("import os\n\ndef run():\n    os.system('echo hi')\n", {"name": "demo_tool", "effect_class": "read", "entrypoint": "run"})
    assert not report.ok


def test_generated_file_secret_detection():
    from executor import _generated_file_contains_secret

    token = "xoxb-fake-token-value-1234567890"
    assert _generated_file_contains_secret(token.encode(), {token})


def test_secrets_service_token_rejects_agent_invocation_token(monkeypatch):
    import api_internal as internal

    secret = "test-internal-secret-for-f14"
    monkeypatch.setattr(internal, "_INTERNAL_SIGNING_SECRET", secret)
    service_token = internal.sign_service_token("slack-relay-dmz")
    internal._verify_secrets_service_token(service_token)
    agent_token = internal.sign_invocation_context("member@example.com", "agent-1")
    with pytest.raises(Exception):
        internal._verify_secrets_service_token(agent_token)


def test_parse_verdict_fails_closed_on_ambiguous_response():
    from api_skill_publish import REVIEW_STATUS_FAIL, _parse_verdict

    verdict, _reason = _parse_verdict('maybe pass but unclear')
    assert verdict == REVIEW_STATUS_FAIL


def test_cap_extracted_text_truncates_large_output(monkeypatch):
    import importlib

    monkeypatch.setenv("KB_MAX_EXTRACT_CHARS", "100")
    import document_extract

    importlib.reload(document_extract)
    capped = document_extract.cap_extracted_text("a" * 500)
    assert len(capped) < 200
    assert capped.endswith("[Text extraction truncated]")
