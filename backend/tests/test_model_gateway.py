"""Tests for local / keyless model gateway helpers."""

from __future__ import annotations

import unittest
from unittest import mock


class ModelGatewayTests(unittest.TestCase):
    def test_gateway_auth_headers_empty_for_local_placeholder(self) -> None:
        from model_gateway import LOCAL_GATEWAY_KEY_PLACEHOLDER, gateway_auth_headers

        self.assertEqual(gateway_auth_headers(""), {})
        self.assertEqual(gateway_auth_headers(None), {})
        self.assertEqual(gateway_auth_headers(LOCAL_GATEWAY_KEY_PLACEHOLDER), {})
        self.assertEqual(
            gateway_auth_headers("sk-real"),
            {"Authorization": "Bearer sk-real"},
        )

    def test_resolved_gateway_api_key_uses_placeholder_when_url_set(self) -> None:
        from model_gateway import LOCAL_GATEWAY_KEY_PLACEHOLDER, resolved_gateway_api_key

        self.assertIsNone(resolved_gateway_api_key("", ""))
        self.assertEqual(
            resolved_gateway_api_key("", "http://127.0.0.1:1234"),
            LOCAL_GATEWAY_KEY_PLACEHOLDER,
        )
        self.assertEqual(
            resolved_gateway_api_key("sk-live", "https://gateway.example.com"),
            "sk-live",
        )

    def test_is_likely_local_gateway(self) -> None:
        from model_gateway import is_likely_local_gateway

        self.assertTrue(is_likely_local_gateway("http://127.0.0.1:1234"))
        self.assertTrue(is_likely_local_gateway("http://localhost:11434"))
        self.assertFalse(is_likely_local_gateway("https://api.openai.com"))

    def test_probe_gateway_without_key(self) -> None:
        from model_gateway import probe_gateway

        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "data": [{"id": "local-model"}, {"id": "other-model"}],
        }
        mock_client = mock.Mock()
        mock_client.__enter__ = mock.Mock(return_value=mock_client)
        mock_client.__exit__ = mock.Mock(return_value=False)
        mock_client.get.return_value = mock_resp

        with mock.patch("model_gateway.httpx.Client", return_value=mock_client):
            ok, msg, count = probe_gateway("http://127.0.0.1:1234", "")

        self.assertTrue(ok)
        self.assertEqual(count, 2)
        self.assertIn("no API key", msg)

    def test_default_gateway_chat_model_from_live_catalog(self) -> None:
        import os

        from model_gateway import default_gateway_chat_model

        prev_url = os.environ.get("MODEL_GATEWAY_URL")
        prev_model = os.environ.get("MODEL")
        os.environ["MODEL_GATEWAY_URL"] = "http://127.0.0.1:1234"
        os.environ.pop("MODEL", None)
        try:
            model = default_gateway_chat_model()
            self.assertTrue(model)
            self.assertNotIn("embed", model.lower())
        finally:
            if prev_url is None:
                os.environ.pop("MODEL_GATEWAY_URL", None)
            else:
                os.environ["MODEL_GATEWAY_URL"] = prev_url
            if prev_model is not None:
                os.environ["MODEL"] = prev_model

    def test_gateway_text_completion_openai_transport(self) -> None:
        from model_gateway import gateway_text_completion

        mock_usage = object()
        mock_message = mock.Mock(content='{"verdict":"pass","reason":"ok","secret_keys":[]}')
        mock_choice = mock.Mock(message=mock_message)
        mock_response = mock.Mock(choices=[mock_choice], usage=mock_usage)
        mock_client = mock.Mock()
        mock_client.chat.completions.create.return_value = mock_response

        with mock.patch("openai.OpenAI", return_value=mock_client):
            text, usage = gateway_text_completion(
                api_key="local-no-key",
                base_url_raw="http://127.0.0.1:1234",
                model="openai/gpt-oss-20b",
                system="You are a reviewer.",
                user_message="review this",
                max_tokens=128,
            )

        self.assertIn("pass", text)
        self.assertIs(usage, mock_usage)

    def test_compact_for_local_gateway(self) -> None:
        from model_gateway import compact_for_local_gateway, strip_local_workspace_boilerplate

        stripped = strip_local_workspace_boilerplate("skill text\n\n---\n\n## Creating Skills, Automations, and Labs\nextra")
        self.assertNotIn("Creating Skills", stripped)

        system, messages = compact_for_local_gateway(
            "x" * 20_000,
            [{"role": "user", "content": f"msg {i}"} for i in range(20)],
            max_system_chars=1000,
            max_turns=4,
        )
        self.assertLessEqual(len(system), 1100)
        self.assertEqual(len(messages), 4)

    def test_local_gateway_max_output_tokens_respects_context(self) -> None:
        from model_gateway import local_gateway_max_output_tokens

        big_system = "word " * 3000
        messages = [{"role": "user", "content": "hello"}]
        tokens = local_gateway_max_output_tokens(
            big_system,
            messages,
            context_limit=4096,
        )
        self.assertGreaterEqual(tokens, 128)
        self.assertLessEqual(tokens, 768)

    def test_apply_gateway_env_defaults_for_local_url(self) -> None:
        import os

        from model_gateway import LOCAL_GATEWAY_KEY_PLACEHOLDER, apply_gateway_env_defaults

        prev_url = os.environ.get("MODEL_GATEWAY_URL")
        prev_key = os.environ.get("MODEL_GATEWAY_API_KEY")
        os.environ["MODEL_GATEWAY_URL"] = "http://127.0.0.1:1234"
        os.environ.pop("MODEL_GATEWAY_API_KEY", None)
        try:
            apply_gateway_env_defaults()
            self.assertEqual(os.environ.get("MODEL_GATEWAY_API_KEY"), LOCAL_GATEWAY_KEY_PLACEHOLDER)
        finally:
            if prev_url is None:
                os.environ.pop("MODEL_GATEWAY_URL", None)
            else:
                os.environ["MODEL_GATEWAY_URL"] = prev_url
            if prev_key is None:
                os.environ.pop("MODEL_GATEWAY_API_KEY", None)
            else:
                os.environ["MODEL_GATEWAY_API_KEY"] = prev_key


if __name__ == "__main__":
    unittest.main()
