"""API endpoints for the Lab — user AI-built web applications.

Endpoints
─────────
POST   /api/apps                     Create a new app
GET    /api/apps                     List apps visible to the caller (own, workspace, or shared with them)
GET    /api/apps/draft               Get in-progress New App draft (auto-save)
PUT    /api/apps/draft               Upsert in-progress New App draft
DELETE /api/apps/draft               Discard New App draft
GET    /api/apps/{id}                Get app metadata
PUT    /api/apps/{id}                Update name / description / visibility
DELETE /api/apps/{id}                Delete app and all its files
GET    /api/apps/{id}/files          List files in the app directory
GET    /api/apps/{id}/files/{path}   Get content of a specific file
GET    /api/apps/{id}/data-sources    List attached Google Drive data sources
POST   /api/apps/{id}/data-sources/google-drive  Attach Sheet/Doc (download snapshot)
DELETE /api/apps/{id}/data-sources/google-drive/{file_id}  Remove data source
GET    /api/apps/{id}/live-data/{file_id}  Fresh Google Sheet/Doc data (owner creds)
GET    /api/apps/{id}/google-workspace/status  User GWS connection status
POST   /api/apps/{id}/chat           Start background AI build (202; poll GET .../build)
GET    /api/apps/{id}/build          Poll in-progress / completed build state
GET    /apps/{slug}                  Serve index.html (auth gated)
GET    /apps/{slug}/{path}           Serve any static file (auth gated)

Security
────────
- All /api/apps/* endpoints: Cloudflare Access JWT via standard middleware.
- /apps/* serve endpoints: also CF-Access gated (nginx passes the JWT headers
  to the FastAPI backend which checks request.state.auth).
- File paths are sanitised to prevent directory traversal on every read/write.
- Apps cannot exceed 100 files or 5 MB total.
- AI-generated files are written only by the backend, never by direct upload.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import mimetypes
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator, Awaitable, Callable, Optional

import anthropic
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from anthropic_blocks import serialize_assistant_block
from app_classifier import classification_meta_payload, resolve_app_config
from model_catalog import platform_default_model
from apps_models import (
    APP_TECH_STACKS,
    APP_VISIBILITIES,
    MAX_APP_DESCRIPTION,
    MAX_APP_NAME,
    App,
    LabDraftRow,
    _load_json_list,
    app_to_dict,
    generate_slug,
    validate_slug,
)
from database import SessionLocal
from sandbox_paths import sandbox_path
from providers import get_blob_store
from tenancy import request_owner_sub

logger = logging.getLogger(__name__)
router = APIRouter()

# ── Config ────────────────────────────────────────────────────────────────────

_MODEL_GATEWAY_API_KEY: Optional[str] = os.environ.get("MODEL_GATEWAY_API_KEY")
_MODEL_GATEWAY_URL_RAW: str = os.environ.get("MODEL_GATEWAY_URL", "http://localhost:4000").rstrip("/")
_MODEL_GATEWAY_URL: str = (
    _MODEL_GATEWAY_URL_RAW[:-3]
    if _MODEL_GATEWAY_URL_RAW.endswith("/v1")
    else _MODEL_GATEWAY_URL_RAW
)
_MODEL: str = platform_default_model()
_MAX_TOOL_TURNS = 25
_MAX_FILES = 100
_MAX_TOTAL_BYTES = 5 * 1024 * 1024  # 5 MB
_MAX_SINGLE_FILE_BYTES = 512 * 1024  # 512 KB

# Local temp directory for app files (ephemeral cache only; the blob store is the source of truth)
_APPS_ROOT = Path(os.environ.get("APPS_GENERATED_DIR", "/tmp/apps"))
_APPS_ROOT.mkdir(parents=True, exist_ok=True)

# Whether to mirror generated apps to the blob store
_APPS_STORE_ENABLED: bool = os.environ.get(
    "APPS_STORE_ENABLED", "1"
).strip().lower() not in ("0", "false", "no", "off")
_APPS_PREFIX = "apps"  # object key: apps/{app_id}/{rel_path}

# Skills directory — mirrors the path used by main.py
_SKILLS_DIR = Path(__file__).parent.parent / "src" / "data" / "skills"

# Fernet key for encrypting per-app secrets (LAB_SECRETS_KEY env var, base-64 url-safe 32 bytes)
_LAB_SECRETS_KEY: Optional[str] = os.environ.get("LAB_SECRETS_KEY")

# Matches assistant text claiming a file/feature was created without any successful write_file
# call this turn — guards against the model narrating a completion it never actually performed.
_CLAIMS_COMPLETION_RE = re.compile(
    r"\b(i(?:'ve| have)?\s+(?:created|implemented|added|built|fixed|updated|written)|"
    r"(?:done|finished|complete)[!.]?\s*$)",
    re.IGNORECASE | re.MULTILINE,
)

# ── Tools ─────────────────────────────────────────────────────────────────────

_WRITE_FILE_TOOL = {
    "name": "write_file",
    "description": (
        "Create or overwrite a file in the app directory. "
        "Use this to write HTML, CSS, JavaScript, or Python files. "
        "Paths must be relative (e.g. 'index.html', 'js/app.js'). "
        "Always write a complete, working index.html as the entry point."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path (e.g. 'index.html', 'css/style.css')",
            },
            "content": {
                "type": "string",
                "description": "Complete file content to write.",
            },
        },
        "required": ["path", "content"],
    },
}

_READ_FILE_TOOL = {
    "name": "read_file",
    "description": "Read the current content of an existing file in the app directory.",
    "input_schema": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file path to read.",
            },
        },
        "required": ["path"],
    },
}

_LIST_FILES_TOOL = {
    "name": "list_files",
    "description": "List all files currently in the app directory.",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}

_RUN_PYTHON_SNIPPET_TOOL = {
    "name": "run_python_snippet",
    "description": (
        "Execute a Python snippet to explore and test external APIs before writing any code. "
        "The snippet runs with all app env vars pre-injected (skill secrets + app secrets), "
        "so you can use os.environ.get('KEY') and import requests directly. "
        "ALWAYS use this tool first when a skill is attached: probe each API endpoint you plan "
        "to call, print the response keys and a sample item, then use those exact field names "
        "in your server.py. Never guess field names from documentation — always verify with this tool. "
        "stdout is captured and returned. Keep snippets short and focused on one endpoint at a time."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "code": {
                "type": "string",
                "description": (
                    "Python code to run. Use print() to output results. "
                    "Example: import os, requests, json; r = requests.get(...); print(json.dumps(r.json(), indent=2))"
                ),
            }
        },
        "required": ["code"],
    },
}

# ── Filesystem helpers ────────────────────────────────────────────────────────

def _app_dir(app_id: str) -> Path:
    return _APPS_ROOT / app_id


def _lab_meta_path(app_id: str) -> Path:
    """Legacy on-disk path — superseded by App.classification_meta (DB)."""
    return _app_dir(app_id) / ".lab_meta.json"


def _read_lab_meta_file(app_id: str) -> dict:
    """Read legacy local lab meta file if present (pre-DB migration)."""
    try:
        path = _lab_meta_path(app_id)
        if not path.is_file():
            return {}
        parsed = json.loads(path.read_text(encoding="utf-8"))
        return parsed if isinstance(parsed, dict) else {}
    except (OSError, TypeError, ValueError):
        return {}


def _load_classification_meta(app: App) -> dict:
    """Load AI classification metadata from DB, with legacy file fallback."""
    raw = getattr(app, "classification_meta", None)
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return parsed
        except (TypeError, ValueError):
            pass
    return _read_lab_meta_file(app.id)


def _sanitise_path(app_id: str, rel_path: str, *, allow_reserved: bool = False) -> Path:
    """Resolve a relative path inside the app directory, rejecting traversal."""
    base = _app_dir(app_id).resolve()
    # Strip leading slashes / dots to make it relative
    rel = re.sub(r"^[./\\]+", "", rel_path.replace("\\", "/")).strip()
    if not rel:
        raise HTTPException(status_code=400, detail="Empty file path.")
    if not allow_reserved and (rel.startswith("data/") or rel.startswith("_records/")):
        raise HTTPException(
            status_code=400,
            detail="Use the Records API for app data (POST /api/apps/{app_id}/records/{collection}).",
        )
    # Reject hidden files
    parts = Path(rel).parts
    if any(p.startswith(".") for p in parts):
        raise HTTPException(status_code=400, detail="Hidden files are not allowed.")
    resolved = (base / rel).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="Path traversal detected.")
    return resolved


def _read_app_file(app_id: str, rel_path: str, *, allow_reserved: bool = False) -> str:
    path = _sanitise_path(app_id, rel_path, allow_reserved=allow_reserved)
    if path.exists():
        if path.stat().st_size > _MAX_SINGLE_FILE_BYTES:
            raise HTTPException(status_code=413, detail="File too large to read.")
        return path.read_text(encoding="utf-8", errors="replace")
    # Fall back to the blob store (source of truth)
    content = _store_download_text(app_id, rel_path)
    if content is None:
        raise HTTPException(status_code=404, detail=f"File not found: {rel_path}")
    if len(content.encode("utf-8")) > _MAX_SINGLE_FILE_BYTES:
        raise HTTPException(status_code=413, detail="File too large to read.")
    return content


def _write_app_file(app_id: str, rel_path: str, content: str, *, allow_reserved: bool = False) -> None:
    """Write content to a file locally and persist to the blob store."""
    if len(content.encode("utf-8")) > _MAX_SINGLE_FILE_BYTES:
        raise ValueError(f"File content too large for {rel_path} (max 512 KB).")
    path = _sanitise_path(app_id, rel_path, allow_reserved=allow_reserved)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    _store_upload_file(app_id, rel_path, content)


def _list_app_files(app_id: str) -> list[str]:
    """Return all relative file paths. The blob store is the source of truth."""
    if _APPS_STORE_ENABLED:
        return _store_list_files(app_id)
    base = _app_dir(app_id)
    if not base.exists():
        return []
    result = []
    for p in sorted(base.rglob("*")):
        if p.is_file():
            try:
                rel = str(p.relative_to(base))
                result.append(rel)
            except ValueError:
                pass
    return result


def _load_app_py_files(app_id: str) -> dict[str, str]:
    """Load root-level .py modules for sandbox execution (server.py, data.py, etc.)."""
    files: dict[str, str] = {}
    for rel in _list_app_files(app_id):
        norm = rel.replace("\\", "/")
        if not norm.endswith(".py") or "/" in norm:
            continue
        try:
            files[norm] = _read_app_file(app_id, norm)
        except HTTPException:
            continue
    return files


def _normalize_run_function_name(name: str) -> str:
    """Map client function names to server.py snake_case (getKpis -> get_kpis)."""
    stripped = re.sub(r"[^a-zA-Z0-9_]", "", name or "")
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", stripped).lower()
    return re.sub(r"[^a-z0-9_]", "", snake)


def _parse_sandbox_json_stdout(stdout: str) -> tuple[Optional[dict], Optional[str]]:
    """Parse JSON from sandbox stdout; tolerate debug lines before the JSON payload."""
    text = (stdout or "").strip()
    if not text:
        return None, "empty stdout"
    try:
        parsed = json.loads(text)
        return (parsed if isinstance(parsed, dict) else {"value": parsed}), None
    except (ValueError, TypeError):
        pass
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, dict):
                return parsed, None
        except (ValueError, TypeError):
            continue
    return None, "non-JSON stdout"


def _substitute_app_placeholders(content: bytes, app_id: str) -> bytes:
    """Replace scaffold placeholders when serving generated app assets."""
    if b"{{APP_ID}}" not in content and b"__APP_ID__" not in content:
        return content
    text = content.decode("utf-8", errors="replace")
    text = text.replace("{{APP_ID}}", app_id).replace("__APP_ID__", app_id)
    return text.encode("utf-8")


# ── Durable app storage ───────────────────────────────────────────────────────
#
# Generated apps are written to local disk (that is what the build and the
# static server read) and mirrored into the configured blob store so they
# survive a restart. Every function here is best-effort: a storage failure logs
# and returns, because the local copy is still good enough to serve from.

def _app_key(app_id: str, rel_path: str) -> str:
    return f"{_APPS_PREFIX}/{app_id}/{rel_path}"


def _app_prefix(app_id: str) -> str:
    return f"{_APPS_PREFIX}/{app_id}/"


def _store_upload_file(app_id: str, rel_path: str, content: str) -> None:
    if not _APPS_STORE_ENABLED:
        return
    try:
        get_blob_store().put(
            _app_key(app_id, rel_path),
            content.encode("utf-8"),
            content_type="text/plain; charset=utf-8",
        )
    except Exception:
        logger.exception("app store: upload failed for %s/%s", app_id, rel_path)


def _store_upload_bytes(app_id: str, rel_path: str, content: bytes) -> None:
    if not _APPS_STORE_ENABLED:
        return
    try:
        get_blob_store().put(_app_key(app_id, rel_path), content)
    except Exception:
        logger.exception("app store: upload failed for %s/%s", app_id, rel_path)


def _store_download_text(app_id: str, rel_path: str) -> Optional[str]:
    if not _APPS_STORE_ENABLED:
        return None
    try:
        return get_blob_store().get_text(_app_key(app_id, rel_path))
    except KeyError:
        return None
    except Exception:
        logger.exception("app store: download failed for %s/%s", app_id, rel_path)
        return None


def _store_download_bytes(app_id: str, rel_path: str) -> Optional[bytes]:
    if not _APPS_STORE_ENABLED:
        return None
    try:
        return get_blob_store().get(_app_key(app_id, rel_path))
    except KeyError:
        return None
    except Exception:
        logger.exception("app store: download failed for %s/%s", app_id, rel_path)
        return None


def _store_list_files(app_id: str) -> list[str]:
    if not _APPS_STORE_ENABLED:
        return []
    prefix = _app_prefix(app_id)
    try:
        return sorted(
            rel
            for info in get_blob_store().list(prefix)
            if (rel := info.key[len(prefix):])
        )
    except Exception:
        logger.exception("app store: list failed for app %s", app_id)
        return []


def _store_delete_app(app_id: str) -> None:
    if not _APPS_STORE_ENABLED:
        return
    try:
        removed = get_blob_store().delete_prefix(_app_prefix(app_id))
        logger.info("app store: deleted %d object(s) for app %s", removed, app_id)
    except Exception:
        logger.exception("app store: delete failed for app %s", app_id)


def _store_delete_blob(app_id: str, rel_path: str) -> None:
    if not _APPS_STORE_ENABLED:
        return
    try:
        get_blob_store().delete(_app_key(app_id, rel_path))
    except Exception:
        logger.exception("app store: delete failed for %s/%s", app_id, rel_path)



def _write_app_binary_file(app_id: str, rel_path: str, content: bytes) -> None:
    """Write binary content locally and persist to the blob store."""
    if len(content) > _MAX_SINGLE_FILE_BYTES:
        raise ValueError(f"File content too large for {rel_path} (max 512 KB).")
    path = _sanitise_path(app_id, rel_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    _store_upload_bytes(app_id, rel_path, content)


def _delete_app_file(app_id: str, rel_path: str, *, allow_reserved: bool = False) -> None:
    """Remove a file from local disk and the blob store."""
    try:
        path = _sanitise_path(app_id, rel_path, allow_reserved=allow_reserved)
        if path.exists():
            path.unlink()
    except HTTPException:
        pass
    _store_delete_blob(app_id, rel_path)


def _records_read_file(app_id: str, rel_path: str) -> str:
    return _read_app_file(app_id, rel_path, allow_reserved=True)


def _records_write_file(app_id: str, rel_path: str, content: str) -> None:
    _write_app_file(app_id, rel_path, content, allow_reserved=True)


def _records_delete_file(app_id: str, rel_path: str) -> None:
    _delete_app_file(app_id, rel_path, allow_reserved=True)


# ── Google Drive data sources (Lab) ───────────────────────────────────────────

_DATA_MANIFEST_PATH = "data/sources/manifest.json"
_LAB_SNIPPET_DATA_ROOT = sandbox_path("_lab_data")
_LAB_SNIPPET_DATA_DIR = sandbox_path("_lab_data", "data", "sources")
_LAB_RUN_DATA_ROOT = sandbox_path("_lab_srv")
_DATA_FILE_EXTS = (".csv", ".txt", ".xlsx")

_LAB_DATA_ANALYSIS_PROMPT = """

