"""Sandbox bridge for Lab Records API — platform_records module + stdout markers."""

from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any, Callable, Optional

from fastapi import HTTPException

from lab_records import (
    RecordActor,
    RecordViewer,
    get_read_scope,
    get_record,
    list_collection_keys,
    load_manifest,
    sanitize_record_for_response,
)

logger = logging.getLogger(__name__)

_RECORD_MARKER_RE = re.compile(
    r"<\|RECORD\|>([A-Za-z0-9+/=\n\r]+)<\|ENDRECORD\|>",
    re.DOTALL,
)

_MAX_SNAPSHOT_KEYS_PER_COLLECTION = 50
_MAX_SNAPSHOT_COLLECTIONS = 10

# Injected as platform_records.py inside the /run sandbox.
PLATFORM_RECORDS_MODULE = '''\
"""Platform record storage (injected by Lab /run)."""
from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional

_SNAPSHOT: dict[str, dict[str, dict]] = {}


def _load_snapshot() -> None:
    global _SNAPSHOT
    raw = os.environ.get("LAB_RECORDS_SNAPSHOT", "")
    if not raw:
        _SNAPSHOT = {}
        return
    try:
        parsed = json.loads(base64.b64decode(raw.encode()).decode("utf-8"))
        _SNAPSHOT = parsed if isinstance(parsed, dict) else {}
    except Exception:
        _SNAPSHOT = {}


def _emit_op(op: dict) -> None:
    payload = base64.b64encode(json.dumps(op, separators=(",", ":")).encode()).decode()
    print(f"<|RECORD|>{payload}<|ENDRECORD|>")


def save_record(collection: str, data: dict, key: Optional[str] = None) -> dict:
    """Append or upsert a JSON document (persisted after /run completes)."""
    if not isinstance(data, dict):
        raise TypeError("save_record data must be a dict")
    op: dict[str, Any] = {"op": "append", "collection": str(collection), "data": data}
    if key is not None:
        op["key"] = str(key)
    _emit_op(op)
    return {"status": "queued", "collection": collection, "key": key}


def list_records(collection: str, limit: int = 50) -> dict:
    """List records from the snapshot loaded at the start of this /run call."""
    items = list(_SNAPSHOT.get(collection, {}).values())
    lim = max(1, min(int(limit), 100))
    return {"collection": collection, "count": min(len(items), lim), "items": items[:lim]}


def get_record(collection: str, key: str) -> dict:
    """Fetch one record from the snapshot loaded at the start of this /run call."""
    rec = _SNAPSHOT.get(collection, {}).get(key)
    if not rec:
        raise KeyError(f"Record not found: {collection}/{key}")
    return rec


_load_snapshot()
'''


def build_records_snapshot(
    app_id: str,
    *,
    read_file: Callable[[str, str], str],
    list_files: Callable[[str], list[str]],
    viewer: RecordViewer,
) -> dict[str, dict[str, dict]]:
    """Load a bounded snapshot of existing records for sandbox reads."""
    snapshot: dict[str, dict[str, dict]] = {}
    try:
        manifest = load_manifest(app_id, read_file)
    except HTTPException:
        manifest = {}

    collections = sorted(set(manifest.keys()))
    for rel in list_files(app_id):
        norm = rel.replace("\\", "/")
        if norm.startswith("_records/") and norm.endswith(".json"):
            parts = norm.split("/")
            if len(parts) >= 3 and parts[1] and not parts[1].startswith("_"):
                collections.append(parts[1])
    seen: set[str] = set()
    for coll in collections:
        if coll in seen or coll.startswith("_"):
            continue
        seen.add(coll)
        if len(snapshot) >= _MAX_SNAPSHOT_COLLECTIONS:
            break
        read_scope = get_read_scope(coll, manifest)
        keys = list_collection_keys(app_id, coll, list_files)[-_MAX_SNAPSHOT_KEYS_PER_COLLECTION:]
        bucket: dict[str, dict] = {}
        for key in keys:
            try:
                record = get_record(
                    app_id,
                    coll,
                    key,
                    read_file=read_file,
                    viewer=viewer,
                    manifest=manifest,
                )
            except HTTPException:
                continue
            bucket[key] = sanitize_record_for_response(record)
        if bucket:
            snapshot[coll] = bucket
    return snapshot


