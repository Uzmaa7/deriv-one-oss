"""Tests for gateway-driven model catalog helpers."""

from __future__ import annotations

import os
import unittest


class ModelCatalogTests(unittest.TestCase):
    def test_platform_default_model_from_env(self) -> None:
        from model_catalog import platform_default_model

        prev = os.environ.get("MODEL")
        os.environ["MODEL"] = "my-gateway-model"
        try:
            # Re-import to pick up env at module load for alias constants
            import importlib
            import model_catalog

            importlib.reload(model_catalog)
            self.assertEqual(model_catalog.platform_default_model(), "my-gateway-model")
        finally:
            if prev is None:
                os.environ.pop("MODEL", None)
            else:
                os.environ["MODEL"] = prev
            import importlib
            import model_catalog

            importlib.reload(model_catalog)

    def test_platform_default_model_empty_when_unset(self) -> None:
        from model_catalog import platform_default_model

        prev = os.environ.get("MODEL")
        os.environ.pop("MODEL", None)
        try:
            import importlib
            import model_catalog

            importlib.reload(model_catalog)
            self.assertEqual(model_catalog.platform_default_model(), "")
        finally:
            if prev is not None:
                os.environ["MODEL"] = prev
            import importlib
            import model_catalog

            importlib.reload(model_catalog)

    def test_emergency_ui_models_use_env_not_hardcoded_vendor(self) -> None:
        import importlib
        import model_catalog

        prev_default = os.environ.get("WORKSPACE_CHAT_DEFAULT_MODEL")
        prev_model = os.environ.get("MODEL")
        os.environ["WORKSPACE_CHAT_DEFAULT_MODEL"] = "custom-workspace-model"
        os.environ.pop("MODEL", None)
        try:
            importlib.reload(model_catalog)
            models = model_catalog._emergency_ui_models()
            self.assertIn("custom-workspace-model", models)
            for slug in models:
                self.assertNotIn("claude", slug.lower())
                self.assertNotIn("gpt-4", slug.lower())
        finally:
            if prev_default is None:
                os.environ.pop("WORKSPACE_CHAT_DEFAULT_MODEL", None)
            else:
                os.environ["WORKSPACE_CHAT_DEFAULT_MODEL"] = prev_default
            if prev_model is not None:
                os.environ["MODEL"] = prev_model
            importlib.reload(model_catalog)

    def test_pick_workspace_chat_model_skips_empty_and_embeddings(self) -> None:
        from model_catalog import ModelCatalog, ModelCatalogEntry, pick_workspace_chat_model

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
        self.assertEqual(pick_workspace_chat_model(catalog, None), "openai/gpt-oss-20b")

    def test_catalog_filter_mode_defaults_to_all(self) -> None:
        import importlib
        import model_catalog

        prev = os.environ.get("MODEL_CATALOG_FILTER")
        os.environ.pop("MODEL_CATALOG_FILTER", None)
        try:
            importlib.reload(model_catalog)
            self.assertEqual(model_catalog.CATALOG_FILTER_MODE, "all")
        finally:
            if prev is not None:
                os.environ["MODEL_CATALOG_FILTER"] = prev
            importlib.reload(model_catalog)


if __name__ == "__main__":
    unittest.main()