━━━ DATA ANALYSIS — DO THIS FIRST (MANDATORY when data sources are attached) ━━━

Before writing ANY dashboard files, you MUST probe each attached data file with
run_python_snippet. Do NOT guess column names or types from the filename.

Attached data files are injected into every run_python_snippet at LAB_DATA_DIR.
Read data/sources/manifest.json with read_file for the list of sources.

For each CSV/Excel source:
  1. PROBE: df = pd.read_csv(os.path.join(os.environ["LAB_DATA_DIR"], "<file_id>.csv"))
     (use read_excel for .xlsx files)
  2. Print: shape, dtypes, head(5), null counts, describe() for numeric columns
  3. For categorical columns: value_counts().head(10)
  4. For likely KPI columns: sum/mean/count aggregates
  5. ANALYZE: pick 2-4 chart dimensions and 1-3 KPI metrics from real output
  6. Only then write_file for index.html / app.js / server.py

Use EXACT column names from probe output — never invent field names.

Example probe:
  import os, pandas as pd
  path = os.path.join(os.environ["LAB_DATA_DIR"], "FILE_ID.csv")
  df = pd.read_csv(path)
  print("shape:", df.shape)
  print("dtypes:\\n", df.dtypes)
  print("head:\\n", df.head().to_string())
  print("nulls:", df.isnull().sum().to_dict())

For Google Docs (.txt at LAB_DATA_DIR): read the file, summarize structure,
extract key figures before designing a summary dashboard.

NEVER call Google APIs or use GOOGLE_OAUTH_CREDENTIALS in run_python_snippet.
Data is already downloaded — analyze local files only.

After probing, build the deployed dashboard to load LIVE data on every page load:

  GET /api/apps/{APP_ID}/live-data/{FILE_ID}

