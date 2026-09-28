"""Storage for files produced during a run.

Every transient artefact the platform hands back to a human goes here: files a
sandboxed code run wrote, files the browser agent downloaded. Callers keep them
apart with a prefix — ``workspaces/<invocation_id>/`` for code runs,
``browser/<session>/`` for browser downloads — and clean up after themselves
with :func:`cleanup`.

Everything is stored under a single ``generated/`` namespace in the configured
:class:`~providers.blobs.BlobStore`, so a deployment can expire the whole
namespace on a schedule without knowing what produced each file.

``download_url`` returns a time-limited link when the store can mint one and
``None`` when it cannot; a caller that gets ``None`` must stream the bytes
itself rather than assuming a link exists.

Every function here is synchronous and may do I/O — callers on the event loop
must wrap them in ``run_in_executor``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from providers import get_blob_store

logger = logging.getLogger(__name__)

DEFAULT_URL_MINUTES = 30

#: Everything this module writes lives under here.
NAMESPACE = "generated"


def safe_name(filename: str) -> str:
    """Reduce a filename to a single safe path component."""
    safe = Path(filename).name
    if not safe or safe in (".", "..") or "/" in filename.replace("\\", "/"):
        safe = Path(filename.replace("\\", "/")).name
    if not safe or safe in (".", ".."):
        raise ValueError("invalid workspace filename")
    return safe


def blob_name(prefix: str, filename: str) -> str:
    """Full store key for a file under ``prefix``."""
    return f"{NAMESPACE}/{prefix.strip('/')}/{safe_name(filename)}"


def upload_from_file(prefix: str, filename: str, local_path: str) -> None:
    get_blob_store().put(blob_name(prefix, filename), Path(local_path).read_bytes())


def upload_bytes(prefix: str, filename: str, data: bytes) -> None:
    get_blob_store().put(blob_name(prefix, filename), data)


def download_url(
    prefix: str,
    filename: str,
    minutes: int = DEFAULT_URL_MINUTES,
    download_name: str | None = None,
) -> str:
    """A time-limited link to one file, or ``""`` if the store cannot mint one.

    ``download_name`` is accepted for callers that want the browser to save
    under the original name when the stored key is content-hashed. Stores that
    cannot express that ignore it.
    """
    try:
        url = get_blob_store().signed_url(
            blob_name(prefix, filename), ttl_seconds=max(60, minutes * 60)
        )
    except Exception:
        logger.exception("run_workspace: could not build a download URL")
        return ""
    return url or ""


def download_to_file(prefix: str, filename: str, local_path: str) -> bool:
    try:
        data = get_blob_store().get(blob_name(prefix, filename))
    except KeyError:
        return False
    except Exception:
        logger.exception("run_workspace: download failed")
        return False
    Path(local_path).write_bytes(data)
    return True


def download_bytes(prefix: str, filename: str) -> bytes | None:
    try:
        return get_blob_store().get(blob_name(prefix, filename))
    except KeyError:
        return None
    except Exception:
        logger.exception("run_workspace: download failed")
        return None


def exists(prefix: str, filename: str) -> bool:
    try:
        return get_blob_store().exists(blob_name(prefix, filename))
    except Exception:
        return False


def cleanup(prefix: str) -> None:
    """Best-effort delete of everything under a prefix."""
    try:
        removed = get_blob_store().delete_prefix(f"{NAMESPACE}/{prefix.strip('/')}/")
        if removed:
            logger.info("run_workspace: cleaned %d file(s) under %s", removed, prefix)
    except Exception:
        logger.exception("run_workspace: cleanup failed for %s", prefix)


def storage_available() -> bool:
    """Whether run artefacts can be stored at all.

    There is always a configured store, so this is normally true — it exists so
    callers can degrade gracefully if the store cannot be constructed (bad
    configuration, unwritable root) rather than failing the whole run.
    """
    try:
        get_blob_store()
        return True
    except Exception:
        logger.exception("run_workspace: no usable blob store")
        return False
