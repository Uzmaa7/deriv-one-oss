"""App-scoped JSON record storage for Lab apps (GCS-backed via api_apps file helpers)."""

from __future__ import annotations

import json
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal, Optional

from fastapi import HTTPException

_COLLECTION_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,126}$")
# Under app root; NOT "data/" (reserved for Drive sources + blocked from agent writes).
_RECORDS_PREFIX = "_records/"
_MANIFEST_REL = "_records/_collections.json"
_MAX_DOC_BYTES = 64 * 1024
_MAX_COLLECTIONS = 32
_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 50
_READ_SCOPES = ("own", "all")
_SPOOFED_DATA_KEYS = frozenset({
    "created_by", "sub", "user_id", "user_sub", "owner_sub", "owner_email", "submitter", "submitter_email",
})

ReadFileFn = Callable[[str, str], str]
WriteFileFn = Callable[[str, str, str], None]
DeleteFileFn = Callable[[str, str], None]
ListFilesFn = Callable[[str], list[str]]
ReadScope = Literal["own", "all"]


@dataclass(frozen=True)
class RecordActor:
    sub: str
    email: str


@dataclass(frozen=True)
class RecordViewer:
    sub: str
    email: str
    is_admin: bool


def validate_collection(name: str) -> str:
    coll = (name or "").strip().lower()
    if not coll or not _COLLECTION_RE.match(coll):
        raise HTTPException(
            status_code=422,
            detail="Collection name must match [a-z0-9_-]+ (1–63 chars).",
        )
    if coll.startswith("_"):
        raise HTTPException(status_code=422, detail="Collection names cannot start with '_'.")
    return coll


def validate_key(key: str) -> str:
    k = (key or "").strip().lower()
    if not k or not _KEY_RE.match(k):
        raise HTTPException(
            status_code=422,
            detail="Record key must match [a-z0-9_-]+ (1–127 chars).",
        )
    return k


def storage_path(collection: str, key: str) -> str:
    coll = validate_collection(collection)
    k = validate_key(key)
    return f"{_RECORDS_PREFIX}{coll}/{k}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _encode_record(record: dict) -> str:
    raw = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    if len(raw.encode("utf-8")) > _MAX_DOC_BYTES:
        raise HTTPException(status_code=413, detail=f"Record exceeds {_MAX_DOC_BYTES // 1024} KB limit.")
    return raw


def _parse_record(text: str) -> dict:
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=500, detail="Stored record is corrupted.") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=500, detail="Stored record is invalid.")
    return parsed


def _strip_spoofed_identity(data: dict) -> dict:
    """Remove client-supplied identity fields from record payload data."""
    return {k: v for k, v in data.items() if str(k).lower() not in _SPOOFED_DATA_KEYS}


def _created_by(actor: RecordActor) -> dict[str, str]:
    return {"sub": actor.sub, "email": actor.email}


def sanitize_record_for_response(record: dict) -> dict:
    """Redact server-side identity fields before returning to clients."""
    out = dict(record)
    created_by = out.get("created_by")
    if isinstance(created_by, dict):
        out["created_by"] = {"email": str(created_by.get("email") or "")}
    return out


def load_manifest(app_id: str, read_file: ReadFileFn) -> dict[str, dict]:
    try:
        raw = read_file(app_id, _MANIFEST_REL)
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except HTTPException as exc:
        if exc.status_code == 404:
            return {}
        raise


def save_manifest(app_id: str, manifest: dict[str, dict], write_file: WriteFileFn) -> None:
    if len(manifest) > _MAX_COLLECTIONS:
        raise HTTPException(status_code=413, detail=f"Too many collections (max {_MAX_COLLECTIONS}).")
    write_file(app_id, _MANIFEST_REL, json.dumps(manifest, indent=2))


def is_append_only(collection: str, manifest: dict[str, dict]) -> bool:
    cfg = manifest.get(collection) or {}
    if cfg.get("append_only"):
        return True
    if collection in ("submissions",) or collection.endswith("_log"):
        return True
    return False


def get_read_scope(collection: str, manifest: dict[str, dict]) -> ReadScope:
    cfg = manifest.get(collection) or {}
    scope = cfg.get("read_scope")
    if scope in _READ_SCOPES:
        return scope  # type: ignore[return-value]
    return "own" if is_append_only(collection, manifest) else "all"


def set_collection_read_scope(
    app_id: str,
    collection: str,
    read_scope: str,
    *,
    read_file: ReadFileFn,
    write_file: WriteFileFn,
) -> dict[str, Any]:
    if read_scope not in _READ_SCOPES:
        raise HTTPException(status_code=422, detail="read_scope must be 'own' or 'all'.")
    coll = validate_collection(collection)
    manifest = ensure_collection(app_id, coll, read_file=read_file, write_file=write_file)
    manifest[coll]["read_scope"] = read_scope
    save_manifest(app_id, manifest, write_file)
    return {"collection": coll, "read_scope": read_scope, "append_only": is_append_only(coll, manifest)}


def record_visible_to_viewer(record: dict, viewer: RecordViewer, read_scope: ReadScope) -> bool:
    if viewer.is_admin or read_scope == "all":
        return True
    created_by = record.get("created_by")
    if not isinstance(created_by, dict) or not created_by.get("sub"):
        return False
    return str(created_by.get("sub")) == viewer.sub


