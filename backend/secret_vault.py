"""Credential storage, as the rest of the application sees it.

The contract this module exists to enforce is that **the database holds a
reference, never a value**. Writing a secret returns an opaque reference string;
that reference is what gets persisted next to an agent, a skill or a user.
Resolving it back to a value happens only in the process about to use it.

Where those bytes actually live is a deployment's choice, expressed through the
configured :class:`~providers.secrets.SecretBackend`. This module owns the
naming scheme and the validation; the backend owns the storage.

Names are derived, never accepted from a caller verbatim. Every path helper
validates its inputs against a conservative pattern before building a name, so
a hostile agent id or skill slug cannot reach outside its own namespace.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Optional

from app_config import RESOURCE_PREFIX, current_env
from providers import get_secret_backend
from providers.secrets import (
    SecretBackendError,
    SecretBackendMisconfigured,
)

logger = logging.getLogger(__name__)

# Kept as the module's own error names so callers do not import from providers.
SecretVaultError = SecretBackendError
SecretVaultMisconfiguredError = SecretBackendMisconfigured

#: Conservative name pattern. Applied to every caller-supplied path component.
_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,255}$")

_AGENT_SECRET_KINDS = frozenset(
    {"xoxb", "xapp", "signing_secret", "client_secret", "gateway"}
)


class SecretVault:
    """Naming and validation over a configured secret backend."""

    def __init__(self) -> None:
        self._backend = get_secret_backend()

    def write(self, name: str, value: str) -> str:
        """Store ``value`` under ``name``; return the reference to persist."""
        _validate_name(name)
        if not isinstance(value, str) or not value:
            raise SecretVaultError("secret value must be a non-empty string")
        return self._backend.put(name, value)

    def read(self, ref: str) -> str:
        """Resolve a reference to its value. Never log the result."""
        if not isinstance(ref, str) or not ref.strip():
            raise SecretVaultError("secret reference must be a non-empty string")
        return self._backend.resolve(ref)

    def latest_version_ref(self, name: str) -> str:
        """Reference to the current version of ``name``."""
        _validate_name(name)
        return self._backend.latest_ref(name)

    def disable_version(self, ref: str) -> None:
        """Retire one version of a secret.

        Backends that do not version secrets individually cannot honour this;
        the call is a no-op there rather than an error, because rotation must
        not fail on a backend that simply overwrites. Use :meth:`delete` when
        the intent is that the material must become unreadable.
        """
        disable = getattr(self._backend, "disable_version", None)
        if callable(disable):
            disable(ref)
            return
        logger.info(
            "secret backend %s does not support per-version disable; "
            "leaving %s in place (use delete() to destroy it)",
            type(self._backend).__name__,
            _redact_ref(ref),
        )

    def delete(self, ref_or_name: str) -> None:
        """Destroy a secret and every version of it."""
        name = ref_or_name
        # Accept a reference as well as a bare name: callers hold whichever
        # they persisted, and both should destroy the same thing.
        if ":" in ref_or_name or "/" in ref_or_name:
            name = _name_from_ref(ref_or_name)
        _validate_name(name)
        self._backend.delete(name)


def _validate_name(name: str) -> str:
    if not isinstance(name, str) or not _SAFE_NAME_RE.match(name):
        raise SecretVaultError("invalid secret name")
    return name


def _name_from_ref(ref: str) -> str:
    """Best-effort name extraction from an opaque reference.

    References are opaque by contract, so this is a convenience for callers that
    kept only a reference. It handles the common ``scheme:name:version`` and
    ``.../secrets/<name>/...`` shapes and otherwise returns the input unchanged.
    """
    if "/secrets/" in ref:
        tail = ref.split("/secrets/", 1)[1]
        return tail.split("/", 1)[0]
    parts = ref.split(":")
    return parts[1] if len(parts) >= 3 else ref


def _redact_ref(ref: str) -> str:
    """A reference is not secret, but truncate it in logs anyway."""
    return (ref[:24] + "…") if len(ref) > 24 else ref


# ── Name construction ─────────────────────────────────────────────────────────

def _env_scoped(kind_prefix: str) -> str:
    """Namespace non-production secrets so environments cannot collide.

    Deployments frequently point several environments at one backing store.
    Production keeps the bare name; everything else is suffixed, so a staging
    deployment can never resolve a production credential by guessing a name.
    """
    env = current_env()
    return (
        f"{RESOURCE_PREFIX}-{kind_prefix}"
        if env == "production"
        else f"{RESOURCE_PREFIX}-{kind_prefix}-{env}"
    )


def vault_path_for_agent(agent_id: str, kind: str) -> str:
    """Secret name for one of an agent's credentials."""
    if not isinstance(agent_id, str) or not _SAFE_NAME_RE.match(agent_id):
        raise SecretVaultError("invalid agent_id for secret path")
    if kind not in _AGENT_SECRET_KINDS:
        raise SecretVaultError(
            "kind must be one of: " + ", ".join(sorted(_AGENT_SECRET_KINDS))
        )
    return f"{_env_scoped('agents')}-{agent_id}-{kind}"


def vault_path_for_skill_secret(slug: str, env_key: str) -> str:
    """Secret name for a published skill's environment variable."""
    if not isinstance(slug, str) or not _SAFE_NAME_RE.match(slug):
        raise SecretVaultError("invalid slug for skill secret path")
    if not isinstance(env_key, str) or not _SAFE_NAME_RE.match(env_key):
        raise SecretVaultError("invalid env_key for skill secret path")
    return f"{_env_scoped('skill')}-{slug}-{env_key.lower()}"


def vault_path_for_user_secret(owner_sub: str, slug: str) -> str:
    """Secret name for a user's personal secret.

    The subject identifier is hashed rather than embedded: subject ids come from
    the identity provider, may contain characters no backend accepts in a
    resource name, and should not be readable in a listing of secret names.
    """
    if not isinstance(owner_sub, str) or not owner_sub.strip():
        raise SecretVaultError("owner_sub must be a non-empty string")
    if not isinstance(slug, str) or not _SAFE_NAME_RE.match(slug):
        raise SecretVaultError("invalid slug for user secret path")
    sub_hash = hashlib.sha256(owner_sub.strip().encode("utf-8")).hexdigest()[:32]
    return f"{_env_scoped('user')}-{sub_hash}-{slug.lower()}"


# ── Process-wide singleton ────────────────────────────────────────────────────

_vault_singleton: Optional[SecretVault] = None
_vault_init_error: Optional[Exception] = None


def init_vault_from_env() -> SecretVault:
    """Initialise the process-wide vault. Idempotent.

    Fails closed: if the configured backend cannot start, every later call
    re-raises the original error rather than falling back to something weaker.
    Storing credentials somewhere unintended is worse than not starting.
    """
    global _vault_singleton, _vault_init_error
    if _vault_singleton is not None:
        return _vault_singleton
    if _vault_init_error is not None:
        raise _vault_init_error
    try:
        _vault_singleton = SecretVault()
    except Exception as exc:
        _vault_init_error = exc
        raise
    return _vault_singleton


def get_vault() -> SecretVault:
    """The process-wide vault, initialising it on first use."""
    return init_vault_from_env()


def reset_vault() -> None:
    """Drop the singleton. For tests and configuration reloads."""
    global _vault_singleton, _vault_init_error
    _vault_singleton = None
    _vault_init_error = None