Returns fresh rows from Google (app owner's credentials). Use the exact app UUID
from this session — never a slug. Show a loading skeleton while fetching; handle
403/404 errors gracefully. Optional ?refresh=1 bypasses the 60s server cache.

Example (static app.js):
  const APP_ID = "{app id from context}";
  const FILE_ID = "google_drive_file_id";
  const res = await fetch(`/api/apps/${APP_ID}/live-data/${FILE_ID}`);
  const data = await res.json();
  // data.columns, data.rows for sheets; data.text for docs

For python tech_stack you may still use server.py + /run for transforms, but
prefer calling the live-data endpoint from the frontend when displaying charts.
"""

_LAB_LIVE_DATA_PROMPT = """

━━━ LIVE DATA ON DASHBOARD LOAD (MANDATORY for Google-backed dashboards) ━━━

The deployed dashboard MUST fetch fresh Google data when the page loads:
  GET /api/apps/<APP_UUID>/live-data/<FILE_ID>

- Use the exact app UUID (not slug) in the URL.
- FILE_ID comes from data/sources/manifest.json (file_id field per source).
- Response includes columns, rows, fetched_at, truncated (and text for Docs).
- Show loading UI while fetching; display a clear error if res.ok is false.
- Add a Refresh button that calls the same URL with ?refresh=1.

Do NOT rely on the GCS snapshot CSV for the deployed dashboard UI — snapshots
are for probing during build only. The live-data endpoint refreshes from Google.
"""


def _read_data_manifest(app_id: str) -> list[dict]:
    try:
        raw = _read_app_file(app_id, _DATA_MANIFEST_PATH)
        data = json.loads(raw)
        return data if isinstance(data, list) else []
    except HTTPException:
        return []


def _write_data_manifest(app_id: str, entries: list[dict]) -> None:
    _write_app_file(app_id, _DATA_MANIFEST_PATH, json.dumps(entries, indent=2))


def _load_app_data_files(app_id: str) -> dict[str, bytes]:
    """Load binary data source files for sandbox injection."""
    result: dict[str, bytes] = {}
    for rel in _list_app_files(app_id):
        if not rel.startswith("data/sources/"):
            continue
        if rel.endswith("manifest.json") or rel.endswith(".meta.json"):
            continue
        if not any(rel.endswith(ext) for ext in _DATA_FILE_EXTS):
            continue
        local_path = _app_dir(app_id) / rel
        if local_path.is_file():
            data = local_path.read_bytes()
        else:
            data = _store_download_bytes(app_id, rel)
        if data is not None:
            result[rel] = data
    return result


def _build_file_injection_prolog(files: dict[str, bytes], root_dir: str) -> str:
    """Return Python source that writes *files* under *root_dir* inside a sandbox."""
    if not files:
        return ""
    lines = ["import os as _os, base64 as _b64\n"]
    for rel_path, content in sorted(files.items()):
        b64 = base64.b64encode(content).decode("ascii")
        full_path = f"{root_dir.rstrip('/')}/{rel_path}".replace("//", "/")
        dir_name = str(Path(full_path).parent).replace("\\", "/")
        lines.append(f"_os.makedirs({dir_name!r}, exist_ok=True)\n")
        lines.append(f"with open({full_path!r}, 'wb') as _f:\n")
        lines.append(f"    _f.write(_b64.b64decode({b64!r}))\n")
    return "".join(lines)


def _snippet_env_with_data_dir() -> dict[str, str]:
    return {"LAB_DATA_DIR": _LAB_SNIPPET_DATA_DIR}


# ── Live Google data cache (per app + file_id) ─────────────────────────────────

_LIVE_DATA_CACHE_TTL_SECS = 60
_live_data_cache: dict[str, tuple[float, dict]] = {}


def _live_data_cache_key(app_id: str, file_id: str) -> str:
    return f"{app_id}:{file_id}"


def _get_live_data_cache(cache_key: str) -> Optional[dict]:
    entry = _live_data_cache.get(cache_key)
    if not entry:
        return None
    expires_at, payload = entry
    if time.time() >= expires_at:
        _live_data_cache.pop(cache_key, None)
        return None
    return payload


def _set_live_data_cache(cache_key: str, payload: dict) -> None:
    _live_data_cache[cache_key] = (
        time.time() + _LIVE_DATA_CACHE_TTL_SECS,
        payload,
    )


def _invalidate_live_data_cache(app_id: str, file_id: Optional[str] = None) -> None:
    if file_id:
        _live_data_cache.pop(_live_data_cache_key(app_id, file_id), None)
        return
    prefix = f"{app_id}:"
    for key in list(_live_data_cache):
        if key.startswith(prefix):
            _live_data_cache.pop(key, None)


def _get_manifest_entry(app_id: str, file_id: str) -> Optional[dict]:
    return next(
        (e for e in _read_data_manifest(app_id) if e.get("file_id") == file_id),
        None,
    )


async def _download_google_drive_for_owner(
    owner_sub: str,
    file_id: str,
    *,
    not_connected_detail: Optional[str] = None,
):
    """Download a Drive file using the app owner's Google credentials."""
    from google_oauth import GoogleOAuthError
    from google_workspace_integration import (
        download_drive_file,
        fetch_google_credentials_for_sub,
        refresh_access_token,
    )

    creds_json = fetch_google_credentials_for_sub(owner_sub)
    if not creds_json:
        raise HTTPException(
            status_code=403,
            detail=not_connected_detail or (
                "Google Workspace is not connected for the app owner. "
                "The owner must connect at /integrations."
            ),
        )

    try:
        access_token = await refresh_access_token(creds_json)
        return await download_drive_file(
            access_token, file_id, max_bytes=_MAX_SINGLE_FILE_BYTES,
        )
    except GoogleOAuthError as exc:
        if exc.google_error == "not_found":
            raise HTTPException(
                status_code=404,
                detail="Google Drive file not found or owner does not have access.",
            ) from exc
        if exc.google_error in ("unauthorized", "invalid_grant"):
            raise HTTPException(
                status_code=403,
                detail="Google access expired. App owner must reconnect at /integrations.",
            ) from exc
        raise HTTPException(
            status_code=502,
            detail="Could not download file from Google Drive.",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Drive download failed for owner file %s", file_id)
        raise HTTPException(
            status_code=502,
            detail="Could not download file from Google Drive.",
        ) from exc


def _persist_google_drive_download(
    app_id: str,
    file_id: str,
    downloaded,
    fetched_at: Optional[str] = None,
) -> dict:
    """Write Drive download to GCS and update manifest. Returns manifest entry."""
    fetched_at = fetched_at or datetime.now(timezone.utc).isoformat()
    data_rel = f"data/sources/{file_id}.{downloaded.content_path_ext}"
    meta_rel = f"data/sources/{file_id}.meta.json"

    meta = {
        "file_id": file_id,
        "name": downloaded.name,
        "mime_type": downloaded.mime_type,
        "source_type": downloaded.source_type,
        "path": data_rel,
        "fetched_at": fetched_at,
        "byte_size": len(downloaded.content),
        "truncated": downloaded.truncated,
        "row_estimate": downloaded.row_estimate,
        "columns_preview": downloaded.columns_preview,
    }

    try:
        _write_app_binary_file(app_id, data_rel, downloaded.content)
        _write_app_file(app_id, meta_rel, json.dumps(meta, indent=2))
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    manifest = [e for e in _read_data_manifest(app_id) if e.get("file_id") != file_id]
    entry = {
        "file_id": file_id,
        "name": downloaded.name,
        "source_type": downloaded.source_type,
        "path": data_rel,
        "fetched_at": fetched_at,
        "row_estimate": downloaded.row_estimate,
        "truncated": downloaded.truncated,
        "columns_preview": downloaded.columns_preview,
    }
    manifest.append(entry)
    _write_data_manifest(app_id, manifest)
    return entry


async def _fetch_live_google_data(
    app_id: str,
    owner_sub: str,
    file_id: str,
    *,
    bypass_cache: bool = False,
) -> dict:
    """Refresh from Google (or cache), update GCS snapshot, return live-data JSON."""
    from google_workspace_integration import build_live_data_payload

    if not _get_manifest_entry(app_id, file_id):
        raise HTTPException(status_code=404, detail="Data source not attached to this app.")

    cache_key = _live_data_cache_key(app_id, file_id)
    if not bypass_cache:
        cached = _get_live_data_cache(cache_key)
        if cached is not None:
            return {**cached, "cached": True}

    downloaded = await _download_google_drive_for_owner(owner_sub, file_id)
    fetched_at = datetime.now(timezone.utc).isoformat()
    _persist_google_drive_download(app_id, file_id, downloaded, fetched_at)
    payload = build_live_data_payload(downloaded, fetched_at)
    payload["cached"] = False
    _set_live_data_cache(cache_key, payload)
    return payload


async def _attach_google_drive_source(
    app_id: str,
    owner_sub: str,
    url_or_id: str,
) -> dict:
    """Download a Drive file and persist it under data/sources/. Returns manifest entry."""
    from google_workspace_integration import parse_drive_url

    file_id = parse_drive_url(url_or_id)
    if not file_id:
        raise HTTPException(status_code=422, detail="Invalid Google Drive URL or file ID.")

    downloaded = await _download_google_drive_for_owner(
        owner_sub,
        file_id,
        not_connected_detail=(
            "Google Workspace is not connected. "
            "Connect your account at /integrations before attaching Drive files."
        ),
    )
    entry = _persist_google_drive_download(app_id, file_id, downloaded)
    _invalidate_live_data_cache(app_id, file_id)
    logger.info(
        "Lab: attached Google Drive source %s (%s) for app %s",
        file_id,
        downloaded.name,
        app_id,
    )
    return entry


def _extract_drive_urls_from_text(text: str) -> list[str]:
    """Return unique Drive file IDs found in *text*.

    Uses parse_drive_url_strict (URL-pattern only) rather than parse_drive_url —
    this scans arbitrary chat prose, so a bare-ID fallback would misfire on any
    plain word 10+ chars long (e.g. "JavaScript", "implementation").
    """
    from google_workspace_integration import parse_drive_url_strict

    found: list[str] = []
    seen: set[str] = set()
    # Whole-string parse first
    direct = parse_drive_url_strict(text.strip())
    if direct and direct not in seen:
        seen.add(direct)
        found.append(direct)
    # Token scan for URLs embedded in prose
    for token in re.split(r"\s+", text):
        token = token.strip(".,;()[]\"'")
        fid = parse_drive_url_strict(token)
        if fid and fid not in seen:
            seen.add(fid)
            found.append(fid)
    return found


# ── Background Lab build state ────────────────────────────────────────────────

_BUILD_PHASES = ("building", "completed", "failed")
_MAX_BUILD_TIMELINE = 400
_STALE_BUILD_MINUTES = 45
_active_lab_builds: set[str] = set()


def _load_build_timeline(app: App) -> list[dict]:
    try:
        data = json.loads(app.build_timeline or "[]")
        return data if isinstance(data, list) else []
    except (TypeError, ValueError):
        return []


def _append_build_timeline_event(app_id: str, event: str, data: dict) -> None:
    """Append a build timeline event to the app row (DB source of truth for polling)."""
    now = datetime.now(timezone.utc)
    entry = {"ts": now.isoformat(), "event": event, **data}
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            return
        timeline = _load_build_timeline(app)
        timeline.append(entry)
        if len(timeline) > _MAX_BUILD_TIMELINE:
            timeline = timeline[-_MAX_BUILD_TIMELINE:]
        app.build_timeline = json.dumps(timeline)
        app.build_updated_at = now.replace(tzinfo=None)
        db.commit()
    except Exception:
        logger.exception("Failed to append build timeline for app %s", app_id)
        db.rollback()
    finally:
        db.close()


def _set_build_phase(app_id: str, phase: Optional[str]) -> None:
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            return
        app.build_phase = phase
        app.build_updated_at = now.replace(tzinfo=None)
        db.commit()
    finally:
        db.close()


def _is_build_stale(app: App) -> bool:
    if app.build_phase != "building" or not app.build_updated_at:
        return False
    updated = app.build_updated_at
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=timezone.utc)
    age = datetime.now(timezone.utc) - updated
    return age.total_seconds() > _STALE_BUILD_MINUTES * 60


def _build_status_dict(app: App) -> dict:
    timeline = _load_build_timeline(app)
    files_written: list[str] = []
    assistant_preview = ""
    error_message: Optional[str] = None
    for entry in timeline:
        ev = entry.get("event")
        if ev == "text" and entry.get("delta"):
            assistant_preview += entry["delta"]
        elif ev == "file_written" and entry.get("path"):
            if entry["path"] not in files_written:
                files_written.append(entry["path"])
        elif ev == "done" and entry.get("files_written"):
            for p in entry["files_written"]:
                if p not in files_written:
                    files_written.append(p)
        elif ev == "error" and entry.get("message"):
            error_message = entry["message"]
    return {
        "phase": app.build_phase,
        "updated_at": (
            app.build_updated_at.isoformat() if app.build_updated_at else None
        ),
        "timeline": timeline,
        "assistant_preview": assistant_preview,
        "files_written": files_written,
        "error_message": error_message,
        "chat_history": _load_json_list(app.chat_history),
        "lab_mode": getattr(app, "lab_mode", "plan") or "plan",
        "plan_ready": bool(_load_classification_meta(app).get("plan_ready")),
    }


# ─────────────────────────────────────────────────────────────────────────────

def _update_manifest(app: App, db) -> None:
    files = _list_app_files(app.id)
    app.file_manifest = json.dumps(files)
    app.updated_at = datetime.now(timezone.utc)
    db.commit()


# ── Skill helpers ─────────────────────────────────────────────────────────────

_SLUG_ALLOWLIST_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$")


def _load_skill(slug: str) -> str:
    """Read skill.md content for the given slug.

    Strips the YAML front-matter block and appends any reference *.md files.
    Returns an empty string if the skill does not exist on disk.
    Path-traversal is prevented by resolving both paths and calling relative_to().
    """
    if not _SLUG_ALLOWLIST_RE.match(slug):
        return ""
    skills_base = _SKILLS_DIR.resolve()
    skill_path = (skills_base / slug / "skill.md").resolve()
    try:
        skill_path.relative_to(skills_base)
    except ValueError:
        return ""
    if not skill_path.exists():
        return ""
    content = skill_path.read_text(encoding="utf-8")
    if content.startswith("---"):
        end = content.find("\n---", 3)
        if end != -1:
            content = content[end + 4:].strip()
    refs_dir = skills_base / slug / "references"
    if refs_dir.is_dir():
        for ref in sorted(refs_dir.glob("*.md")):
            safe = ref.resolve()
            try:
                safe.relative_to(refs_dir.resolve())
            except ValueError:
                continue
            content += f"\n\n---\n\n## {ref.stem}\n\n{safe.read_text(encoding='utf-8')}"
    return content


def _load_skill_secrets(slug: str) -> dict[str, str]:
    """Resolve GCP Secret Manager refs for a skill into a key→value dict.

    Returns an empty dict when the vault is unavailable or the skill has no secrets.
    Failures on individual secret reads are logged and skipped.
    """
    if not _SLUG_ALLOWLIST_RE.match(slug):
        return {}
    try:
        from skill_submission_models import SkillSecret  # type: ignore[import]
        from secret_vault import SecretVaultError, get_vault  # type: ignore[import]
    except ImportError:
        return {}
    db = SessionLocal()
    try:
        rows = db.query(SkillSecret).filter(SkillSecret.slug == slug).all()
        if not rows:
            return {}
        try:
            vault = get_vault()
        except Exception:
            logger.warning("_load_skill_secrets: vault unavailable for skill '%s'", slug)
            return {}
        result: dict[str, str] = {}
        for row in rows:
            try:
                result[row.env_key] = vault.read(row.secret_ref)
            except SecretVaultError:
                logger.warning(
                    "_load_skill_secrets: could not read secret '%s' for slug '%s'",
                    row.env_key, slug,
                )
        return result
    finally:
        db.close()


# ── Per-app secret encryption helpers ────────────────────────────────────────

def _get_fernet():
    """Return a Fernet instance from LAB_SECRETS_KEY, or None if unconfigured."""
    if not _LAB_SECRETS_KEY:
        return None
    try:
        from cryptography.fernet import Fernet  # type: ignore[import]
        return Fernet(_LAB_SECRETS_KEY.encode())
    except Exception:
        logger.error("LAB_SECRETS_KEY is not a valid Fernet key")
        return None


def _encrypt_env_config(data: dict) -> str:
    """Encrypt a key→value dict as a Fernet-encrypted JSON string."""
    fernet = _get_fernet()
    if not fernet:
        raise HTTPException(status_code=503, detail="Secret storage not configured (LAB_SECRETS_KEY missing).")
    return fernet.encrypt(json.dumps(data).encode()).decode()


def _decrypt_env_config(encrypted: Optional[str]) -> dict:
    """Decrypt the env_config blob. Returns {} on any failure — never raises."""
    if not encrypted:
        return {}
    fernet = _get_fernet()
    if not fernet:
        return {}
    try:
        from cryptography.fernet import InvalidToken  # type: ignore[import]
        return json.loads(fernet.decrypt(encrypted.encode()).decode())
    except Exception:
        logger.error("Failed to decrypt env_config — key mismatch or corrupt data")
        return {}


def _skill_env_key_names(skill_slugs: list[str]) -> list[str]:
    """Return env_key names registered in the DB for all given skill slugs.

    This is a lightweight DB-only query — no vault access, no secret values.
    Returns an empty list if the skill submission tables are unavailable.
    """
    if not skill_slugs:
        return []
    try:
        from skill_submission_models import SkillSecret  # type: ignore[import]
    except ImportError:
        return []
    db = SessionLocal()
    try:
        rows = (
            db.query(SkillSecret.env_key)
            .filter(SkillSecret.slug.in_(skill_slugs))
            .all()
        )
        return [r.env_key for r in rows]
    finally:
        db.close()


def _app_dict_with_env_keys(app: App) -> dict:
    """Serialize an App to dict, adding decrypted env_var_keys and skill_env_keys."""
    env_data = _decrypt_env_config(app.env_config)
    slugs = _load_json_list(app.skill_ids)
    skill_keys = _skill_env_key_names(slugs)
    d = app_to_dict(app, env_var_keys=list(env_data.keys()))
    d["skill_env_keys"] = skill_keys
    meta = _load_classification_meta(app)
    confidence = meta.get("classification_confidence")
    if isinstance(confidence, str) and confidence in ("low", "medium", "high"):
        d["classification_confidence"] = confidence
    if meta:
        d["classification_meta"] = meta
        d["plan_ready"] = bool(meta.get("plan_ready"))
        d["plan_implemented"] = bool(meta.get("plan_implemented"))
        plan = meta.get("build_plan")
        if isinstance(plan, dict):
            d["build_plan"] = plan
    return d


def _delete_app_files(app_id: str) -> None:
    d = _app_dir(app_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
    _store_delete_app(app_id)


# ── Auth helpers ──────────────────────────────────────────────────────────────

def deps_owner_sub(request: Request) -> str:
    return request_owner_sub(request)


def _get_caller_email(request: Request) -> str:
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "email", "") or ""


def _ensure_owner(app: Optional[App], owner_sub: str) -> App:
    if not app or app.owner_sub != owner_sub:
        raise HTTPException(status_code=404, detail="App not found.")
    return app


def _app_role(app: Optional[App], owner_sub: str, email: str = "") -> Optional[str]:
    """Return 'admin' | 'member' | None. Fail closed."""
    if not app:
        return None
    if app.owner_sub == owner_sub:
        return "admin"
    e = (email or "").lower()
    admin_emails = [a.lower() for a in _load_json_list(getattr(app, "admin_users", None))]
    if e and e in admin_emails:
        return "admin"
    if app.visibility == "workspace":
        return "member"
    if app.visibility == "shared" and e:
        allowed = [a.lower() for a in _load_json_list(app.allowed_users)]
        if e in allowed:
            return "member"
    return None


def _check_read_access(app: Optional[App], owner_sub: str, email: str = "") -> App:
    """Allow read if owner, workspace-visible, shared allowlist, or app admin."""
    if not app or _app_role(app, owner_sub, email) is None:
        raise HTTPException(status_code=404, detail="App not found.")
    return app


def _record_viewer(app: App, owner_sub: str, email: str) -> "RecordViewer":
    from lab_records import RecordViewer

    role = _app_role(app, owner_sub, email) or "member"
    return RecordViewer(sub=owner_sub, email=email, is_admin=role == "admin")


def _record_actor(owner_sub: str, email: str) -> "RecordActor":
    from lab_records import RecordActor

    return RecordActor(sub=owner_sub, email=email)


def _check_record_post_access(app: App, owner_sub: str, email: str, collection: str) -> None:
    """Owner can always append; read-access users may POST to append-only collections."""
    if app.owner_sub == owner_sub:
        return
    from lab_records import is_append_only, load_manifest, validate_collection

    coll = validate_collection(collection)
    manifest = load_manifest(app.id, _read_app_file)
    if not is_append_only(coll, manifest):
        raise HTTPException(status_code=403, detail="Only the app owner can write to this collection.")
    _check_read_access(app, owner_sub, email)


def _check_record_owner_write(app: App, owner_sub: str) -> None:
    if app.owner_sub != owner_sub:
        raise HTTPException(status_code=403, detail="Only the app owner can modify this collection.")


# ── Request / response bodies ─────────────────────────────────────────────────

class AppCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_APP_NAME)
    description: str = Field(
        default="",
        max_length=MAX_APP_DESCRIPTION,
        description="What the app should do — used by AI to infer build settings.",
    )
    tech_stack: Optional[str] = None
    skill_ids: Optional[list[str]] = None
    mode: Optional[str] = None  # "standard" | "overnight"
    data_source_url: Optional[str] = Field(default=None, max_length=2000)


