"""Google Workspace integration helpers for Lab data sources and platform agents.

Server-side token refresh and Drive file download. Credentials are read from
the user vault (the vault) — never passed to sandboxes.
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
from dataclasses import dataclass
from typing import Literal, Optional

import httpx

from database import SessionLocal, UserSecret
from google_oauth import GOOGLE_TOKEN_URL, GoogleOAuthError
from secret_vault import SecretVaultError, SecretVaultMisconfiguredError, get_vault

logger = logging.getLogger(__name__)

_GOOGLE_SLUG = "GOOGLE_OAUTH_CREDENTIALS"
_HTTP_TIMEOUT_SECS = 30.0
_DRIVE_API = "https://www.googleapis.com/drive/v3"

# MIME types for Google Workspace native files and uploads.
_MIME_SHEET = "application/vnd.google-apps.spreadsheet"
_MIME_DOC = "application/vnd.google-apps.document"
_MIME_CSV = "text/csv"
_MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_MIME_XLS = "application/vnd.ms-excel"

_DRIVE_URL_PATTERNS = [
    re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"/document/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"/file/d/([a-zA-Z0-9_-]+)"),
    re.compile(r"[?&]id=([a-zA-Z0-9_-]+)"),
]
_FILE_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{10,}$")


@dataclass
class DownloadedFile:
    file_id: str
    name: str
    mime_type: str
    source_type: Literal["sheet", "doc", "file"]
    content: bytes
    content_path_ext: str  # csv | txt | xlsx
    truncated: bool
    row_estimate: Optional[int] = None
    columns_preview: Optional[list[str]] = None


def parse_drive_url(url_or_id: str) -> Optional[str]:
    """Extract a Google Drive file ID from a URL or bare ID string.

    Accepts a bare ID (no URL structure) as a fallback — intended for explicit
    "attach by URL or ID" inputs where the whole field is the ID, not for
    scanning prose. Use parse_drive_url_strict() for free-text scanning.
    """
    text = (url_or_id or "").strip()
    if not text:
        return None
    file_id = parse_drive_url_strict(text)
    if file_id:
        return file_id
    if _FILE_ID_RE.match(text):
        return text
    return None


def parse_drive_url_strict(url: str) -> Optional[str]:
    """Extract a Google Drive file ID only from an actual Drive URL pattern.

    Unlike parse_drive_url(), this never falls back to treating a bare
    alphanumeric token as a file ID — safe to run against arbitrary prose.
    """
    text = (url or "").strip()
    if not text:
        return None
    for pattern in _DRIVE_URL_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def fetch_google_credentials_for_sub(owner_sub: str) -> Optional[str]:
    """Return raw GOOGLE_OAUTH_CREDENTIALS JSON for a user, or None."""
    db = SessionLocal()
    try:
        row = (
            db.query(UserSecret)
            .filter(
                UserSecret.owner_sub == owner_sub,
                UserSecret.slug == _GOOGLE_SLUG,
            )
            .first()
        )
        if not row:
            return None
        secret_ref = row.secret_ref
    finally:
        db.close()

    try:
        vault = get_vault()
        return vault.read(secret_ref)
    except (SecretVaultMisconfiguredError, SecretVaultError):
        logger.warning(
            "Could not read Google credentials from vault for owner (sub omitted)"
        )
        return None


async def refresh_access_token(credentials_json: str) -> str:
    """Exchange a refresh token for a short-lived access token.

    Raises ``GoogleOAuthError`` on failure (e.g. ``invalid_grant``).
    Never logs secret values.
    """
    try:
        creds = json.loads(credentials_json)
    except (ValueError, TypeError) as exc:
        raise GoogleOAuthError("Invalid credentials JSON") from exc

    client_id = creds.get("client_id", "")
    client_secret = creds.get("client_secret", "")
    refresh_token = creds.get("refresh_token", "")
    token_uri = creds.get("token_uri", GOOGLE_TOKEN_URL)

    if not all([client_id, client_secret, refresh_token]):
        raise GoogleOAuthError("Credentials JSON missing required fields")

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECS) as client:
        resp = await client.post(
            token_uri,
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "refresh_token": refresh_token,
                "grant_type": "refresh_token",
            },
        )

    if resp.status_code != 200:
        try:
            err = resp.json().get("error", "unknown")
        except ValueError:
            err = "unknown"
        logger.error("Google token refresh failed: error=%s", err)
        raise GoogleOAuthError("Google token refresh failed", google_error=err)

    data = resp.json()
    access_token = data.get("access_token", "")
    if not access_token:
        raise GoogleOAuthError("Google token refresh returned no access_token")
    return access_token


def _truncate_content(content: bytes, max_bytes: int) -> tuple[bytes, bool]:
    if len(content) <= max_bytes:
        return content, False
    return content[:max_bytes], True


def _estimate_csv_rows(content: bytes) -> Optional[int]:
    try:
        text = content.decode("utf-8", errors="replace")
        return max(0, text.count("\n") - 1)  # minus header
    except Exception:
        return None


def _csv_columns_preview(content: bytes) -> Optional[list[str]]:
    try:
        text = content.decode("utf-8", errors="replace")
        reader = csv.reader(io.StringIO(text))
        row = next(reader, None)
        return row if row else None
    except Exception:
        return None


def _source_type_for_mime(mime_type: str) -> Literal["sheet", "doc", "file"]:
    if mime_type == _MIME_SHEET:
        return "sheet"
    if mime_type == _MIME_DOC:
        return "doc"
    return "file"


def _ext_for_mime(mime_type: str) -> str:
    if mime_type == _MIME_SHEET:
        return "csv"
    if mime_type == _MIME_DOC:
        return "txt"
    if mime_type in (_MIME_XLSX, _MIME_XLS):
        return "xlsx"
    if mime_type == _MIME_CSV:
        return "csv"
    return "txt"


_MAX_LIVE_DATA_ROWS = 50_000


def parse_csv_to_table(
    content: bytes,
    max_rows: int = _MAX_LIVE_DATA_ROWS,
) -> tuple[list[str], list[list[str]]]:
    """Parse CSV bytes into column names and row values (all strings)."""
    text = content.decode("utf-8", errors="replace")
    reader = csv.reader(io.StringIO(text))
    header = next(reader, None)
    if not header:
        return [], []
    columns = [str(c).strip() for c in header]
    rows: list[list[str]] = []
    for i, row in enumerate(reader):
        if i >= max_rows:
            break
        rows.append([str(c) for c in row])
    return columns, rows


def build_live_data_payload(downloaded: DownloadedFile, fetched_at: str) -> dict:
    """Build JSON-serialisable live-data response from a Drive download."""
    payload: dict = {
        "file_id": downloaded.file_id,
        "name": downloaded.name,
        "source_type": downloaded.source_type,
        "fetched_at": fetched_at,
        "truncated": downloaded.truncated,
        "byte_size": len(downloaded.content),
    }
    ext = downloaded.content_path_ext
    if ext == "csv":
        columns, rows = parse_csv_to_table(downloaded.content)
        payload.update({
            "format": "csv",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
        })
    elif ext == "txt":
        payload.update({
            "format": "text",
            "text": downloaded.content.decode("utf-8", errors="replace"),
            "columns": None,
            "rows": None,
            "row_count": None,
        })
    else:
        payload.update({
            "format": ext,
            "columns": downloaded.columns_preview,
            "rows": None,
            "row_count": downloaded.row_estimate,
            "message": (
                "Live tabular rows are only returned for Google Sheets (CSV export). "
                "Use the attached snapshot for xlsx or re-attach as a Google Sheet."
            ),
        })
    return payload


async def download_drive_file(
    access_token: str,
    file_id: str,
    max_bytes: int = 512 * 1024,
) -> DownloadedFile:
    """Download or export a Drive file as bytes.

    Uses Drive API v3. Raises ``GoogleOAuthError`` on auth errors and
    ``httpx.HTTPStatusError`` on other HTTP failures.
    """
    headers = {"Authorization": f"Bearer {access_token}"}

    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_SECS) as client:
        meta_resp = await client.get(
            f"{_DRIVE_API}/files/{file_id}",
            headers=headers,
            params={"fields": "id,name,mimeType"},
        )
        if meta_resp.status_code == 401:
            raise GoogleOAuthError("Google Drive access unauthorized", google_error="unauthorized")
        if meta_resp.status_code == 404:
            raise GoogleOAuthError("Google Drive file not found", google_error="not_found")
        meta_resp.raise_for_status()
        meta = meta_resp.json()

        name = meta.get("name", file_id)
        mime_type = meta.get("mimeType", "")

        if mime_type == _MIME_SHEET:
            dl_resp = await client.get(
                f"{_DRIVE_API}/files/{file_id}/export",
                headers=headers,
                params={"mimeType": "text/csv"},
            )
        elif mime_type == _MIME_DOC:
            dl_resp = await client.get(
                f"{_DRIVE_API}/files/{file_id}/export",
                headers=headers,
                params={"mimeType": "text/plain"},
            )
        else:
            dl_resp = await client.get(
                f"{_DRIVE_API}/files/{file_id}",
                headers=headers,
                params={"alt": "media"},
            )

        if dl_resp.status_code == 401:
            raise GoogleOAuthError("Google Drive access unauthorized", google_error="unauthorized")
        if dl_resp.status_code == 404:
            raise GoogleOAuthError("Google Drive file not found", google_error="not_found")
        dl_resp.raise_for_status()
        raw = dl_resp.content

    content, truncated = _truncate_content(raw, max_bytes)
    source_type = _source_type_for_mime(mime_type)
    ext = _ext_for_mime(mime_type)

    row_estimate: Optional[int] = None
    columns_preview: Optional[list[str]] = None
    if ext == "csv":
        row_estimate = _estimate_csv_rows(content)
        columns_preview = _csv_columns_preview(content)

    return DownloadedFile(
        file_id=file_id,
        name=name,
        mime_type=mime_type,
        source_type=source_type,
        content=content,
        content_path_ext=ext,
        truncated=truncated,
        row_estimate=row_estimate,
        columns_preview=columns_preview,
    )
