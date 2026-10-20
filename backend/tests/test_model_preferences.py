"""Tests for workspace model allowlists."""

from __future__ import annotations

import os
import unittest


class ModelPreferencesTests(unittest.TestCase):
    def test_filter_catalog_hides_embeddings(self) -> None:
        from model_catalog import ModelCatalog, ModelCatalogEntry
        from model_preferences import filter_catalog

        catalog = ModelCatalog(
            entries=[
                ModelCatalogEntry(id="text-embedding-nomic-embed-text-v1.5"),
                ModelCatalogEntry(id="openai/gpt-oss-20b"),
            ],
            source="gateway",
            cached_at="now",
            default_model="",
            all_entries=[
                ModelCatalogEntry(id="text-embedding-nomic-embed-text-v1.5"),
                ModelCatalogEntry(id="openai/gpt-oss-20b"),
            ],
        )
        filtered = filter_catalog(catalog)
        self.assertEqual([e.id for e in filtered.entries], ["openai/gpt-oss-20b"])

    def test_user_allowlist_restricts_picker(self) -> None:
        from model_catalog import ModelCatalog, ModelCatalogEntry
        from model_preferences import model_preferences_payload

        catalog = ModelCatalog(
            entries=[
                ModelCatalogEntry(id="model-a"),
                ModelCatalogEntry(id="model-b"),
            ],
            source="gateway",
            cached_at="now",
            default_model="model-a",
            all_entries=[
                ModelCatalogEntry(id="model-a"),
                ModelCatalogEntry(id="model-b"),
            ],
        )

        class Profile:
            enabled_models_json = '["model-b"]'
            preferred_model = "model-b"

        payload = model_preferences_payload(catalog, profile=Profile())
        self.assertEqual(payload["enabled_models"], ["model-b"])
        self.assertEqual(payload["picker"]["models"], ["model-b"])
        self.assertEqual(payload["picker"]["default"], "model-b")

    def test_platform_allowlist_from_env(self) -> None:
        from model_preferences import platform_enabled_models

        prev = os.environ.get("WORKSPACE_ENABLED_MODELS")
        os.environ["WORKSPACE_ENABLED_MODELS"] = "alpha,beta"
        try:
            allowed = platform_enabled_models()
            self.assertEqual(allowed, frozenset({"alpha", "beta"}))
        finally:
            if prev is None:
                os.environ.pop("WORKSPACE_ENABLED_MODELS", None)
            else:
                os.environ["WORKSPACE_ENABLED_MODELS"] = prev


if __name__ == "__main__":
    unittest.main()
