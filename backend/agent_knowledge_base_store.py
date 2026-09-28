"""Document storage for per-agent knowledge bases.

Each agent's documents live under their own key prefix in the configured
:class:`~providers.blobs.BlobStore`. The prefix plays the role a separate
container would: it is created when a knowledge base is enabled, destroyed when
it is disabled, and one agent's prefix is never reachable from another's.

Unlike ``skills_store.py`` — where a storage outage must not stop the app from
starting — these operations **do** raise. A silently-failed write would leave a
knowledge base marked ready with nowhere to put documents, which is worse than
a visible failure.
"""

from __future__ import annotations

import logging

from agent_knowledge_base_naming import (
    bucket_name_for_agent,
    is_owned_by_this_env,
)
from providers import get_blob_store

logger = logging.getLogger(__name__)

_DOCUMENTS_PREFIX = "documents/"

#: Everything this module writes lives under here.
NAMESPACE = "knowledge-bases"


def _root(namespace: str) -> str:
    """Key prefix for one agent's knowledge base."""
    return f"{NAMESPACE}/{namespace.strip('/')}/"


def object_path(content_hash: str, filename: str) -> str:
    """Path of a document within its agent's namespace.

    Content-hash prefixed so the database's de-duplication guard and the stored
    layout agree — re-uploading identical bytes cannot produce two objects.
    """
    safe = filename.replace("/", "_").replace("\\", "_").strip() or "document"
    return f"{_DOCUMENTS_PREFIX}{content_hash}-{safe}"


def storage_uri(namespace: str, obj_path: str) -> str:
    """Stable identifier for a stored document."""
    return f"{_root(namespace)}{obj_path}"


def create_namespace(agent_id: str, agent_name: str | None = None) -> str:
    """Reserve this agent's namespace and return its name.

    Idempotent — a key prefix needs no creation step, so this validates the name
    and returns it. Kept as a distinct call so the provisioning flow reads the
    same whether or not the underlying store needs a container created.
    """
    name = bucket_name_for_agent(agent_id)
    logger.info("kb_store: namespace %s ready", name)
    return name


def upload_document(namespace: str, obj_path: str, data: bytes, content_type: str) -> str:
    """Store document bytes. Returns the stored document's URI."""
    key = storage_uri(namespace, obj_path)
    get_blob_store().put(key, data, content_type=content_type or "application/octet-stream")
    return key


def delete_document(namespace: str, obj_path: str) -> None:
    """Delete one document. A missing document is not an error."""
    try:
        get_blob_store().delete(storage_uri(namespace, obj_path))
    except Exception as exc:  # noqa: BLE001 — absence is the common, benign case
        logger.warning("kb_store: delete of %s/%s failed: %s", namespace, obj_path, exc)


def delete_namespace(namespace: str) -> None:
    """Delete an agent's namespace and everything in it.

    Guarded by ``is_owned_by_this_env``: the namespace comes from the database,
    and a database restored from another environment would otherwise point this
    deletion at that environment's data.
    """
    if not namespace:
        return
    if not is_owned_by_this_env(namespace):
        raise ValueError(
            f"refusing to delete namespace {namespace!r}: it does not belong to this "
            f"environment (possible cross-environment database restore)"
        )
    removed = get_blob_store().delete_prefix(_root(namespace))
    logger.info("kb_store: deleted namespace %s (%d object(s))", namespace, removed)


def total_bytes(namespace: str) -> int:
    """Total stored size — used to enforce the per-agent storage cap."""
    if not namespace:
        return 0
    return sum(info.size or 0 for info in get_blob_store().list(_root(namespace)))
