"""Tests for first-run setup store and health snapshot."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class PlatformSetupStoreTests(unittest.TestCase):
    def test_generate_encryption_key_length(self) -> None:
        from platform_setup_store import generate_encryption_key

        key = generate_encryption_key()
        self.assertGreater(len(key), 20)

    def test_health_snapshot_includes_extended_checks(self) -> None:
        from platform_setup_store import health_snapshot

        snap = health_snapshot()
        for field in (
            "model_gateway_configured",
            "encryption_key_configured",
            "blob_store",
            "blob_configured",
            "sandbox_provider",
            "sandbox_configured",
            "slack_auto_create_configured",
            "directory_provider",
            "platform_env_exists",
        ):
            self.assertIn(field, snap)

    def test_complete_setup_writes_platform_env_without_echoing_secrets(self) -> None:
        from platform_setup_store import complete_setup

        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "platform.env"
            draft = {
                "deployment_mode": "personal",
                "branding": {
                    "app_name": "TestCo",
                    "app_short_name": "TestCo",
                    "app_logo_url": "/logo.svg",
                    "org_noun": "teammate",
                    "brand_color": "#000000",
                    "brand_color_hover": "#111111",
                    "brand_color_soft": "#eeeeee",
                },
                "core": {
                    "app_base_url": "http://localhost:3000",
                    "resource_prefix": "testco",
                    "app_env": "development",
                },
                "models": {
                    "gateway_url": "http://127.0.0.1:4000/v1",
                    "gateway_api_key": "sk-test-secret-value",
                    "encryption_key": "enc-test-secret-value",
                },
                "identity": {
                    "mock_user_email": "dev@localhost.local",
                    "mock_user_groups": "platform-admins",
                    "oidc_issuer": "",
                    "policy_aud": "",
                    "oidc_jwks_url": "",
                    "admin_groups": "platform-admins",
                    "member_groups": "platform-users",
                },
                "directory": {"provider": "claims", "file_content": ""},
                "slack": {
                    "mode": "skip",
                    "config_collaborator": "",
                    "config_access_token": "",
                    "config_refresh_token": "",
                    "ip_allowlist": "",
                },
            }
            with mock.patch("platform_setup_store._platform_env_path", return_value=env_path):
                with mock.patch("platform_setup_store.is_setup_complete", return_value=False):
                    with mock.patch("platform_setup_store.SessionLocal") as session_local:
                        session = session_local.return_value.__enter__.return_value
                        session.query.return_value.filter.return_value.first.return_value = None
                        result = complete_setup(draft)

            self.assertTrue(env_path.is_file())
            contents = env_path.read_text()
            self.assertIn("APP_NAME=TestCo", contents)
            self.assertIn("MODEL_GATEWAY_URL=", contents)
            # Values are stored in env file for local boot — ensure we did write them.
            self.assertIn("sk-test-secret-value", contents)
            # Return payload should not leak secrets to callers/logs by accident.
            payload = str(result)
            self.assertNotIn("sk-test-secret-value", payload)
            self.assertNotIn("enc-test-secret-value", payload)

    def test_local_gateway_without_api_key_writes_placeholder(self) -> None:
        from model_gateway import LOCAL_GATEWAY_KEY_PLACEHOLDER
        from platform_setup_store import build_env_from_draft

        draft = {
            "deployment_mode": "personal",
            "branding": {"app_name": "Local"},
            "core": {"app_base_url": "http://localhost:3000"},
            "models": {
                "gateway_url": "http://127.0.0.1:1234",
                "gateway_api_key": "",
                "encryption_key": "enc-test",
            },
            "identity": {},
            "directory": {"provider": "claims"},
            "slack": {"mode": "skip"},
        }
        env = build_env_from_draft(draft)
        self.assertEqual(env["MODEL_GATEWAY_URL"], "http://127.0.0.1:1234")
        self.assertEqual(env["MODEL_GATEWAY_API_KEY"], LOCAL_GATEWAY_KEY_PLACEHOLDER)

    def test_mock_auth_blocked_in_production_env(self) -> None:
        from cf_access import assert_mock_auth_not_in_production

        prev_auth = os.environ.get("ENABLE_MOCK_AUTH")
        prev_env = os.environ.get("APP_ENV")
        os.environ["ENABLE_MOCK_AUTH"] = "true"
        os.environ["APP_ENV"] = "production"
        try:
            with self.assertRaises(RuntimeError):
                assert_mock_auth_not_in_production()
        finally:
            if prev_auth is None:
                os.environ.pop("ENABLE_MOCK_AUTH", None)
            else:
                os.environ["ENABLE_MOCK_AUTH"] = prev_auth
            if prev_env is None:
                os.environ.pop("APP_ENV", None)
            else:
                os.environ["APP_ENV"] = prev_env


if __name__ == "__main__":
    unittest.main()