class AppUpdateRequest(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=MAX_APP_NAME)
    description: Optional[str] = Field(default=None, max_length=MAX_APP_DESCRIPTION)
    visibility: Optional[str] = None
    skill_ids: Optional[list[str]] = None
    tech_stack: Optional[str] = None  # owner override: "python" | "react"
    lab_mode: Optional[str] = None  # "plan" | "agent"


class LabModeRequest(BaseModel):
    lab_mode: str = Field(..., pattern="^(plan|agent)$")


class AppSharingRequest(BaseModel):
    visibility: str  # "private" | "shared" | "workspace"
    allowed_users: list[str] = Field(default_factory=list)  # list of email addresses
    admin_users: list[str] = Field(default_factory=list)  # app admins (see all records)
    skill_ids: Optional[list[str]] = None


class RecordCollectionScopeBody(BaseModel):
    read_scope: str = Field(..., pattern="^(own|all)$")


class SecretsUpsertRequest(BaseModel):
    secrets: dict[str, str] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000)


class RecordDataBody(BaseModel):
    """JSON document payload for Lab record storage."""

    data: dict = Field(default_factory=dict)
    key: Optional[str] = Field(default=None, max_length=127)


# ── Lab (New App) drafts (auto-save / resume) ─────────────────────────────────

_MAX_LAB_DRAFT_SKILLS = 64
_MAX_SHEET_URL = 2000
_SLUG_SAFE_RE = re.compile(r"^[a-z0-9][a-z0-9\-_]{0,78}$")


class LabDraftPayload(BaseModel):
    """Saved form state for an in-progress Lab New App modal. No credentials."""

    step: str = Field(default="pick", max_length=16)
    mode: str = Field(default="skills", max_length=16)
    name: str = Field(default="", max_length=MAX_APP_NAME)
    description: str = Field(default="", max_length=MAX_APP_DESCRIPTION)
    skill_ids: list[str] = Field(default_factory=list)
    sheet_url: str = Field(default="", max_length=_MAX_SHEET_URL)


def _sanitize_lab_skill_ids(ids: list[str]) -> list[str]:
    out: list[str] = []
    for s in ids or []:
        if not isinstance(s, str):
            continue
        slug = s.strip().lower()[:80]
        if _SLUG_SAFE_RE.match(slug) and slug not in out:
            out.append(slug)
        if len(out) >= _MAX_LAB_DRAFT_SKILLS:
            break
    return out


@router.get("/api/apps/draft")
def get_lab_draft(owner_sub: str = Depends(deps_owner_sub)):
    """Return the caller's saved Lab New App draft, or null if none."""
    db = SessionLocal()
    try:
        row = db.query(LabDraftRow).filter(LabDraftRow.owner_sub == owner_sub).first()
        if not row:
            return {"draft": None}
        try:
            draft = json.loads(row.draft)
            if not isinstance(draft, dict):
                return {"draft": None}
        except Exception:
            return {"draft": None}
        return {"draft": draft, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
    finally:
        db.close()


@router.put("/api/apps/draft")
def save_lab_draft(body: LabDraftPayload, owner_sub: str = Depends(deps_owner_sub)):
    """Upsert the caller's Lab New App draft (auto-save)."""
    step = body.step if body.step in ("pick", "form") else "pick"
    mode = body.mode if body.mode in ("gws", "skills") else "skills"
    payload = {
        "step": step,
        "mode": mode,
        "name": (body.name or "").strip()[:MAX_APP_NAME],
        "description": (body.description or "").strip()[:MAX_APP_DESCRIPTION],
        "skill_ids": _sanitize_lab_skill_ids(body.skill_ids),
        "sheet_url": (body.sheet_url or "").strip()[:_MAX_SHEET_URL],
    }
    draft_json = json.dumps(payload, separators=(",", ":"))
    db = SessionLocal()
    try:
        row = db.query(LabDraftRow).filter(LabDraftRow.owner_sub == owner_sub).first()
        if row:
            row.draft = draft_json
        else:
            row = LabDraftRow(owner_sub=owner_sub, draft=draft_json)
            db.add(row)
        db.commit()
        return {"ok": True, "updated_at": row.updated_at.isoformat() if row.updated_at else None}
    except Exception:
        db.rollback()
        logger.exception("save_lab_draft failed for owner=%s", owner_sub)
        raise HTTPException(status_code=500, detail="Could not save draft.")
    finally:
        db.close()


@router.delete("/api/apps/draft")
def discard_lab_draft(owner_sub: str = Depends(deps_owner_sub)):
    """Discard the caller's Lab New App draft (after a successful create)."""
    db = SessionLocal()
    try:
        deleted = (
            db.query(LabDraftRow)
            .filter(LabDraftRow.owner_sub == owner_sub)
            .delete(synchronize_session=False)
        )
        db.commit()
        return {"ok": True, "deleted": deleted}
    except Exception:
        db.rollback()
        logger.exception("discard_lab_draft failed for owner=%s", owner_sub)
        raise HTTPException(status_code=500, detail="Could not discard draft.")
    finally:
        db.close()


# ── CRUD endpoints ────────────────────────────────────────────────────────────

@router.post("/api/apps")
async def create_app(
    body: AppCreateRequest,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    gws_mode = bool(body.data_source_url and body.data_source_url.strip())
    description = body.description.strip()

    if not gws_mode and not description:
        raise HTTPException(status_code=422, detail="description is required.")
    if body.mode == "overnight":
        raise HTTPException(
            status_code=422,
            detail="Overnight Lab builds are not available in this release. Use standard Lab.",
        )
    if body.tech_stack == "static":
        raise HTTPException(
            status_code=422,
            detail="Static apps are not supported. All Lab apps use a Python backend.",
        )

    if gws_mode:
        # GWS path: no skill inference, no AI classification
        from app_classifier import AppClassification, SAFE_MODE, SAFE_TECH_STACK
        skill_ids: list[str] = []
        mode, tech_stack = SAFE_MODE, SAFE_TECH_STACK
        classification = AppClassification(
            mode=mode, tech_stack=tech_stack, skill_ids=[],
            confidence="high", reasoning="GWS sheet mode — skills disabled.",
        )
    else:
        mode, tech_stack, skill_ids, classification = resolve_app_config(
            name=body.name,
            description=description,
            explicit_mode=body.mode,
            explicit_tech_stack=body.tech_stack,
            explicit_skill_ids=body.skill_ids,
        )

    if mode != "standard":
        raise HTTPException(status_code=422, detail="mode must be 'standard'")
    if tech_stack not in APP_TECH_STACKS:
        raise HTTPException(status_code=422, detail=f"tech_stack must be one of {list(APP_TECH_STACKS)}")

    for slug_id in skill_ids:
        if not _SLUG_ALLOWLIST_RE.match(slug_id):
            raise HTTPException(status_code=422, detail=f"Invalid skill id: {slug_id!r}")

    meta = classification_meta_payload(
        classification, mode=mode, tech_stack=tech_stack, skill_ids=skill_ids,
    )
    if gws_mode:
        meta["gws_mode"] = True

    db = SessionLocal()
    try:
        slug = generate_slug(body.name, db)
        app = App(
            slug=slug,
            name=body.name.strip(),
            description=description,
            owner_sub=owner_sub,
            creator_email=_get_caller_email(request),
            tech_stack=tech_stack,
            mode=mode,
            skill_ids=json.dumps(skill_ids),
            classification_meta=json.dumps(meta, separators=(",", ":")),
        )
        db.add(app)
        db.commit()
        db.refresh(app)
        app_dir = _app_dir(app.id)
        app_dir.mkdir(parents=True, exist_ok=True)

        from lab_scaffold import seed_scaffold
        app_id_for_seed = app.id
        seeded = seed_scaffold(
            app_id_for_seed,
            tech_stack,
            app.name.strip(),
            lambda rel, content: _write_app_file(app_id_for_seed, rel, content),
        )
        if seeded:
            app.file_manifest = json.dumps(sorted(seeded))
            db.commit()
            db.refresh(app)

        logger.info(
            "Lab app created app_id=%s mode=%s tech_stack=%s skill_ids=%s confidence=%s gws_mode=%s",
            app.id, mode, tech_stack, skill_ids, classification.confidence, gws_mode,
        )
        app_id = app.id
    finally:
        db.close()

    if gws_mode:
        try:
            await _attach_google_drive_source(app_id, owner_sub, body.data_source_url)
        except Exception:
            logger.warning("create_app: failed to pre-attach GWS source for app_id=%s", app_id)

    from event_log import log_event as _log_event
    _log_event(owner_sub, _get_caller_email(request), "lab.app_created", {
        "mode": "gws" if gws_mode else "skills",
        "skill_count": len(skill_ids),
        "has_description": bool(description),
        "tech_stack": tech_stack,
    })

    db2 = SessionLocal()
    try:
        return _app_dict_with_env_keys(db2.get(App, app_id))
    finally:
        db2.close()


@router.get("/api/apps")
def list_apps(request: Request, owner_sub: str = Depends(deps_owner_sub)):
    """List apps the caller can see: their own, workspace-wide, or individually shared with them."""
    email = _get_caller_email(request).lower()
    db = SessionLocal()
    try:
        rows = (
            db.query(App)
            .filter(
                (App.owner_sub == owner_sub)
                | (App.visibility == "workspace")
                | (App.visibility == "shared")
            )
            .order_by(App.updated_at.desc())
            .all()
        )
        visible = []
        for a in rows:
            is_mine = a.owner_sub == owner_sub
            if is_mine or a.visibility == "workspace":
                visible.append((a, is_mine))
                continue
            if a.visibility == "shared" and email:
                allowed = [e.lower() for e in _load_json_list(a.allowed_users)]
                if email in allowed:
                    visible.append((a, is_mine))
        return [{**_app_dict_with_env_keys(a), "is_mine": is_mine} for a, is_mine in visible]
    finally:
        db.close()


# ── Pre-creation helpers (must come before /{app_id} routes) ─────────────────

@router.get("/api/apps/gws-preflight")
def gws_preflight(owner_sub: str = Depends(deps_owner_sub)):
    """Return whether the caller has connected Google Workspace."""
    from google_workspace_integration import fetch_google_credentials_for_sub
    return {"connected": fetch_google_credentials_for_sub(owner_sub) is not None}


class GwsValidateRequest(BaseModel):
    url_or_id: str = Field(..., min_length=3, max_length=2000)


@router.post("/api/apps/gws-validate-sheet")
async def gws_validate_sheet(body: GwsValidateRequest, owner_sub: str = Depends(deps_owner_sub)):
    """Validate that a Google Sheet URL is readable with the caller's GWS credentials."""
    from google_workspace_integration import (
        download_drive_file,
        fetch_google_credentials_for_sub,
        parse_drive_url,
        refresh_access_token,
    )

    creds_json = fetch_google_credentials_for_sub(owner_sub)
    if not creds_json:
        raise HTTPException(status_code=400, detail="Google Workspace not connected.")

    access_token = await refresh_access_token(creds_json)
    if not access_token:
        raise HTTPException(status_code=400, detail="Could not refresh Google credentials. Try reconnecting on the Integrations page.")

    file_id = parse_drive_url(body.url_or_id)
    if not file_id:
        raise HTTPException(status_code=422, detail="Invalid Google Sheet URL or ID.")

    try:
        downloaded = await download_drive_file(access_token, file_id, max_bytes=512 * 1024)
    except Exception as exc:
        msg = str(exc)
        if "403" in msg or "404" in msg or "not found" in msg.lower():
            raise HTTPException(
                status_code=400,
                detail="Sheet not found or outside your Google Drive. Check that it's in your own Drive (not a shared drive).",
            )
        raise HTTPException(status_code=400, detail="Could not read the sheet. Check the URL and try again.")

    return {
        "ok": True,
        "name": downloaded.name,
        "columns": downloaded.columns_preview,
        "row_estimate": downloaded.row_estimate,
    }


class ClassifyPreviewRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=MAX_APP_NAME)
    description: str = Field(..., min_length=1, max_length=MAX_APP_DESCRIPTION)


@router.post("/api/apps/classify-preview")
def classify_preview(body: ClassifyPreviewRequest, owner_sub: str = Depends(deps_owner_sub)):
    """Run the AI classifier and return suggested skill_ids and tech_stack."""
    from app_classifier import classify_new_app, resolve_app_config

    classification = classify_new_app(body.name, body.description)
    _, tech_stack, skill_ids, _ = resolve_app_config(
        name=body.name,
        description=body.description,
        explicit_mode=None,
        explicit_tech_stack=None,
        explicit_skill_ids=None,
    )
    return {
        "skill_ids": skill_ids or classification.skill_ids,
        "confidence": classification.confidence,
        "tech_stack": tech_stack,
        "reasoning": classification.reasoning,
    }


@router.get("/api/apps/{app_id}")
def get_app(app_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub)):
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        return _app_dict_with_env_keys(app)
    finally:
        db.close()


