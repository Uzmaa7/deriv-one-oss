"""Regression tests for Codex security report remediations."""

from __future__ import annotations

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


def test_cap_extracted_text_truncates_large_output(monkeypatch):
    import importlib

    monkeypatch.setenv("KB_MAX_EXTRACT_CHARS", "100")
    import document_extract

    importlib.reload(document_extract)
    capped = document_extract.cap_extracted_text("a" * 500)
    assert len(capped) < 200
    assert capped.endswith("[Text extraction truncated]")