def snapshot_env_value(snapshot: dict[str, dict[str, dict]]) -> str:
    raw = json.dumps(snapshot, separators=(",", ":"), ensure_ascii=False)
    return base64.b64encode(raw.encode("utf-8")).decode("ascii")


def extract_record_ops(stdout: str) -> tuple[str, list[dict]]:
    """Strip record markers from stdout and return decoded operations."""
    ops: list[dict] = []

    def _decode(match: re.Match) -> str:
        b64data = match.group(1).strip().replace("\n", "").replace("\r", "")
        try:
            parsed = json.loads(base64.b64decode(b64data).decode("utf-8"))
            if isinstance(parsed, dict):
                ops.append(parsed)
        except Exception:
            logger.warning("Failed to decode sandbox record op marker")
        return ""

    cleaned = _RECORD_MARKER_RE.sub(_decode, stdout)
    return cleaned.strip(), ops


def apply_sandbox_record_ops(
    app_id: str,
    ops: list[dict],
    *,
    owner_sub: str,
    email: str,
    app_owner_sub: str,
    app_visibility: str = "private",
    app_allowed_users: Optional[list[str]] = None,
    app_admin_users: Optional[list[str]] = None,
    read_file: Callable[[str, str], str],
    write_file: Callable[[str, str], str],
) -> list[dict]:
    """Apply queued record writes from a completed /run sandbox execution."""
    from lab_records import append_record, is_append_only, load_manifest, upsert_record

    if not ops:
        return []

    is_owner = owner_sub == app_owner_sub
    allowed = [e.lower() for e in (app_allowed_users or [])]
    actor = RecordActor(sub=owner_sub, email=email)

    def _can_post(collection: str) -> None:
        if is_owner:
            return
        manifest = load_manifest(app_id, read_file)
        if not is_append_only(collection, manifest):
            raise HTTPException(status_code=403, detail="Only the app owner can write to this collection.")
        if app_visibility == "workspace":
            return
        if app_visibility == "shared" and email.lower() in allowed:
            return
        raise HTTPException(status_code=403, detail="Not allowed to write records for this app.")

    applied: list[dict] = []
    for op in ops:
        operation = op.get("op", "append")
        collection = str(op.get("collection", ""))
        data = op.get("data")
        key = op.get("key")
        if not isinstance(data, dict):
            continue
        try:
            if operation == "append":
                _can_post(collection)
                if key and not is_owner:
                    raise HTTPException(status_code=403, detail="Only the app owner can set a custom record key.")
                record = append_record(
                    app_id,
                    collection,
                    data,
                    actor=actor,
                    read_file=read_file,
                    write_file=write_file,
                    key=str(key) if key else None,
                )
            elif operation == "upsert" and key:
                if not is_owner:
                    raise HTTPException(status_code=403, detail="Only the app owner can modify this collection.")
                record = upsert_record(
                    app_id,
                    collection,
                    str(key),
                    data,
                    actor=actor,
                    read_file=read_file,
                    write_file=write_file,
                )
            else:
                continue
            applied.append(record)
            from event_log import log_event

            log_event(owner_sub, email, "lab.record_sandbox", {
                "app_id": app_id,
                "collection": collection,
                "key": record.get("key"),
                "op": operation,
            })
        except HTTPException as exc:
            logger.warning(
                "Sandbox record op rejected app=%s collection=%s: %s",
                app_id, collection, exc.detail,
            )
        except ValueError as exc:
            logger.warning("Sandbox record op failed app=%s: %s", app_id, exc)
    return applied