@router.put("/api/apps/{app_id}")
def update_app(
    app_id: str,
    body: AppUpdateRequest,
    owner_sub: str = Depends(deps_owner_sub),
):
    if body.visibility is not None and body.visibility not in APP_VISIBILITIES:
        raise HTTPException(status_code=422, detail=f"visibility must be one of {list(APP_VISIBILITIES)}")
    if body.skill_ids is not None:
        for slug_id in body.skill_ids:
            if not _SLUG_ALLOWLIST_RE.match(slug_id):
                raise HTTPException(status_code=422, detail=f"Invalid skill id: {slug_id!r}")
    if body.tech_stack is not None:
        if body.tech_stack not in ("python", "react"):
            raise HTTPException(status_code=422, detail="tech_stack must be 'python' or 'react'")
    if body.lab_mode is not None:
        from apps_models import LAB_MODES
        if body.lab_mode not in LAB_MODES:
            raise HTTPException(status_code=422, detail=f"lab_mode must be one of {list(LAB_MODES)}")

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        if body.name is not None:
            app.name = body.name.strip()
        if body.description is not None:
            app.description = body.description.strip() or None
        if body.visibility is not None:
            app.visibility = body.visibility
        if body.skill_ids is not None:
            app.skill_ids = json.dumps(body.skill_ids)
        if body.tech_stack is not None:
            app.tech_stack = body.tech_stack
        if body.lab_mode is not None:
            app.lab_mode = body.lab_mode
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
        db.refresh(app)
        return _app_dict_with_env_keys(app)
    finally:
        db.close()


@router.delete("/api/apps/{app_id}")
def delete_app(app_id: str, owner_sub: str = Depends(deps_owner_sub)):
    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        _delete_app_files(app.id)
        db.delete(app)
        db.commit()
        return {"ok": True}
    finally:
        db.close()


@router.put("/api/apps/{app_id}/secrets")
def upsert_secrets(
    app_id: str,
    body: SecretsUpsertRequest,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Merge the provided key→value pairs into the app's encrypted secret store.

    Values are Fernet-encrypted before storage; only key names are returned.
    Existing keys not present in the request body are preserved unchanged.
    """
    # Validate key names — only uppercase alphanumeric + underscore, max 128 chars
    _key_re = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
    for key in body.secrets:
        if not _key_re.match(key):
            raise HTTPException(
                status_code=422,
                detail=f"Invalid secret key {key!r}. Keys must be UPPER_SNAKE_CASE (A-Z0-9_).",
            )

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        existing = _decrypt_env_config(app.env_config)
        merged = {**existing, **body.secrets}
        app.env_config = _encrypt_env_config(merged)
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"env_var_keys": list(merged.keys())}
    finally:
        db.close()


@router.delete("/api/apps/{app_id}/secrets/{key}")
def delete_secret(
    app_id: str,
    key: str,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Remove a single key from the app's encrypted secret store."""
    # Validate key name matches the same allowlist as upsert
    _key_re = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
    if not _key_re.match(key):
        raise HTTPException(status_code=422, detail=f"Invalid secret key: {key!r}")

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        existing = _decrypt_env_config(app.env_config)
        if key not in existing:
            raise HTTPException(status_code=404, detail=f"Secret key not found: {key!r}")
        del existing[key]
        app.env_config = _encrypt_env_config(existing) if existing else None
        app.updated_at = datetime.now(timezone.utc)
        db.commit()
        return {"env_var_keys": list(existing.keys())}
    finally:
        db.close()


@router.get("/api/apps/{app_id}/files")
def list_files(app_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub)):
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        _check_read_access(db.get(App, app_id), owner_sub, email)
        return {"files": _list_app_files(app_id)}
    finally:
        db.close()


@router.get("/api/apps/{app_id}/files/{path:path}")
def get_file_content(app_id: str, path: str, request: Request, owner_sub: str = Depends(deps_owner_sub)):
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        _check_read_access(db.get(App, app_id), owner_sub, email)
        content = _read_app_file(app_id, path)
        return {"path": path, "content": content}
    finally:
        db.close()


@router.get("/api/apps/{app_id}/export.zip")
def export_app_zip(app_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub)):
    """Download all app source files as a ZIP archive."""
    import io
    import zipfile

    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        slug = app.slug or "lab-app"
    finally:
        db.close()

    paths = sorted(_list_app_files(app_id))
    if not paths:
        raise HTTPException(status_code=404, detail="No files to export.")

    max_files = 500
    max_bytes = 25 * 1024 * 1024
    total = 0
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in paths[:max_files]:
            if rel.startswith(".") or "/." in rel:
                continue
            try:
                raw = _read_app_file(app_id, rel).encode("utf-8")
            except HTTPException:
                continue
            total += len(raw)
            if total > max_bytes:
                raise HTTPException(status_code=413, detail="App too large to export as ZIP.")
            zf.writestr(rel, raw)
    buf.seek(0)
    from fastapi.responses import Response
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "-", slug).strip("-") or "lab-app"
    return Response(
        content=buf.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.zip"'},
    )


# ── Lab app records (GCS-backed JSON KV) ──────────────────────────────────────

