"""Tests for Lab app-scoped JSON record storage."""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def store():
    files: dict[str, str] = {}

    def read_file(app_id: str, rel: str) -> str:
        key = f"{app_id}:{rel}"
        if key not in files:
            raise HTTPException(status_code=404, detail="not found")
        return files[key]

    def write_file(app_id: str, rel: str, content: str) -> None:
        files[f"{app_id}:{rel}"] = content

    def delete_file(app_id: str, rel: str) -> None:
        files.pop(f"{app_id}:{rel}", None)

    def list_files(app_id: str) -> list[str]:
        prefix = f"{app_id}:"
        return sorted(
            k[len(prefix):] for k in files if k.startswith(prefix)
        )

    return files, read_file, write_file, delete_file, list_files


def _actor(sub: str, email: str):
    from lab_records import RecordActor

    return RecordActor(sub=sub, email=email)


def _viewer(sub: str, email: str, *, is_admin: bool = False):
    from lab_records import RecordViewer

    return RecordViewer(sub=sub, email=email, is_admin=is_admin)


class TestLabRecords:
    def test_append_and_get(self, store):
        from lab_records import append_record, get_record

        _, read_file, write_file, _, _ = store
        rec = append_record(
            "app-1",
            "submissions",
            {"name": "Ansh"},
            actor=_actor("sub-1", "ansh@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        assert rec["key"]
        assert rec["data"]["name"] == "Ansh"
        assert rec["created_by"]["email"] == "ansh@example.com"
        assert "sub" not in rec["created_by"]
        loaded = get_record(
            "app-1",
            "submissions",
            rec["key"],
            read_file=read_file,
            viewer=_viewer("sub-1", "ansh@example.com", is_admin=True),
        )
        assert loaded["data"]["name"] == "Ansh"

    def test_list_records(self, store):
        from lab_records import append_record, list_records

        _, read_file, write_file, _, list_files = store
        append_record(
            "app-1",
            "settings",
            {"n": 1},
            actor=_actor("sub-1", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        append_record(
            "app-1",
            "settings",
            {"n": 2},
            actor=_actor("sub-1", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        out = list_records(
            "app-1",
            "settings",
            read_file=read_file,
            list_files=list_files,
            limit=10,
            viewer=_viewer("sub-1", "a@example.com", is_admin=True),
        )
        assert out["count"] == 2
        assert len(out["items"]) == 2

    def test_upsert_and_delete(self, store):
        from lab_records import delete_record, get_record, upsert_record

        _, read_file, write_file, delete_file, _ = store
        upsert_record(
            "app-1",
            "settings",
            "default",
            {"theme": "dark"},
            actor=_actor("sub-1", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        rec = get_record(
            "app-1",
            "settings",
            "default",
            read_file=read_file,
            viewer=_viewer("sub-1", "a@example.com", is_admin=True),
        )
        assert rec["data"]["theme"] == "dark"
        delete_record("app-1", "settings", "default", delete_file=delete_file)
        with pytest.raises(HTTPException) as exc:
            get_record(
                "app-1",
                "settings",
                "default",
                read_file=read_file,
                viewer=_viewer("sub-1", "a@example.com", is_admin=True),
            )
        assert exc.value.status_code == 404

    def test_append_only_blocks_upsert(self, store):
        from lab_records import upsert_record

        _, read_file, write_file, _, _ = store
        with pytest.raises(HTTPException) as exc:
            upsert_record(
                "app-1",
                "submissions",
                "fixed-key",
                {"x": 1},
                actor=_actor("sub-1", "a@example.com"),
                read_file=read_file,
                write_file=write_file,
            )
        assert exc.value.status_code == 405

    def test_rejects_invalid_collection(self):
        from lab_records import validate_collection

        with pytest.raises(HTTPException):
            validate_collection("../etc")

    def test_doc_size_limit(self, store):
        from lab_records import append_record

        _, read_file, write_file, _, _ = store
        huge = {"blob": "x" * 70_000}
        with pytest.raises(HTTPException) as exc:
            append_record(
                "app-1",
                "items",
                huge,
                actor=_actor("sub-1", "a@example.com"),
                read_file=read_file,
                write_file=write_file,
            )
        assert exc.value.status_code == 413

    def test_strips_spoofed_identity_from_data(self, store):
        from lab_records import append_record

        _, read_file, write_file, _, _ = store
        rec = append_record(
            "app-1",
            "submissions",
            {"name": "Ansh", "created_by": {"email": "fake@example.com"}, "sub": "evil"},
            actor=_actor("sub-1", "real@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        assert "created_by" not in rec["data"]
        assert "sub" not in rec["data"]
        assert rec["created_by"]["email"] == "real@example.com"


class TestRecordScoping:
    def test_member_sees_only_own_submissions(self, store):
        from lab_records import append_record, list_records

        _, read_file, write_file, _, list_files = store
        append_record(
            "app-1",
            "submissions",
            {"n": 1},
            actor=_actor("sub-a", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        append_record(
            "app-1",
            "submissions",
            {"n": 2},
            actor=_actor("sub-b", "b@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        out = list_records(
            "app-1",
            "submissions",
            read_file=read_file,
            list_files=list_files,
            viewer=_viewer("sub-a", "a@example.com"),
        )
        assert out["count"] == 1
        assert out["items"][0]["data"]["n"] == 1

    def test_admin_sees_all_submissions(self, store):
        from lab_records import append_record, list_records

        _, read_file, write_file, _, list_files = store
        append_record(
            "app-1",
            "submissions",
            {"n": 1},
            actor=_actor("sub-a", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        append_record(
            "app-1",
            "submissions",
            {"n": 2},
            actor=_actor("sub-b", "b@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        out = list_records(
            "app-1",
            "submissions",
            read_file=read_file,
            list_files=list_files,
            viewer=_viewer("sub-admin", "admin@example.com", is_admin=True),
        )
        assert out["count"] == 2

    def test_legacy_records_hidden_from_members(self, store):
        from lab_records import list_records

        files, read_file, write_file, _, list_files = store
        write_file(
            "app-1",
            "_records/_collections.json",
            json.dumps({"submissions": {"append_only": True, "read_scope": "own", "created_at": "t"}}),
        )
        write_file(
            "app-1",
            "_records/submissions/legacy.json",
            json.dumps({
                "key": "legacy",
                "collection": "submissions",
                "created_at": "t",
                "updated_at": "t",
                "data": {"n": 99},
            }),
        )
        out = list_records(
            "app-1",
            "submissions",
            read_file=read_file,
            list_files=list_files,
            viewer=_viewer("sub-a", "a@example.com"),
        )
        assert out["count"] == 0

        admin_out = list_records(
            "app-1",
            "submissions",
            read_file=read_file,
            list_files=list_files,
            viewer=_viewer("sub-admin", "admin@example.com", is_admin=True),
        )
        assert admin_out["count"] == 1

    def test_cross_user_get_returns_404(self, store):
        from lab_records import append_record, get_record

        _, read_file, write_file, _, _ = store
        rec = append_record(
            "app-1",
            "submissions",
            {"n": 1},
            actor=_actor("sub-a", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        with pytest.raises(HTTPException) as exc:
            get_record(
                "app-1",
                "submissions",
                rec["key"],
                read_file=read_file,
                viewer=_viewer("sub-b", "b@example.com"),
            )
        assert exc.value.status_code == 404

    def test_scope_all_forbidden_for_member(self, store):
        from lab_records import append_record, list_records

        _, read_file, write_file, _, list_files = store
        append_record(
            "app-1",
            "submissions",
            {"n": 1},
            actor=_actor("sub-a", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        with pytest.raises(HTTPException) as exc:
            list_records(
                "app-1",
                "submissions",
                read_file=read_file,
                list_files=list_files,
                viewer=_viewer("sub-a", "a@example.com"),
                scope_all=True,
            )
        assert exc.value.status_code == 403


class TestRecordsApiGuards:
    def test_write_app_file_blocks_data_prefix(self):
        from api_apps import _write_app_file

        with pytest.raises(HTTPException) as exc:
            _write_app_file("app-1", "data/submissions/foo.json", "{}")
        assert exc.value.status_code == 400
        assert "Records API" in exc.value.detail

    def test_write_app_file_blocks_records_prefix_for_agents(self):
        from api_apps import _write_app_file

        with pytest.raises(HTTPException) as exc:
            _write_app_file("app-1", "_records/submissions/foo.json", "{}")
        assert exc.value.status_code == 400
        assert "Records API" in exc.value.detail

    def test_records_helpers_allow_reserved_prefix(self):
        from api_apps import _records_read_file, _records_write_file

        _records_write_file("app-1", "_records/submissions/foo.json", '{"ok":true}')
        assert _records_read_file("app-1", "_records/submissions/foo.json") == '{"ok":true}'

    def test_app_role_admin_via_admin_users(self):
        from api_apps import _app_role

        app = SimpleNamespace(
            owner_sub="owner-sub",
            visibility="shared",
            allowed_users='["member@example.com"]',
            admin_users='["admin@example.com"]',
        )
        assert _app_role(app, "other-sub", "admin@example.com") == "admin"
        assert _app_role(app, "other-sub", "member@example.com") == "member"
        assert _app_role(app, "other-sub", "stranger@example.com") is None


class TestSharingValidation:
    def test_admin_users_must_be_subset_of_allowed_users(self):
        from api_apps import AppSharingRequest, update_sharing

        body = AppSharingRequest(
            visibility="shared",
            allowed_users=["member@example.com"],
            admin_users=["outsider@example.com"],
        )
        with pytest.raises(HTTPException) as exc:
            update_sharing("app-1", body, owner_sub="owner-sub")
        assert exc.value.status_code == 422


class TestRecordsSandboxBridge:
    def test_extract_record_ops_strips_markers(self):
        from lab_records_sandbox import extract_record_ops

        op = {"op": "append", "collection": "submissions", "data": {"x": 1}}
        payload = base64.b64encode(json.dumps(op).encode()).decode()
        stdout = f'{{"ok":true}}\n<|RECORD|>{payload}<|ENDRECORD|>'
        cleaned, ops = extract_record_ops(stdout)
        assert cleaned == '{"ok":true}'
        assert len(ops) == 1
        assert ops[0]["collection"] == "submissions"

    def test_snapshot_scoped_to_viewer(self, store):
        from lab_records import append_record
        from lab_records_sandbox import build_records_snapshot

        _, read_file, write_file, _, list_files = store
        append_record(
            "app-1",
            "submissions",
            {"n": 1},
            actor=_actor("sub-a", "a@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        append_record(
            "app-1",
            "submissions",
            {"n": 2},
            actor=_actor("sub-b", "b@example.com"),
            read_file=read_file,
            write_file=write_file,
        )
        snapshot = build_records_snapshot(
            "app-1",
            read_file=read_file,
            list_files=list_files,
            viewer=_viewer("sub-a", "a@example.com"),
        )
        assert len(snapshot["submissions"]) == 1
        only = next(iter(snapshot["submissions"].values()))
        assert only["data"]["n"] == 1
