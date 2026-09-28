"""Where files live.

Skill sources, files a sandboxed run produced, documents uploaded to a knowledge
base — all of it goes through this interface. Keys are POSIX-style paths
(``skills/my-skill/skill.md``); the store decides what that means physically.

``signed_url`` is the one method that needs explaining. Several features hand a
human a link to a file that the platform holds. The store returns a
time-limited URL for that file, or ``None`` if it cannot mint one, in which case
the caller must stream the bytes itself. The local implementation signs a token
the application verifies on its own download route, so the concept works with no
external infrastructure.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import shutil
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote

logger = logging.getLogger(__name__)


class BlobStoreError(RuntimeError):
    """Storage failure. Never contains file contents."""


@dataclass(frozen=True)
class BlobInfo:
    key: str
    size: int
    modified_at: float


class BlobStore(ABC):
    """A flat namespace of byte blobs addressed by key."""

    @abstractmethod
    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        """Write ``data`` at ``key``, replacing anything already there."""

    @abstractmethod
    def get(self, key: str) -> bytes:
        """Read ``key``. Raises :class:`KeyError` if absent."""

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key``. Absent keys are not an error."""

    @abstractmethod
    def exists(self, key: str) -> bool: ...

    @abstractmethod
    def list(self, prefix: str = "") -> list[BlobInfo]:
        """Every key beginning with ``prefix``."""

    def get_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.get(key).decode(encoding)

    def put_text(self, key: str, text: str, encoding: str = "utf-8") -> None:
        self.put(key, text.encode(encoding), content_type="text/plain; charset=utf-8")

    def signed_url(self, key: str, *, ttl_seconds: int = 3600) -> Optional[str]:
        """A time-limited URL for ``key``, or ``None`` if unsupported."""
        return None

    def delete_prefix(self, prefix: str) -> int:
        """Delete everything under ``prefix``. Returns the count removed."""
        removed = 0
        for info in self.list(prefix):
            self.delete(info.key)
            removed += 1
        return removed


def _normalise_key(key: str) -> str:
    """Reject anything that could escape the store's root."""
    cleaned = (key or "").strip().strip("/")
    if not cleaned:
        raise BlobStoreError("blob key must not be empty")
    parts: list[str] = []
    for part in cleaned.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            raise BlobStoreError("blob key must not contain '..'")
        parts.append(part)
    if not parts:
        raise BlobStoreError("blob key must not be empty")
    return "/".join(parts)


class LocalBlobStore(BlobStore):
    """Blobs as files under a single root directory.

    Root comes from ``BLOB_ROOT`` (default ``./var/blobs``). Signed URLs are
    HMAC tokens over ``key`` and an expiry, verified by the application's own
    download route using the same ``BLOB_URL_SECRET``.
    """

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(root or os.environ.get("BLOB_ROOT") or "var/blobs").resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._url_base = (os.environ.get("BLOB_URL_BASE") or "/api/files").rstrip("/")
        self._url_secret = os.environ.get("BLOB_URL_SECRET") or ""

    def _path(self, key: str) -> Path:
        path = (self._root / _normalise_key(key)).resolve()
        if not path.is_relative_to(self._root):
            raise BlobStoreError("resolved blob path escapes the store root")
        return path

    def put(self, key: str, data: bytes, *, content_type: str | None = None) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename so a reader never observes a partial file.
        tmp = path.with_suffix(path.suffix + ".partial")
        tmp.write_bytes(data)
        tmp.replace(path)

    def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(key) from exc

    def delete(self, key: str) -> None:
        path = self._path(key)
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except FileNotFoundError:
            return

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def list(self, prefix: str = "") -> list[BlobInfo]:
        cleaned = (prefix or "").strip().strip("/")
        base = self._root / cleaned if cleaned else self._root
        search_root = base if base.is_dir() else self._root
        results: list[BlobInfo] = []
        for path in search_root.rglob("*"):
            if not path.is_file() or path.suffix == ".partial":
                continue
            key = path.relative_to(self._root).as_posix()
            if cleaned and not key.startswith(cleaned):
                continue
            stat = path.stat()
            results.append(BlobInfo(key=key, size=stat.st_size, modified_at=stat.st_mtime))
        results.sort(key=lambda b: b.key)
        return results

    def signed_url(self, key: str, *, ttl_seconds: int = 3600) -> Optional[str]:
        if not self._url_secret:
            # Unsigned links would be an open read of the whole store.
            logger.debug("BLOB_URL_SECRET unset; cannot mint signed URLs")
            return None
        normalised = _normalise_key(key)
        expires = int(time.time()) + max(1, int(ttl_seconds))
        token = self.sign(normalised, expires)
        return f"{self._url_base}/{quote(normalised)}?expires={expires}&token={token}"

    def sign(self, key: str, expires: int) -> str:
        payload = f"{_normalise_key(key)}:{int(expires)}".encode()
        return hmac.new(
            self._url_secret.encode(), payload, hashlib.sha256
        ).hexdigest()

    def verify(self, key: str, expires: int, token: str) -> bool:
        """Check a signed-URL token. Constant-time, expiry-aware."""
        if not self._url_secret or not token:
            return False
        try:
            expires_int = int(expires)
        except (TypeError, ValueError):
            return False
        if expires_int < time.time():
            return False
        return hmac.compare_digest(self.sign(key, expires_int), token)
