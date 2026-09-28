"""Minimal smoke tests so forks catch import and config regressions early."""

from __future__ import annotations

import os
import unittest


class SmokeTests(unittest.TestCase):
    def test_app_config_branding(self) -> None:
        from app_config import APP_NAME, RESOURCE_PREFIX, http_user_agent

        self.assertTrue(APP_NAME)
        self.assertTrue(RESOURCE_PREFIX)
        self.assertIn(APP_NAME.replace(" ", ""), http_user_agent().replace(" ", ""))

    def test_platform_config_route(self) -> None:
        from api_platform import platform_config

        body = platform_config()
        self.assertIn("app_name", body)
        self.assertIn("slack_auto_create_enabled", body)
        self.assertIn("setup_complete", body)

    def test_setup_store_helpers(self) -> None:
        from platform_setup_store import generate_encryption_key, health_snapshot

        key = generate_encryption_key()
        self.assertGreater(len(key), 20)
        snap = health_snapshot()
        self.assertIn("model_gateway_configured", snap)

    def test_providers_import(self) -> None:
        from providers import get_blob_store, get_directory  # noqa: F401

    def test_oidc_jwks_resolution_explicit(self) -> None:
        from cf_access import resolve_jwks_uri

        prev = os.environ.get("OIDC_JWKS_URL")
        os.environ["OIDC_JWKS_URL"] = "https://example.com/jwks.json"
        try:
            # Clear module cache for resolved URI
            import cf_access

            cf_access._resolved_jwks_uri = None
            self.assertEqual(resolve_jwks_uri(), "https://example.com/jwks.json")
        finally:
            import cf_access

            cf_access._resolved_jwks_uri = None
            if prev is None:
                os.environ.pop("OIDC_JWKS_URL", None)
            else:
                os.environ["OIDC_JWKS_URL"] = prev

    def test_mock_auth_blocked_in_production(self) -> None:
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