@router.get("/api/apps/{app_id}/me")
async def get_app_me(
    app_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Return the current user's identity and role within this app (server-derived)."""
    from org_directory import get_employee_by_email

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        role = _app_role(app, owner_sub, email) or "member"
    finally:
        db.close()

    name = ""
    try:
        employee = await get_employee_by_email(email)
        if employee and employee.name:
            name = employee.name
    except Exception:
        pass

    return {
        "email": email,
        "name": name or email.split("@")[0],
        "role": role,
        "is_owner": app.owner_sub == owner_sub,
    }


@router.get("/api/apps/{app_id}/records/{collection}")
def list_app_records(
    app_id: str,
    collection: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
    limit: int = Query(50, ge=1, le=100),
    scope: str = Query("default", pattern="^(default|all)$"),
):
    """List documents in a collection (most recent up to limit)."""
    from lab_records import list_records

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        viewer = _record_viewer(app, owner_sub, email)
    finally:
        db.close()
    result = list_records(
        app.id,
        collection,
        read_file=_records_read_file,
        list_files=_list_app_files,
        limit=limit,
        viewer=viewer,
        scope_all=scope == "all",
    )
    return result


@router.get("/api/apps/{app_id}/records/{collection}/{key}")
def get_app_record(
    app_id: str,
    collection: str,
    key: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Fetch one document by key."""
    from lab_records import get_record, sanitize_record_for_response

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        viewer = _record_viewer(app, owner_sub, email)
    finally:
        db.close()
    record = get_record(
        app.id,
        collection,
        key,
        read_file=_records_read_file,
        viewer=viewer,
    )
    return sanitize_record_for_response(record)


@router.post("/api/apps/{app_id}/records/{collection}", status_code=201)
def append_app_record(
    app_id: str,
    collection: str,
    body: RecordDataBody,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Append a document. Auto-generates key unless body.key is provided (owner only)."""
    from lab_records import append_record

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found.")
        _check_record_post_access(app, owner_sub, email, collection)
        if body.key and app.owner_sub != owner_sub:
            raise HTTPException(status_code=403, detail="Only the app owner can set a custom record key.")
    finally:
        db.close()

    try:
        record = append_record(
            app_id,
            collection,
            body.data,
            actor=_record_actor(owner_sub, email),
            read_file=_records_read_file,
            write_file=_records_write_file,
            key=body.key,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    from event_log import log_event

    log_event(owner_sub, email, "lab.record_append", {
        "app_id": app_id,
        "collection": collection,
        "key": record.get("key"),
    })
    return record


@router.put("/api/apps/{app_id}/records/{collection}/{key}")
def upsert_app_record(
    app_id: str,
    collection: str,
    key: str,
    body: RecordDataBody,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Create or replace a document (owner only; not allowed on append-only collections)."""
    from lab_records import upsert_record

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found.")
        _check_record_owner_write(app, owner_sub)
    finally:
        db.close()

    try:
        record = upsert_record(
            app_id,
            collection,
            key,
            body.data,
            actor=_record_actor(owner_sub, email),
            read_file=_records_read_file,
            write_file=_records_write_file,
        )
    except ValueError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc

    from event_log import log_event

    log_event(owner_sub, email, "lab.record_upsert", {
        "app_id": app_id,
        "collection": collection,
        "key": key,
    })
    return record


@router.delete("/api/apps/{app_id}/records/{collection}/{key}", status_code=204)
def delete_app_record(
    app_id: str,
    collection: str,
    key: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Delete a document (owner only)."""
    from lab_records import delete_record

    email = _get_caller_email(request)
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            raise HTTPException(status_code=404, detail="App not found.")
        _check_record_owner_write(app, owner_sub)
    finally:
        db.close()

    delete_record(app_id, collection, key, delete_file=_records_delete_file)

    from event_log import log_event

    log_event(owner_sub, email, "lab.record_delete", {
        "app_id": app_id,
        "collection": collection,
        "key": key,
    })
    return Response(status_code=204)


@router.put("/api/apps/{app_id}/record-collections/{collection}")
def update_record_collection_scope(
    app_id: str,
    collection: str,
    body: RecordCollectionScopeBody,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Set read_scope for a collection (owner only)."""
    from lab_records import set_collection_read_scope

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
    finally:
        db.close()
    return set_collection_read_scope(
        app.id,
        collection,
        body.read_scope,
        read_file=_records_read_file,
        write_file=_records_write_file,
    )


# ── Google Drive data sources ─────────────────────────────────────────────────

class GoogleDriveAttachRequest(BaseModel):
    url_or_id: str = Field(..., min_length=3, max_length=2000)


@router.get("/api/apps/{app_id}/google-workspace/status")
def google_workspace_status(
    app_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Return whether the current user has connected Google Workspace."""
    from google_workspace_integration import fetch_google_credentials_for_sub

    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        _check_read_access(db.get(App, app_id), owner_sub, email)
    finally:
        db.close()
    return {"connected": fetch_google_credentials_for_sub(owner_sub) is not None}


@router.get("/api/apps/{app_id}/data-sources")
def list_data_sources(
    app_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """List attached Google Drive data sources (metadata only)."""
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        _check_read_access(db.get(App, app_id), owner_sub, email)
    finally:
        db.close()
    return {"sources": _read_data_manifest(app_id)}


@router.post("/api/apps/{app_id}/data-sources/google-drive")
async def attach_google_drive(
    app_id: str,
    body: GoogleDriveAttachRequest,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Download a Google Sheet or Doc and persist as a local data snapshot."""
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        _ensure_owner(db.get(App, app_id), owner_sub)
    finally:
        db.close()

    entry = await _attach_google_drive_source(app_id, owner_sub, body.url_or_id)

    db2 = SessionLocal()
    try:
        app = db2.get(App, app_id)
        if app:
            _update_manifest(app, db2)
    finally:
        db2.close()

    return {"ok": True, "source": entry}


@router.delete("/api/apps/{app_id}/data-sources/google-drive/{file_id}")
def detach_google_drive(
    app_id: str,
    file_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Remove an attached Google Drive data source."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", file_id):
        raise HTTPException(status_code=422, detail="Invalid file ID.")

    db = SessionLocal()
    try:
        _ensure_owner(db.get(App, app_id), owner_sub)
    finally:
        db.close()

    manifest = _read_data_manifest(app_id)
    entry = next((e for e in manifest if e.get("file_id") == file_id), None)
    if not entry:
        raise HTTPException(status_code=404, detail="Data source not found.")

    for rel in (
        entry.get("path"),
        f"data/sources/{file_id}.meta.json",
        f"data/sources/{file_id}.csv",
        f"data/sources/{file_id}.txt",
        f"data/sources/{file_id}.xlsx",
    ):
        if rel:
            _delete_app_file(app_id, rel)

    _write_data_manifest(app_id, [e for e in manifest if e.get("file_id") != file_id])
    _invalidate_live_data_cache(app_id, file_id)

    db2 = SessionLocal()
    try:
        app = db2.get(App, app_id)
        if app:
            _update_manifest(app, db2)
    finally:
        db2.close()

    return {"ok": True}


@router.get("/api/apps/{app_id}/live-data/{file_id}")
async def get_live_data(
    app_id: str,
    file_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
    refresh: bool = Query(False, description="Bypass server cache and re-fetch from Google"),
):
    """Return fresh Google Sheet/Doc data for a dashboard (uses app owner's GWS creds)."""
    if not re.match(r"^[a-zA-Z0-9_-]+$", file_id):
        raise HTTPException(status_code=422, detail="Invalid file ID.")

    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        owner_sub_for_google = app.owner_sub
    finally:
        db.close()

    return await _fetch_live_google_data(
        app_id,
        owner_sub_for_google,
        file_id,
        bypass_cache=refresh,
    )


# ── AI Builder (SSE) ──────────────────────────────────────────────────────────

def _build_system_prompt(app: App, existing_files: list[str]) -> str:
    """Build the system prompt for the AI developer agent."""
    from lab_prompts import build_system_prompt

    skill_blocks = ""
    for slug in _load_json_list(app.skill_ids):
        content = _load_skill(slug)
        if content:
            skill_blocks += f"\n\n---\n\n## Attached skill: {slug}\n\n{content}"

    all_env_keys: list[str] = []
    for slug in _load_json_list(app.skill_ids):
        secrets = _load_skill_secrets(slug)
        all_env_keys.extend(k for k in secrets if k not in all_env_keys)
    app_secrets = _decrypt_env_config(app.env_config)
    for k in app_secrets:
        if k not in all_env_keys:
            all_env_keys.append(k)
    env_keys_block = ""
    if all_env_keys:
        keys_str = ", ".join(all_env_keys)
        env_keys_block = (
            f'\n\nAvailable environment variables '
            f'(access via os.environ.get("KEY", "") inside functions): {keys_str}'
        )

    low_confidence_note = ""
    chat_history = _load_json_list(app.chat_history)
    lab_meta = _load_classification_meta(app)
    if not chat_history and lab_meta.get("classification_confidence") == "low":
        low_confidence_note = (
            "\n\nIMPORTANT — FIRST REPLY: The description was vague. Ask ONE short "
            "clarifying question about scope or requirements before building."
        )

    gws_block = ""
    data_manifest = _read_data_manifest(app.id)
    if data_manifest:
        source_lines = []
        for entry in data_manifest:
            cols = entry.get("columns_preview") or []
            cols_str = ", ".join(cols[:8]) if cols else "(probe to discover)"
            trunc = " [TRUNCATED]" if entry.get("truncated") else ""
            source_lines.append(
                f"- **{entry.get('name', entry.get('file_id'))}** "
                f"({entry.get('source_type', 'file')}) → `{entry.get('path')}` "
                f"~{entry.get('row_estimate', '?')} rows{trunc}; columns preview: {cols_str}"
            )
        gws_block = (
            "\n\n---\n\n## Attached Google Drive data sources\n\n"
            + "\n".join(source_lines)
            + _LAB_DATA_ANALYSIS_PROMPT
            + _LAB_LIVE_DATA_PROMPT.replace("<APP_UUID>", app.id)
        )
    else:
        from google_workspace_integration import fetch_google_credentials_for_sub
        if not fetch_google_credentials_for_sub(app.owner_sub):
            gws_block = (
                "\n\n---\n\n## Google Workspace\n\n"
                "To build dashboards from Google Sheets, connect Google Workspace "
                "on /integrations, then attach a Drive URL via the Data panel."
            )

    stack_rule_extra = (
        f"\n\nAPI EXPLORATION (mandatory when skills attached):\n"
        f"Before writing server.py, use run_python_snippet to probe each API endpoint.\n"
        f"App id for fetch URLs: {app.id}\n"
        f"POST /api/apps/{app.id}/run — body: {{\"function\": \"fn_name\", \"payload\": {{...}}}}"
    )

    prompt = build_system_prompt(
        app,
        existing_files,
        skill_blocks=skill_blocks + stack_rule_extra,
        env_keys_block=env_keys_block,
        gws_block=gws_block,
        low_confidence_note=low_confidence_note,
    )
    return prompt.replace("{app_id}", app.id)


def _sse_event(event_type: str, data: dict) -> str:
    """Format a single SSE event."""
    return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"


async def _run_snippet_for_builder(app_id: str, code: str) -> str:
    """Execute a Python snippet in a gVisor sandbox with app env vars injected.

    Used by the AI builder's run_python_snippet tool so it can probe live APIs
    or analyze attached Google Drive CSV data before writing server.py code.
    stdout is returned (max 8 KB).
    """
    import tempfile
    from executor import execute_in_sandbox  # type: ignore[import]

    merged_secrets: dict[str, str] = {}
    db2 = SessionLocal()
    try:
        run_app = db2.get(App, app_id)
        if run_app:
            for slug in _load_json_list(run_app.skill_ids):
                merged_secrets.update(_load_skill_secrets(slug))
            merged_secrets.update(_decrypt_env_config(run_app.env_config))
    finally:
        db2.close()

    data_files = _load_app_data_files(app_id)
    file_prolog = _build_file_injection_prolog(data_files, _LAB_SNIPPET_DATA_ROOT)
    full_code = file_prolog + code if file_prolog else code
    merged_secrets.update(_snippet_env_with_data_dir())

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"lab_snip_{app_id[:8]}_"))
    try:
        result = await execute_in_sandbox(
            full_code,
            str(tmp_dir),
            env_extras=merged_secrets or None,
            timeout=20,
            pool="lab-python-executor-pool",
            template="lab-python-executor",
        )
        out = (result.get("stdout") or "").strip()
        err = (result.get("stderr") or "").strip()
        if err and not out:
            return f"[stderr]\n{err[:4096]}"
        if err:
            return f"{out[:4000]}\n[stderr]\n{err[:1000]}"
        return out[:8192] if out else "(no output)"
    except Exception as exc:
        return f"[error] {exc}"
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


def _serialize_block(block) -> dict:
    """Serialize a content block to a minimal API-safe dict.

    Thin wrapper over the shared implementation. Previously this stringified
    any unrecognised block, which turned Sonnet 5's unsolicited ``thinking``
    blocks into visible text and dropped their signature.
    """
    return serialize_assistant_block(block)


def _coalesce_saved_history(history: list) -> list[dict]:
    """Merge consecutive same-role messages; keep UI metadata like ``file_edits``."""
    coalesced: list[dict] = []
    for msg in history:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in ("user", "assistant") or not isinstance(content, str):
            continue
        text = content.strip()
        if not text:
            continue
        entry: dict = {"role": role, "content": text}
        if msg.get("file_edits"):
            entry["file_edits"] = msg["file_edits"]
        if coalesced and coalesced[-1]["role"] == role:
            prev = coalesced[-1]
            prev["content"] = prev["content"] + "\n\n" + text
            if entry.get("file_edits"):
                prev["file_edits"] = entry["file_edits"]
        else:
            coalesced.append(entry)
    return coalesced


def _blocks_from_timeline(timeline: list[dict]) -> list[dict]:
    """Build ordered UI blocks (text / file_edit / warning) from a build timeline."""
    blocks: list[dict] = []
    text_parts: list[str] = []

    def flush_text() -> None:
        combined = "".join(text_parts).strip()
        text_parts.clear()
        if combined:
            blocks.append({"type": "text", "content": combined})

    for entry in timeline:
        ev = entry.get("event")
        if ev == "text" and entry.get("delta"):
            text_parts.append(str(entry["delta"]))
        elif ev == "file_edit" and entry.get("path"):
            flush_text()
            blocks.append({
                "type": "file_edit",
                "path": entry["path"],
                "kind": entry.get("kind", "write"),
                "additions": entry.get("additions", 0),
                "deletions": entry.get("deletions", 0),
                "preview": entry.get("preview") or [],
            })
        elif ev == "verify_fail":
            flush_text()
            errors = entry.get("errors") or []
            if errors:
                msg = "Verification failed after build. Fix these issues:\n" + "\n".join(
                    f"- {e}" for e in errors
                )
                blocks.append({"type": "warning", "content": msg})
        elif ev == "warning" and entry.get("message"):
            flush_text()
            blocks.append({"type": "warning", "content": str(entry["message"])})
        elif ev == "error" and entry.get("message"):
            flush_text()
            blocks.append({"type": "error", "content": str(entry["message"])})

    flush_text()
    return blocks


def _attach_turn_blocks_to_history(app_id: str) -> None:
    """Persist chronological content_blocks on the latest assistant turn."""
    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        if not app:
            return
        blocks = _blocks_from_timeline(_load_build_timeline(app))
        if not blocks:
            return
        history = _load_json_list(app.chat_history)
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "assistant":
                history[i] = {**history[i], "content_blocks": blocks}
                app.chat_history = json.dumps(history)
                db.commit()
                return
        text_only = "\n\n".join(
            b["content"] for b in blocks if b.get("type") in ("text", "warning", "error")
        )
        history.append({
            "role": "assistant",
            "content": text_only or "Build finished.",
            "content_blocks": blocks,
        })
        app.chat_history = json.dumps(history)
        db.commit()
    except Exception:
        logger.exception("Failed to attach content_blocks for app %s", app_id)
        db.rollback()
    finally:
        db.close()


def _prepare_chat_history_for_llm(history: list) -> list[dict]:
    """Strip UI metadata and coalesce consecutive same-role turns for LLM APIs.

    Saved Lab chat history may include ``file_edits`` and multiple back-to-back
    user or assistant strings (multi-turn builds save one assistant blob per
    tool round). Anthropic requires alternating roles.
    """
    result = [
        {"role": m["role"], "content": m["content"]}
        for m in _coalesce_saved_history(history)
    ]
    if result and result[0]["role"] != "user":
        result.insert(0, {"role": "user", "content": "(continued)"})
    return result


def _compress_history(
    messages: list[dict],
    *,
    file_edits: list[dict] | None = None,
) -> list[dict]:
    """Strip intermediate tool_use / tool_result rounds from message history.

    Only user text messages and the final text-only assistant responses are kept.
    This avoids replaying tool_use blocks that require matching tool_results and
    keeps the saved history concise for future turns.

    Kept format:
        {"role": "user",      "content": "<plain text>"}
        {"role": "assistant", "content": "<plain text>"}
    """
    clean: list[dict] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content")

        if role == "user" and isinstance(content, str):
            clean.append({"role": "user", "content": content})

        elif role == "assistant" and isinstance(content, list):
            # Collect only text blocks; skip tool_use blocks
            texts = [
                b.get("text", "") or ""
                for b in content
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            combined = "\n".join(t for t in texts if t).strip()
            if combined:
                clean.append({"role": "assistant", "content": combined})

        elif role == "assistant" and isinstance(content, str):
            if content.strip():
                clean.append({"role": "assistant", "content": content})

        # user + list content == tool_result messages — intentionally skipped

    if file_edits:
        for i in range(len(clean) - 1, -1, -1):
            if clean[i].get("role") == "assistant":
                clean[i] = {**clean[i], "file_edits": file_edits}
                break

    # Multi-turn builds append one assistant entry per tool round — coalesce.
    return _coalesce_saved_history(clean)[-20:]


async def _run_plan_background(
    app_id: str,
    message: str,
    history: list[dict],
    owner_sub: str,
) -> None:
    """Background task: plan-mode conversation (no code writing)."""

    async def on_event(event: str, data: dict) -> None:
        _append_build_timeline_event(app_id, event, data)

    try:
        _active_lab_builds.add(app_id)
        from lab_plan_chat import run_plan_chat

        result = await run_plan_chat(app_id, message, history, owner_sub, on_event)
        if result.success:
            await on_event("done", {"plan_ready": result.plan_ready})
            _set_build_phase(app_id, "completed")
            if result.plan_ready:
                await _start_agent_implementation(app_id, owner_sub, message)
        else:
            _set_build_phase(app_id, "failed")
    except Exception:
        logger.exception("Lab plan background failed for app %s", app_id)
        _append_build_timeline_event(
            app_id, "error", {"message": "An error occurred while planning."},
        )
        _set_build_phase(app_id, "failed")
    finally:
        _active_lab_builds.discard(app_id)


async def _start_agent_implementation(
    app_id: str,
    owner_sub: str,
    original_message: str = "",
) -> None:
    """Switch to agent mode and run the approved plan."""

    from lab_plan import set_lab_mode

    set_lab_mode(app_id, "agent")
    _append_build_timeline_event(app_id, "mode_switched", {"lab_mode": "agent"})

    async def on_event(event: str, data: dict) -> None:
        _append_build_timeline_event(app_id, event, data)

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        tech_stack = (app.tech_stack if app else "python") or "python"
        app_slug = app.slug if app else ""
    finally:
        db.close()

    try:
        _active_lab_builds.add(app_id)
        _set_build_phase(app_id, "building")

        from lab_builder import run_planned_implementation

        result = await run_planned_implementation(
            app_id, owner_sub, on_event, original_message=original_message,
        )
        final_text, files_written, ok = result.final_text, result.files_written, result.success

        if ok and files_written:
            from lab_verify import verify_lab_app
            verify = await verify_lab_app(
                app_id, tech_stack, app_slug, skip_build=(tech_stack == "react"),
            )
            if verify["ok"]:
                await on_event("verify_ok", {})
            else:
                await on_event("verify_fail", {"errors": verify["errors"]})

        if ok:
            await on_event("done", {"files_written": files_written})
            _set_build_phase(app_id, "completed")
        else:
            _set_build_phase(app_id, "failed")
    except Exception:
        logger.exception("Lab agent implementation failed for app %s", app_id)
        _set_build_phase(app_id, "failed")
    finally:
        _active_lab_builds.discard(app_id)


async def _execute_builder(
    app_id: str,
    message: str,
    history: list[dict],
    owner_sub: str,
    on_event: Callable[[str, dict], Awaitable[None]],
) -> tuple[str, list[str], bool]:
    """Run the AI builder; emit events via on_event.

    Returns (final_text, files_written, success).
    """
    from lab_builder import run_lab_builder

    result = await run_lab_builder(app_id, message, history, owner_sub, on_event)
    return result.final_text, result.files_written, result.success


async def _run_builder_background(
    app_id: str,
    message: str,
    history: list[dict],
    owner_sub: str,
) -> None:
    """Background task: runs builder, verify loop, and persists timeline events."""

    async def on_event(event: str, data: dict) -> None:
        _append_build_timeline_event(app_id, event, data)

    db = SessionLocal()
    try:
        app = db.get(App, app_id)
        tech_stack = (app.tech_stack if app else "python") or "python"
        app_slug = app.slug if app else ""
    finally:
        db.close()

    try:
        _active_lab_builds.add(app_id)
        final_text, files_written, ok = await _execute_builder(
            app_id, message, history, owner_sub, on_event,
        )

        if ok and files_written:
            from lab_verify import verify_lab_app

            verify = await verify_lab_app(
                app_id, tech_stack, app_slug, skip_build=(tech_stack == "react"),
            )
            if verify["ok"]:
                await on_event("verify_ok", {})
            else:
                await on_event("verify_fail", {"errors": verify["errors"]})
                repair_msg = (
                    "Verification failed after build. Fix these issues:\n"
                    + "\n".join(f"- {e}" for e in verify["errors"])
                )
                db2 = SessionLocal()
                try:
                    app_row = db2.get(App, app_id)
                    repair_history = _load_json_list(app_row.chat_history) if app_row else history
                finally:
                    db2.close()
                repair_text, repair_files, repair_ok = await _execute_builder(
                    app_id, repair_msg, repair_history, owner_sub, on_event,
                )
                if repair_ok:
                    final_text = (final_text + "\n\n" + repair_text).strip()
                    files_written = list(dict.fromkeys(files_written + repair_files))
                    reverify = await verify_lab_app(
                        app_id, tech_stack, app_slug, skip_build=(tech_stack == "react"),
                    )
                    if reverify["ok"]:
                        await on_event("verify_ok", {"repaired": True})
                    else:
                        await on_event("verify_fail", {"errors": reverify["errors"], "repaired": True})
                ok = repair_ok

        if ok:
            from event_log import log_event as _log_event
            _log_event(owner_sub, "", "lab.build_completed", {
                "app_id": app_id,
                "tech_stack": tech_stack,
                "files_count": len(files_written),
            })
            await on_event("done", {"files_written": files_written})
            _set_build_phase(app_id, "completed")
            if not final_text and files_written:
                logger.info(
                    "Lab build completed for app %s with %d files, no assistant text",
                    app_id, len(files_written),
                )
        else:
            _set_build_phase(app_id, "failed")
    except Exception:
        logger.exception("Lab background build failed for app %s", app_id)
        _append_build_timeline_event(
            app_id, "error", {"message": "An error occurred while building the app."},
        )
        _set_build_phase(app_id, "failed")
    finally:
        _attach_turn_blocks_to_history(app_id)
        _active_lab_builds.discard(app_id)


@router.post("/api/apps/{app_id}/chat", status_code=202)
async def app_chat(
    app_id: str,
    body: ChatRequest,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Start a background plan or agent turn. Poll GET /api/apps/{id}/build for progress."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        if app.build_phase == "building" and not _is_build_stale(app):
            raise HTTPException(status_code=409, detail="A build is already in progress.")

        try:
            history = json.loads(app.chat_history or "[]")
        except (ValueError, TypeError):
            history = []
        if not isinstance(history, list):
            history = []

        history = _coalesce_saved_history(history)

        worker_message = body.message
        if (
            app.build_phase == "failed"
            and history
            and history[-1].get("role") == "user"
            and isinstance(history[-1].get("content"), str)
        ):
            # Failed build left a dangling user turn — merge retry into it.
            prior = history[-1]["content"].strip()
            retry = body.message.strip()
            history[-1] = {
                "role": "user",
                "content": f"{prior}\n\n{retry}" if retry and retry != prior else prior,
            }
            worker_message = history[-1]["content"]
            history_for_worker = history[:-1]
        else:
            history.append({"role": "user", "content": body.message})
            history_for_worker = history[:-1]

        app.chat_history = json.dumps(history)
        app.build_phase = "building"
        app.build_timeline = "[]"
        app.build_updated_at = now
        db.commit()

        lab_mode = getattr(app, "lab_mode", "plan") or "plan"
    finally:
        db.close()

    if lab_mode == "plan":
        asyncio.create_task(
            _run_plan_background(
                app_id=app_id,
                message=worker_message,
                history=history_for_worker,
                owner_sub=owner_sub,
            )
        )
    else:
        asyncio.create_task(
            _run_builder_background(
                app_id=app_id,
                message=worker_message,
                history=history_for_worker,
                owner_sub=owner_sub,
            )
        )
    return JSONResponse(
        status_code=202,
        content={"status": "accepted", "build_phase": "building", "lab_mode": lab_mode},
    )


@router.put("/api/apps/{app_id}/lab-mode")
async def set_app_lab_mode(
    app_id: str,
    body: LabModeRequest,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Switch between plan and agent mode. Switching to agent starts implementation if plan is ready."""
    from apps_models import LAB_MODES
    from lab_plan import is_plan_ready

    if body.lab_mode not in LAB_MODES:
        raise HTTPException(status_code=422, detail=f"lab_mode must be one of {list(LAB_MODES)}")

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        meta = _load_classification_meta(app)
        app.lab_mode = body.lab_mode
        db.commit()
        plan_ready = is_plan_ready(meta)
    finally:
        db.close()

    if body.lab_mode == "agent" and plan_ready:
        asyncio.create_task(_start_agent_implementation(app_id, owner_sub))

    return {"ok": True, "lab_mode": body.lab_mode, "plan_ready": plan_ready}


@router.post("/api/apps/{app_id}/start-build", status_code=202)
async def start_build_from_plan(
    app_id: str,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Approve the plan and switch to agent mode to begin implementation."""
    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        meta = _load_classification_meta(app)
        if not meta.get("build_plan"):
            raise HTTPException(status_code=422, detail="No plan exists yet. Stay in plan mode and refine the plan first.")
        if app.build_phase == "building" and not _is_build_stale(app):
            raise HTTPException(status_code=409, detail="A build is already in progress.")
        from lab_plan import set_plan_ready
        set_plan_ready(app_id, True)
    finally:
        db.close()

    asyncio.create_task(_start_agent_implementation(app_id, owner_sub))
    return JSONResponse(status_code=202, content={"status": "accepted", "lab_mode": "agent"})


@router.get("/api/apps/{app_id}/build")
async def get_build_status(
    app_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Poll in-progress or completed standard Lab build state."""
    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        if app.build_phase == "building" and _is_build_stale(app):
            app.build_phase = "failed"
            timeline = _load_build_timeline(app)
            timeline.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "error",
                "message": "Build timed out. You can start a new build.",
            })
            app.build_timeline = json.dumps(timeline[-_MAX_BUILD_TIMELINE:])
            app.build_updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
            db.commit()
        return _build_status_dict(app)
    finally:
        db.close()


# ── Python API runner ─────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    function: str = Field(..., min_length=1, max_length=120)
    payload: dict = Field(default_factory=dict)


_RUN_ALLOWED_STACKS = frozenset({"python", "react"})


def _check_app_allows_run(app: App) -> None:
    """React apps may ship optional server.py at app root; vanilla/python always may."""
    if (getattr(app, "tech_stack", None) or "python") not in _RUN_ALLOWED_STACKS:
        raise HTTPException(status_code=400, detail="This app does not have a Python backend.")


@router.post("/api/apps/{app_id}/run")
async def run_app_function(
    app_id: str,
    body: RunRequest,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Execute a named Python function from the app's server.py in a gVisor sandbox."""
    import tempfile
    from executor import execute_in_sandbox  # type: ignore[import]

    email = getattr(getattr(request.state, "auth", None), "email", "") or ""
    db = SessionLocal()
    try:
        app = _check_read_access(db.get(App, app_id), owner_sub, email)
        _check_app_allows_run(app)
    finally:
        db.close()

    # Resolve server.py — GCS is source of truth; fall back to local disk
    py_files = _load_app_py_files(app_id)
    server_content = py_files.get("server.py")
    if server_content is None:
        raise HTTPException(status_code=404, detail="server.py not found in app directory.")

    func_name = _normalize_run_function_name(body.function)
    if not func_name:
        raise HTTPException(status_code=422, detail="Invalid function name.")

    payload_json = json.dumps(body.payload)

    # Collect merged secrets (skill secrets overridden by per-app secrets)
    merged_secrets: dict[str, str] = {}
    db2 = SessionLocal()
    try:
        run_app = db2.get(App, app_id)
        if run_app:
            for slug in _load_json_list(run_app.skill_ids):
                merged_secrets.update(_load_skill_secrets(slug))
            merged_secrets.update(_decrypt_env_config(run_app.env_config))
    finally:
        db2.close()

    import base64 as _b64
    from lab_records_sandbox import (
        PLATFORM_RECORDS_MODULE,
        apply_sandbox_record_ops,
        build_records_snapshot,
        extract_record_ops,
        snapshot_env_value,
    )

    py_files_b64 = {
        name: _b64.b64encode(content.encode("utf-8")).decode()
        for name, content in py_files.items()
    }
    py_files_b64["platform_records.py"] = _b64.b64encode(
        PLATFORM_RECORDS_MODULE.encode("utf-8"),
    ).decode()

    data_files = _load_app_data_files(app_id)
    file_prolog = _build_file_injection_prolog(data_files, _LAB_RUN_DATA_ROOT)
    merged_secrets["LAB_DATA_DIR"] = f"{_LAB_RUN_DATA_ROOT}/data/sources"
    records_snapshot = build_records_snapshot(
        app_id,
        read_file=_records_read_file,
        list_files=_list_app_files,
        viewer=_record_viewer(app, owner_sub, email),
    )
    merged_secrets["LAB_RECORDS_SNAPSHOT"] = snapshot_env_value(records_snapshot)

    tmp_dir = Path(tempfile.mkdtemp(prefix=f"lab_{app_id[:8]}_"))
    try:
        runner_code = file_prolog + f"""
import sys, json, inspect, os, base64
try:
    _srv_dir = '/tmp/_lab_srv'
    os.makedirs(_srv_dir, exist_ok=True)
    for _name, _b64data in {repr(py_files_b64)}.items():
        with open(os.path.join(_srv_dir, _name), 'wb') as _f:
            _f.write(base64.b64decode(_b64data))
    sys.path.insert(0, _srv_dir)
    import server as _srv
    fn = getattr(_srv, {repr(func_name)}, None)
    if fn is None:
        print(json.dumps({{"error": "Function not found: {func_name}"}}))
    else:
        payload = json.loads({repr(payload_json)})
        result = fn(payload) if inspect.signature(fn).parameters else fn()
        print(json.dumps(result) if not isinstance(result, str) else result)
except Exception as _exc:
    print(json.dumps({{
        "error": "server execution failed",
        "detail": str(_exc)[:500],
    }}))
"""
        try:
            result = await execute_in_sandbox(
                runner_code,
                str(tmp_dir),
                env_extras=merged_secrets or None,
                pool="lab-python-executor-pool",
                template="lab-python-executor",
                max_stdout=512_000,  # Lab functions can return large JSON payloads
            )
        except asyncio.TimeoutError:
            raise HTTPException(status_code=408, detail="Function timed out.")
        except Exception as exc:
            logger.exception("Lab /run sandbox error for app %s", app_id)
            raise HTTPException(status_code=500, detail=f"Sandbox execution error: {exc}")

        output = (result.get("stdout") or "").strip()
        stderr = (result.get("stderr") or "").strip()
        output, record_ops = extract_record_ops(output)
        if record_ops:
            apply_sandbox_record_ops(
                app_id,
                record_ops,
                owner_sub=owner_sub,
                email=email,
                app_owner_sub=app.owner_sub,
                app_visibility=app.visibility or "private",
                app_allowed_users=_load_json_list(app.allowed_users),
                app_admin_users=_load_json_list(getattr(app, "admin_users", None)),
                read_file=_records_read_file,
                write_file=_records_write_file,
            )
        parsed, parse_err = _parse_sandbox_json_stdout(output)
        if parsed is not None:
            if parsed.get("error"):
                logger.warning(
                    "Lab /run app=%s func=%s error=%s stderr=%s",
                    app_id, func_name, parsed.get("error"), stderr[:300],
                )
            return JSONResponse(content=parsed)
        logger.warning(
            "Lab /run app=%s func=%s parse_err=%s stdout=%s stderr=%s",
            app_id, func_name, parse_err, output[:300], stderr[:300],
        )
        return JSONResponse(content={"error": parse_err or "invalid response", "output": output, "stderr": stderr})
    finally:
        shutil.rmtree(str(tmp_dir), ignore_errors=True)


# ── App sharing ───────────────────────────────────────────────────────────────

@router.get("/api/apps/{app_id}/sharing")
def get_sharing(app_id: str, owner_sub: str = Depends(deps_owner_sub)):
    """Return the current sharing config for an app (owner only)."""
    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        return {
            "visibility":    app.visibility,
            "allowed_users": _load_json_list(app.allowed_users),
            "admin_users":   _load_json_list(getattr(app, "admin_users", None)),
        }
    finally:
        db.close()


def _normalize_email_list(raw: list[str]) -> list[str]:
    import re as _re
    _EMAIL_RE = _re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    emails: list[str] = []
    for e in raw:
        e = e.strip().lower()
        if e and _EMAIL_RE.match(e):
            emails.append(e)
    return emails


@router.put("/api/apps/{app_id}/sharing")
def update_sharing(
    app_id: str,
    body: AppSharingRequest,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Update visibility and/or allowed_users list (owner only)."""
    if body.visibility not in APP_VISIBILITIES:
        raise HTTPException(status_code=422, detail=f"visibility must be one of {list(APP_VISIBILITIES)}")

    emails = _normalize_email_list(body.allowed_users)
    admin_emails = _normalize_email_list(body.admin_users)

    if body.visibility == "shared":
        email_set = set(emails)
        extra_admins = [e for e in admin_emails if e not in email_set]
        if extra_admins:
            raise HTTPException(
                status_code=422,
                detail="admin_users must be a subset of allowed_users when visibility is shared.",
            )

    db = SessionLocal()
    try:
        app = _ensure_owner(db.get(App, app_id), owner_sub)
        app.visibility    = body.visibility
        app.allowed_users = json.dumps(emails)
        app.admin_users   = json.dumps(admin_emails)
        from datetime import datetime
        app.updated_at = datetime.utcnow()
        db.commit()
        return {
            "visibility": app.visibility,
            "allowed_users": emails,
            "admin_users": admin_emails,
        }
    finally:
        db.close()


# ── Employee search (configured org directory) ────────────────────────────────

@router.get("/api/employees/search")
async def search_employees(
    q: str = "",
    limit: int = 10,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Search people by name or email via the configured directory provider."""
    from org_directory import get_all_employees

    needle = q.strip().lower()
    if len(needle) < 2:
        return {"employees": []}

    limit = min(max(limit, 1), 50)
    directory_provider = (os.environ.get("DIRECTORY_PROVIDER", "claims") or "claims").strip()
    try:
        people = await get_all_employees()
    except Exception:
        logger.exception("employee search failed")
        return {
            "employees": [],
            "directory_provider": directory_provider,
            "directory_hint": _employee_search_hint(directory_provider),
        }

    matches = []
    for person in people:
        if not person.is_active:
            continue
        hay = f"{person.name} {person.email}".lower()
        if needle not in hay:
            continue
        matches.append({
            "name": person.name or person.email,
            "email": person.email.lower(),
            "job_title": person.title or "",
            "department": person.department or person.team or "",
            "status": person.status or "active",
        })
        if len(matches) >= limit:
            break
    return {
        "employees": matches,
        "directory_provider": directory_provider,
        "directory_hint": _employee_search_hint(directory_provider) if not matches else None,
    }


def _employee_search_hint(directory_provider: str) -> str:
    if directory_provider == "file":
        return (
            "No matches in the uploaded directory file. Update it in "
            "Settings → Platform setup, or paste email addresses directly."
        )
    if directory_provider == "claims":
        return (
            "Directory is claims-based: only people who have signed in appear here. "
            "Paste email addresses directly, or switch to a file/HRIS directory in "
            "Platform setup."
        )
    return "No matches. Paste email addresses directly if directory search is unavailable."


# ── Static file serving (/apps/{slug}/) ──────────────────────────────────────

_ALLOWED_EXTENSIONS = {
    ".html", ".htm", ".css", ".js", ".mjs", ".json", ".svg",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".woff",
    ".woff2", ".ttf", ".eot", ".map", ".txt",
}


def _get_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    return mime or "application/octet-stream"


@router.get("/apps/{slug}")
@router.get("/apps/{slug}/")
async def serve_app_index(slug: str, request: Request):
    """Serve the index.html for an app slug. Requires CF Access JWT."""
    return await _serve_app_file_inner(slug, "index.html", request)


@router.get("/apps/{slug}/{path:path}")
async def serve_app_file(slug: str, path: str, request: Request):
    """Serve any static file for a deployed app. Requires CF Access JWT."""
    return await _serve_app_file_inner(slug, path, request)


async def _serve_app_file_inner(slug: str, rel_path: str, request: Request):
    # Auth is already verified by the CF Access middleware at request.state.auth.
    auth = getattr(request.state, "auth", None)
    if auth is None:
        raise HTTPException(status_code=401, detail="Not authenticated.")

    owner_sub = getattr(auth, "sub", None) or ""
    email = getattr(auth, "email", "") or ""

    db = SessionLocal()
    try:
        app = db.query(App).filter(App.slug == slug).first()
    finally:
        db.close()

    if not app:
        raise HTTPException(status_code=404, detail="App not found.")

    # Visibility check — owner, workspace, or shared+allowed_users
    if app.owner_sub != owner_sub:
        if app.visibility == "workspace":
            pass  # allowed
        elif app.visibility == "shared" and email:
            allowed = _load_json_list(app.allowed_users)
            if email.lower() not in [e.lower() for e in allowed]:
                raise HTTPException(status_code=404, detail="App not found.")
        else:
            raise HTTPException(status_code=404, detail="App not found.")

    # Path sanitisation
    clean = re.sub(r"^[./\\]+", "", rel_path.replace("\\", "/")).strip()
    if not clean:
        clean = "index.html"

    # Overnight apps keep their React source under the "__src__/" prefix; only the
    # built assets at the app root are public. Never serve raw source.
    if clean == "__src__" or clean.startswith("__src__/"):
        raise HTTPException(status_code=404, detail="File not found.")

    # Extension check
    ext = Path(clean).suffix.lower()
    if ext and ext not in _ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=403, detail="File type not served.")

    # Validate path for traversal (raises HTTPException if invalid)
    _sanitise_path(app.id, clean)

    no_cache_exts = {".html", ".htm", ".js", ".mjs", ".css"}
    cache_header = (
        "no-cache, no-store, must-revalidate"
        if Path(clean).suffix.lower() in no_cache_exts
        else "public, max-age=3600"
    )
    mime = _get_mime(Path(clean))

    if _APPS_STORE_ENABLED:
        from fastapi.responses import Response as _Response
        data = _store_download_bytes(app.id, clean)
        if data is None:
            # SPA fallback
            data = _store_download_bytes(app.id, "index.html")
            if data is None:
                raise HTTPException(status_code=404, detail="File not found.")
            mime = "text/html"
            cache_header = "no-cache, no-store, must-revalidate"
        if Path(clean).suffix.lower() in no_cache_exts:
            data = _substitute_app_placeholders(data, app.id)
        return _Response(content=data, media_type=mime, headers={"Cache-Control": cache_header})

    # Local filesystem fallback (docker-compose / local dev)
    file_path = _sanitise_path(app.id, clean)
    if not file_path.exists():
        index = _app_dir(app.id) / "index.html"
        if index.exists():
            return FileResponse(
                str(index),
                media_type="text/html",
                headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
            )
        raise HTTPException(status_code=404, detail="File not found.")

    if Path(clean).suffix.lower() in no_cache_exts:
        from fastapi.responses import Response as _Response
        raw = file_path.read_bytes()
        raw = _substitute_app_placeholders(raw, app.id)
        return _Response(
            content=raw,
            media_type=_get_mime(file_path),
            headers={"Cache-Control": cache_header},
        )

    return FileResponse(
        str(file_path),
        media_type=_get_mime(file_path),
        headers={"Cache-Control": cache_header},
    )
