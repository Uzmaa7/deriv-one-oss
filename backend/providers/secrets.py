"""Where credential material lives.

The contract that matters is not which store is used but what the database is
allowed to hold: **a reference, never a value**. ``put`` returns an opaque
reference string; that string is what gets persisted next to an agent or a user.
Resolving it back to a value happens only in the process that is about to use
it, and the value is never logged, never returned to an API caller, and never
written to the database.

References are opaque to callers. Do not parse them; they mean something only to
the backend that issued them.

The reference implementation encrypts values at rest with a key supplied by the
environment. It is appropriate for a single-node deployment. A deployment with a
managed secret store supplies its own implementation and keeps the same
reference-only contract.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import threading
import time
from abc import ABC, abstractmethod
from pathlib import Path

logger = logging.getLogger(__name__)

# The name portion of a reference — deliberately restrictive so a name can be
# used as a filesystem path component or a remote resource id without escaping.
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,255}$")


class SecretBackendError(RuntimeError):
    """Secret storage failure. Must never include secret material."""


class SecretBackendMisconfigured(SecretBackendError):
    """Backend cannot operate — missing key, unreachable store, bad config."""


class SecretBackend(ABC):
    """Store and resolve credential material by opaque reference."""

    @abstractmethod
    def put(self, name: str, value: str) -> str:
        """Store ``value`` under ``name`` and return a reference to this version.

        Storing again under the same name creates a new version and returns a
        new reference. Old references may continue to resolve.
        """

    @abstractmethod
    def resolve(self, ref: str) -> str:
        """Return the value for ``ref``. Raises :class:`KeyError` if gone."""

    @abstractmethod
    def delete(self, name: str) -> None:
        """Destroy every version of ``name``. Absent names are not an error."""

    def exists(self, name: str) -> bool:
        try:
            self.resolve(self.latest_ref(name))
            return True
        except (KeyError, SecretBackendError):
            return False

    @abstractmethod
    def latest_ref(self, name: str) -> str:
        """The reference for the current version of ``name``."""

    @staticmethod
    def validate_name(name: str) -> str:
        if not SAFE_NAME_RE.match(name or ""):
            raise SecretBackendError("invalid secret name")
        return name


def _prefixed(name: str) -> str:
    """Namespace a secret so several deployments can share one backend."""
    prefix = (os.environ.get("SECRET_PREFIX") or "").strip()
    return f"{prefix}-{name}" if prefix else name


class LocalSecretBackend(SecretBackend):
    """Encrypted secrets on the local filesystem.

    Values are encrypted with AES-GCM under a key from ``SECRET_ENCRYPTION_KEY``
    (32 bytes, base64 or hex). Each write appends a new version file, so an
    existing reference keeps resolving after a rotation.

    Root comes from ``SECRET_ROOT`` (default ``./var/secrets``). That directory
    holds ciphertext only, but it is still credential storage — mode 0700, and it
    belongs on the same footing as any other key material you hold.
    """

    _REF_RE = re.compile(r"^local:(?P<name>[A-Za-z0-9_\-]{1,255}):(?P<version>\d+)$")

    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self._root = Path(
            root or os.environ.get("SECRET_ROOT") or "var/secrets"
        ).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        self._lock = threading.Lock()
        self._key = self._load_key()

    @staticmethod
    def _load_key() -> bytes:
        raw = (os.environ.get("SECRET_ENCRYPTION_KEY") or "").strip()
        if not raw:
            raise SecretBackendMisconfigured(
                "SECRET_ENCRYPTION_KEY is not set — refusing to store credentials "
                "unencrypted. Generate one with: "
                "python -c \"import os,base64;print(base64.b64encode(os.urandom(32)).decode())\""
            )
        for decoder in (base64.b64decode, bytes.fromhex):
            try:
                key = decoder(raw)
            except Exception:
                continue
            if len(key) == 32:
                return key
        # Last resort: derive 32 bytes from an arbitrary passphrase rather than
        # failing, but say so, because a passphrase is weaker than a real key.
        logger.warning(
            "SECRET_ENCRYPTION_KEY is not a 32-byte base64/hex key; deriving one "
            "from it. Prefer a generated key."
        )
        return hashlib.sha256(raw.encode()).digest()

    def _dir(self, name: str) -> Path:
        self.validate_name(name)
        return self._root / _prefixed(name)

    def _encrypt(self, value: str) -> bytes:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = os.urandom(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, value.encode("utf-8"), None)
        return nonce + ciphertext

    def _decrypt(self, blob: bytes) -> str:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        if len(blob) < 13:
            raise SecretBackendError("stored secret is truncated")
        nonce, ciphertext = blob[:12], blob[12:]
        try:
            return AESGCM(self._key).decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception as exc:
            raise SecretBackendError(
                "could not decrypt stored secret — wrong SECRET_ENCRYPTION_KEY?"
            ) from exc

    def put(self, name: str, value: str) -> str:
        directory = self._dir(name)
        with self._lock:
            directory.mkdir(parents=True, exist_ok=True)
            os.chmod(directory, 0o700)
            version = self._next_version(directory)
            path = directory / f"{version}.bin"
            path.write_bytes(self._encrypt(value))
            os.chmod(path, 0o600)
            (directory / "meta.json").write_text(
                json.dumps({"latest": version, "updated_at": int(time.time())}),
                encoding="utf-8",
            )
        return f"local:{_prefixed(name)}:{version}"

    @staticmethod
    def _next_version(directory: Path) -> int:
        versions = [
            int(p.stem) for p in directory.glob("*.bin") if p.stem.isdigit()
        ]
        return (max(versions) + 1) if versions else 1

    def resolve(self, ref: str) -> str:
        match = self._REF_RE.match(ref or "")
        if not match:
            raise SecretBackendError("malformed secret reference")
        path = self._root / match.group("name") / f"{match.group('version')}.bin"
        try:
            blob = path.read_bytes()
        except FileNotFoundError as exc:
            raise KeyError(ref) from exc
        return self._decrypt(blob)

    def latest_ref(self, name: str) -> str:
        directory = self._dir(name)
        meta = directory / "meta.json"
        if not meta.exists():
            raise KeyError(name)
        try:
            latest = int(json.loads(meta.read_text(encoding="utf-8"))["latest"])
        except Exception as exc:
            raise SecretBackendError("unreadable secret metadata") from exc
        return f"local:{_prefixed(name)}:{latest}"

    def delete(self, name: str) -> None:
        import shutil

        directory = self._dir(name)
        with self._lock:
            shutil.rmtree(directory, ignore_errors=True)
