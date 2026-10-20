"""Pluggable provider layer.

Every dependency this platform has on the outside world — where blobs live, where
secrets live, what runs untrusted code, who the people in the organisation are,
what answers a retrieval query, what carries events between processes, where
alerts go — is expressed here as an abstract interface with a self-contained
reference implementation.

Two rules keep this layer honest:

1. **No module in this package may import a hosting vendor's SDK.** The reference
   implementations use the filesystem, SQLite, subprocesses, and HTTP. A
   deployment that wants managed infrastructure supplies its own implementation
   and points the relevant environment variable at it; nothing in the public tree
   needs to know that implementation exists.

2. **The interface is the contract, not the reference implementation.** Callers
   depend only on the abstract base class. The reference implementations are
   correct and usable in production for a single-node deployment, but they are
   deliberately simple — they exist so the project runs on a laptop with no
   external accounts, and so the shape of each interface is demonstrated.

Selection is by environment variable. Each accepts either a built-in short name
or a dotted import path to a class implementing the interface:

    BLOB_STORE=local                        # built-in
    BLOB_STORE=acme.providers:ManagedBlobs  # external implementation

Providers are constructed once and cached. Call `reset_providers()` in tests.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from typing import Any, Callable, TypeVar

from .alerts import AlertSink, LogAlertSink, WebhookAlertSink
from .blobs import BlobStore, LocalBlobStore
from .directory import (
    ClaimsDirectoryProvider,
    DirectoryProvider,
    FileDirectoryProvider,
    NullDirectoryProvider,
    Person,
)
from .queue import InProcessQueue, MessageQueue
from .retrieval import LocalRetrievalProvider, RetrievalProvider, RetrievedChunk
from .sandbox import ContainerSandbox, SandboxProvider, SandboxResult, SubprocessSandbox
from .secrets import LocalSecretBackend, SecretBackend, SecretBackendError

logger = logging.getLogger(__name__)

T = TypeVar("T")

__all__ = [
    "AlertSink",
    "BlobStore",
    "DirectoryProvider",
    "MessageQueue",
    "Person",
    "RetrievalProvider",
    "RetrievedChunk",
    "SandboxProvider",
    "SandboxResult",
    "SecretBackend",
    "SecretBackendError",
    "get_alert_sink",
    "get_blob_store",
    "get_directory",
    "get_message_queue",
    "get_retrieval",
    "get_sandbox",
    "get_secret_backend",
    "reset_providers",
]


class ProviderConfigurationError(RuntimeError):
    """Raised when a provider name cannot be resolved to a usable class."""


_lock = threading.Lock()
_instances: dict[str, Any] = {}


def _load_dotted(path: str) -> type:
    """Import ``module:Class`` (or ``module.Class``) and return the class."""
    if ":" in path:
        module_name, _, attr = path.partition(":")
    else:
        module_name, _, attr = path.rpartition(".")
    if not module_name or not attr:
        raise ProviderConfigurationError(
            f"{path!r} is not a valid import path — expected 'module:Class'"
        )
    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:
        raise ProviderConfigurationError(
            f"cannot import provider module {module_name!r}: {exc}"
        ) from exc
    try:
        return getattr(module, attr)
    except AttributeError as exc:
        raise ProviderConfigurationError(
            f"module {module_name!r} has no attribute {attr!r}"
        ) from exc


def _resolve(
    *,
    slot: str,
    env_var: str,
    builtins: dict[str, Callable[[], T]],
    default: str,
    interface: type,
) -> T:
    """Resolve, construct and cache the provider configured for ``slot``."""
    cached = _instances.get(slot)
    if cached is not None:
        return cached

    with _lock:
        cached = _instances.get(slot)
        if cached is not None:
            return cached

        name = (os.environ.get(env_var) or default).strip()
        factory = builtins.get(name.lower())
        if factory is not None:
            instance = factory()
        else:
            cls = _load_dotted(name)
            if not issubclass(cls, interface):
                raise ProviderConfigurationError(
                    f"{name!r} does not implement {interface.__name__}"
                )
            instance = cls()

        logger.info("provider %s -> %s", slot, type(instance).__name__)
        _instances[slot] = instance
        return instance


def get_blob_store() -> BlobStore:
    """Object storage for skills, generated files and run artefacts."""
    return _resolve(
        slot="blob_store",
        env_var="BLOB_STORE",
        builtins={"local": LocalBlobStore},
        default="local",
        interface=BlobStore,
    )


def get_secret_backend() -> SecretBackend:
    """Storage for credential material. The database only ever holds references."""
    return _resolve(
        slot="secret_backend",
        env_var="SECRET_BACKEND",
        builtins={"local": LocalSecretBackend},
        default="local",
        interface=SecretBackend,
    )


def get_sandbox() -> SandboxProvider:
    """Isolation boundary for model-generated code."""
    return _resolve(
        slot="sandbox",
        env_var="SANDBOX_PROVIDER",
        builtins={"container": ContainerSandbox, "subprocess": SubprocessSandbox},
        default="container",
        interface=SandboxProvider,
    )


def get_directory() -> DirectoryProvider:
    """Source of truth for people, their reporting lines and their groups."""
    return _resolve(
        slot="directory",
        env_var="DIRECTORY_PROVIDER",
        builtins={
            "file": FileDirectoryProvider,
            "claims": ClaimsDirectoryProvider,
            "null": NullDirectoryProvider,
        },
        default="claims",
        interface=DirectoryProvider,
    )


def get_retrieval() -> RetrievalProvider:
    """Document indexing and search behind agent knowledge bases."""
    return _resolve(
        slot="retrieval",
        env_var="RETRIEVAL_PROVIDER",
        builtins={"local": LocalRetrievalProvider},
        default="local",
        interface=RetrievalProvider,
    )


def get_message_queue() -> MessageQueue:
    """Transport between the API process and background workers."""
    return _resolve(
        slot="message_queue",
        env_var="MESSAGE_QUEUE",
        builtins={"inprocess": InProcessQueue},
        default="inprocess",
        interface=MessageQueue,
    )


def get_alert_sink() -> AlertSink:
    """Where operational alerts are delivered."""
    return _resolve(
        slot="alert_sink",
        env_var="ALERT_SINK",
        builtins={"log": LogAlertSink, "webhook": WebhookAlertSink},
        default="log",
        interface=AlertSink,
    )


def reset_providers() -> None:
    """Drop every cached provider. For tests and configuration reloads."""
    with _lock:
        _instances.clear()
