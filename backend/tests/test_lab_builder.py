"""Tests for Lab builder patch tool and scaffold seeding."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestApplyPatch:
    def test_requires_read_before_write(self):
        from lab_builder import apply_patch_file

        with patch("api_apps._read_app_file", return_value="hello world"):
            with patch("api_apps._write_app_file") as mock_write:
                result = apply_patch_file(
                    "app-1",
                    "app.js",
                    "hello",
                    "hi",
                    files_read=set(),
                    existing_files={"app.js"},
                )
        assert "read_file" in result
        mock_write.assert_not_called()

    def test_patches_when_read(self):
        from lab_builder import apply_patch_file

        with patch("api_apps._read_app_file", return_value="hello world"):
            with patch("api_apps._write_app_file") as mock_write:
                result = apply_patch_file(
                    "app-1",
                    "app.js",
                    "world",
                    "there",
                    files_read={"app.js"},
                    existing_files={"app.js"},
                )
        assert result.startswith("Patched:")
        mock_write.assert_called_once()


class TestSeedScaffold:
    def test_seeds_vanilla_files(self):
        from lab_scaffold import seed_scaffold

        written: dict[str, str] = {}

        def capture(rel: str, content: str) -> None:
            written[rel] = content

        paths = seed_scaffold("uuid-123", "python", "My App", capture)
        assert "index.html" in paths
        assert "server.py" in paths
        assert "uuid-123" in written["app.js"]

    def test_seeds_react_under_src_prefix(self):
        from lab_scaffold import seed_scaffold

        written: dict[str, str] = {}

        def capture(rel: str, content: str) -> None:
            written[rel] = content

        paths = seed_scaffold("uuid-456", "react", "React App", capture)
        assert any(p.startswith("__src__/") for p in paths)
        assert "uuid-456" in written.get("__src__/src/lib/api.ts", "")

    def test_normalize_run_function_name(self):
        from api_apps import _normalize_run_function_name

        assert _normalize_run_function_name("get_kpis") == "get_kpis"
        assert _normalize_run_function_name("getKpis") == "get_kpis"
        assert _normalize_run_function_name("getRecentItems") == "get_recent_items"

    def test_parse_sandbox_json_stdout(self):
        from api_apps import _parse_sandbox_json_stdout

        parsed, err = _parse_sandbox_json_stdout('{"kpis": {"a": 1}}')
        assert parsed == {"kpis": {"a": 1}}
        assert err is None

        parsed, err = _parse_sandbox_json_stdout('debug line\n{"items": []}')
        assert parsed == {"items": []}
        assert err is None

        parsed, err = _parse_sandbox_json_stdout("not json")
        assert parsed is None
        assert err == "non-JSON stdout"

    def test_write_fn_wiring_matches_write_app_file(self):
        """create_app must pass a 2-arg callback; _write_app_file needs app_id."""
        from lab_scaffold import seed_scaffold

        app_id = "uuid-789"
        with patch("api_apps._write_app_file") as mock_write:
            seed_scaffold(
                app_id,
                "python",
                "Wired App",
                lambda rel, content: mock_write(app_id, rel, content),
            )
        assert mock_write.call_count >= 3
        for call in mock_write.call_args_list:
            assert call.args[0] == app_id
            assert call.args[1]
            assert isinstance(call.args[2], str)


class TestLabEdits:
    def test_new_file_write_summary(self):
        from lab_edits import build_file_edit_summary

        edit = build_file_edit_summary(
            "server.py",
            kind="write",
            old_text="",
            new_text="def hello():\n    return 1\n",
        )
        assert edit["path"] == "server.py"
        assert edit["kind"] == "write"
        assert edit["additions"] >= 2
        assert edit["deletions"] == 0
        assert any(line["type"] == "add" for line in edit["preview"])

    def test_patch_summary_counts(self):
        from lab_edits import build_file_edit_summary

        edit = build_file_edit_summary(
            "app.js",
            kind="patch",
            old_text="const x = 1;\nconst y = 2;\n",
            new_text="const x = 10;\nconst y = 2;\n",
        )
        assert edit["additions"] == 1
        assert edit["deletions"] == 1
        assert len(edit["preview"]) == 2


class TestPrepareChatHistoryForLlm:
    def test_coalesces_consecutive_user_messages(self):
        from api_apps import _prepare_chat_history_for_llm

        out = _prepare_chat_history_for_llm([
            {"role": "assistant", "content": "Done."},
            {"role": "user", "content": "add emergency contact step"},
            {"role": "user", "content": "try again"},
        ])
        assert out[-1]["role"] == "user"
        assert "add emergency contact step" in out[-1]["content"]
        assert "try again" in out[-1]["content"]
        assert not any(
            m["role"] == "user" and m["content"] == "try again"
            for m in out
        )

    def test_strips_file_edits_metadata(self):
        from api_apps import _prepare_chat_history_for_llm

        out = _prepare_chat_history_for_llm([
            {
                "role": "assistant",
                "content": "Updated files.",
                "file_edits": [{"path": "app.js", "additions": 1, "deletions": 0}],
            },
            {"role": "user", "content": "next change"},
        ])
        assert out == [
            {"role": "user", "content": "(continued)"},
            {"role": "assistant", "content": "Updated files."},
            {"role": "user", "content": "next change"},
        ]

    def test_coalesces_consecutive_assistant_messages(self):
        from api_apps import _coalesce_saved_history, _prepare_chat_history_for_llm

        raw = [
            {"role": "user", "content": "build it"},
            {"role": "assistant", "content": "Step one done."},
            {"role": "assistant", "content": "Step two done."},
            {"role": "assistant", "content": "Now let's rebuild."},
            {"role": "assistant", "content": "Build succeeded."},
        ]
        saved = _coalesce_saved_history(raw)
        assert len(saved) == 2
        assert "Step one done." in saved[1]["content"]
        assert "Build succeeded." in saved[1]["content"]

        llm = _prepare_chat_history_for_llm(raw)
        roles = [m["role"] for m in llm]
        assert roles.count("assistant") == 1
        assert "file_edits" not in str(llm)

    def test_onboarding_style_history_for_llm(self):
        """Multi-turn feature builds leave consecutive assistant rows in DB."""
        from api_apps import _prepare_chat_history_for_llm

        history = [
            {"role": "user", "content": "CRITICAL ROUTING BUG: fix basename"},
            {"role": "assistant", "content": "Sidebar already uses navigate()."},
            {"role": "assistant", "content": "All pages already use useNavigate."},
            {"role": "assistant", "content": "Checking vite.config.ts."},
            {"role": "assistant", "content": "Fixed the routing bug."},
            {"role": "user", "content": "the sidebar doesn't extend to the bottom"},
            {"role": "assistant", "content": "Fixed: sticky top-0 h-screen."},
            {"role": "user", "content": "add an emergy contact details page as a new step"},
        ]
        llm = _prepare_chat_history_for_llm(history)
        for i in range(1, len(llm)):
            assert llm[i]["role"] != llm[i - 1]["role"], f"adjacent same role at {i}: {llm}"
        assert llm[-1]["content"] == "add an emergy contact details page as a new step"


class TestBlocksFromTimeline:
    def test_interleaves_text_and_file_edits(self):
        from api_apps import _blocks_from_timeline

        blocks = _blocks_from_timeline([
            {"event": "text", "delta": "First I'll update context."},
            {"event": "file_edit", "path": "ctx.jsx", "kind": "patch", "additions": 2, "deletions": 1, "preview": []},
            {"event": "text", "delta": "Now updating the router."},
            {"event": "file_edit", "path": "router.jsx", "kind": "write", "additions": 10, "deletions": 0, "preview": []},
            {"event": "text", "delta": "Build succeeded."},
        ])
        assert [b["type"] for b in blocks] == [
            "text", "file_edit", "text", "file_edit", "text",
        ]
        assert blocks[0]["content"] == "First I'll update context."
        assert blocks[2]["content"] == "Now updating the router."
        assert blocks[4]["content"] == "Build succeeded."

    def test_verify_fail_becomes_warning_block(self):
        from api_apps import _blocks_from_timeline

        blocks = _blocks_from_timeline([
            {"event": "verify_fail", "errors": ["main.tsx must use basename"]},
            {"event": "text", "delta": "Fixed routing."},
        ])
        assert blocks[0]["type"] == "warning"
        assert "basename" in blocks[0]["content"]
        assert blocks[1]["type"] == "text"