def ensure_collection(
    app_id: str,
    collection: str,
    *,
    read_file: ReadFileFn,
    write_file: WriteFileFn,
    append_only: Optional[bool] = None,
) -> dict[str, dict]:
    manifest = load_manifest(app_id, read_file)
    if collection not in manifest:
        if len(manifest) >= _MAX_COLLECTIONS:
            raise HTTPException(status_code=413, detail=f"Too many collections (max {_MAX_COLLECTIONS}).")
        append_only_val = append_only if append_only is not None else is_append_only(collection, manifest)
        manifest[collection] = {
            "append_only": append_only_val,
            "read_scope": "own" if append_only_val else "all",
            "created_at": _now_iso(),
        }
        save_manifest(app_id, manifest, write_file)
    return manifest


def list_collection_keys(app_id: str, collection: str, list_files: ListFilesFn) -> list[str]:
    coll = validate_collection(collection)
    prefix = f"{_RECORDS_PREFIX}{coll}/"
    keys: list[str] = []
    for rel in list_files(app_id):
        norm = rel.replace("\\", "/")
        if not norm.startswith(prefix) or not norm.endswith(".json"):
            continue
        key_part = norm[len(prefix): -len(".json")]
        if key_part and not key_part.startswith("_"):
            keys.append(key_part)
    return sorted(keys)


def get_record(
    app_id: str,
    collection: str,
    key: str,
    *,
    read_file: ReadFileFn,
    viewer: Optional[RecordViewer] = None,
    manifest: Optional[dict[str, dict]] = None,
) -> dict:
    coll = validate_collection(collection)
    rel = storage_path(coll, key)
    try:
        record = _parse_record(read_file(app_id, rel))
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Record not found.") from exc
        raise
    if viewer is not None:
        manifest = manifest if manifest is not None else load_manifest(app_id, read_file)
        read_scope = get_read_scope(coll, manifest)
        if not record_visible_to_viewer(record, viewer, read_scope):
            raise HTTPException(status_code=404, detail="Record not found.")
    return record


def list_records(
    app_id: str,
    collection: str,
    *,
    read_file: ReadFileFn,
    list_files: ListFilesFn,
    limit: int = _DEFAULT_LIST_LIMIT,
    viewer: Optional[RecordViewer] = None,
    scope_all: bool = False,
) -> dict[str, Any]:
    coll = validate_collection(collection)
    lim = max(1, min(limit, _MAX_LIST_LIMIT))
    manifest = load_manifest(app_id, read_file)
    read_scope = get_read_scope(coll, manifest)
    if scope_all and viewer is not None and not viewer.is_admin:
        raise HTTPException(status_code=403, detail="Only app admins can request scope=all.")

    keys = list_collection_keys(app_id, coll, list_files)
    items: list[dict] = []
    for key in reversed(keys):
        if len(items) >= lim:
            break
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
        items.append(sanitize_record_for_response(record))
    items.reverse()
    return {"collection": coll, "count": len(items), "items": items}


def append_record(
    app_id: str,
    collection: str,
    data: dict,
    *,
    actor: RecordActor,
    read_file: ReadFileFn,
    write_file: WriteFileFn,
    key: Optional[str] = None,
) -> dict:
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    coll = validate_collection(collection)
    ensure_collection(app_id, coll, read_file=read_file, write_file=write_file)
    record_key = validate_key(key) if key else secrets.token_hex(8)
    rel = storage_path(coll, record_key)
    now = _now_iso()
    record = {
        "key": record_key,
        "collection": coll,
        "created_at": now,
        "updated_at": now,
        "created_by": _created_by(actor),
        "data": _strip_spoofed_identity(data),
    }
    write_file(app_id, rel, _encode_record(record))
    return sanitize_record_for_response(record)


def upsert_record(
    app_id: str,
    collection: str,
    key: str,
    data: dict,
    *,
    actor: RecordActor,
    read_file: ReadFileFn,
    write_file: WriteFileFn,
) -> dict:
    if not isinstance(data, dict):
        raise HTTPException(status_code=422, detail="Request body must be a JSON object.")
    coll = validate_collection(collection)
    record_key = validate_key(key)
    manifest = ensure_collection(app_id, coll, read_file=read_file, write_file=write_file)
    if is_append_only(coll, manifest):
        raise HTTPException(
            status_code=405,
            detail="This collection is append-only; use POST to add records.",
        )
    rel = storage_path(coll, record_key)
    now = _now_iso()
    created_at = now
    created_by = _created_by(actor)
    try:
        existing = _parse_record(read_file(app_id, rel))
        created_at = existing.get("created_at") or now
        existing_cb = existing.get("created_by")
        if isinstance(existing_cb, dict) and existing_cb.get("sub"):
            created_by = existing_cb
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
    record = {
        "key": record_key,
        "collection": coll,
        "created_at": created_at,
        "updated_at": now,
        "created_by": created_by,
        "data": _strip_spoofed_identity(data),
    }
    write_file(app_id, rel, _encode_record(record))
    return sanitize_record_for_response(record)


def delete_record(
    app_id: str,
    collection: str,
    key: str,
    *,
    delete_file: DeleteFileFn,
) -> None:
    rel = storage_path(collection, key)
    try:
        delete_file(app_id, rel)
    except HTTPException as exc:
        if exc.status_code == 404:
            raise HTTPException(status_code=404, detail="Record not found.") from exc
        raise
