"""Indexing and search for per-agent knowledge bases.

Sits between the knowledge-base service and the configured
:class:`~providers.retrieval.RetrievalProvider`. The service deals in uploaded
documents; the provider deals in text and namespaces. This module is the
translation: fetch the stored bytes, turn them into text, hand them over, and
turn hits back into something a model can quote.

Indexing is asynchronous by contract — :func:`import_documents` returns once
documents are accepted, and the ingest worker polls state. The local provider
happens to index synchronously; nothing here may assume that.

Document identity is the stored key. Providers that mint their own ids return
them from ``index``; where they do not, the key round-trips unchanged, which is
why :func:`map_uris_to_document_ids` can be an identity mapping.
"""

from __future__ import annotations

import logging
import os
import re

from providers import get_blob_store, get_retrieval
from providers.retrieval import Document

logger = logging.getLogger(__name__)

_DEFAULT_PAGE_SIZE = 5
_MAX_SNIPPET_CHARS = 900

#: Extensions we can turn into text. Anything else is stored but not indexed.
_TEXT_EXTENSIONS = frozenset(
    {".txt", ".md", ".markdown", ".csv", ".json", ".yaml", ".yml", ".html", ".htm"}
)


def data_store_path(data_store_id: str, location: str | None = None) -> str:
    """Stable identifier for a namespace. Kept for callers that log it."""
    return data_store_id


def create_data_store(agent_id: str, location: str | None = None) -> str:
    """Create this agent's search namespace and return its id."""
    namespace = f"agent-{agent_id}"
    get_retrieval().provision(namespace)
    logger.info("kb_index: provisioned namespace %s", namespace)
    return namespace


def delete_data_store(data_store_id: str, location: str | None = None) -> None:
    """Destroy a namespace and everything indexed in it.

    Guarded by ``is_owned_by_this_env``: the namespace comes from the database,
    and a database restored from another environment would otherwise point this
    deletion at that environment's index.
    """
    if not data_store_id:
        return
    from agent_knowledge_base_naming import is_owned_by_this_env

    if not is_owned_by_this_env(data_store_id):
        raise ValueError(
            f"refusing to delete namespace {data_store_id!r}: it does not belong to "
            f"this environment (possible cross-environment database restore)"
        )
    get_retrieval().deprovision(data_store_id)
    logger.info("kb_index: deprovisioned namespace %s", data_store_id)


def _extract_text(data: bytes, key: str) -> str:
    """Turn stored document bytes into indexable text.

    Unknown formats yield ``""`` rather than raising: the document stays stored
    and visible, it simply is not searchable. That is a better outcome than
    failing the whole upload batch because one file was an image.
    """
    ext = os.path.splitext(key)[1].lower()

    if ext in _TEXT_EXTENSIONS:
        return data.decode("utf-8", "replace")

    # Reuse the extractors the attachment path already uses rather than keeping
    # a second implementation. Imported lazily to avoid an import cycle.
    try:
        if ext == ".pdf":
            from api_slack_events import _extract_pdf_text

            return _extract_pdf_text(data) or ""
        if ext == ".docx":
            from api_slack_events import _extract_docx_text

            return _extract_docx_text(data) or ""
        if ext in (".xlsx", ".xlsm"):
            from api_slack_events import _extract_xlsx_text

            return _extract_xlsx_text(data) or ""
    except Exception:
        logger.warning("kb_index: text extraction failed for %s", key, exc_info=True)
        return ""

    logger.info("kb_index: %s has no text extractor — stored but not searchable", key)
    return ""


def import_documents(
    data_store_id: str,
    gcs_uris: list[str],
    *,
    agent_id: str,
    agent_name: str | None = None,
    location: str | None = None,
) -> tuple[int, int]:
    """Index the documents named by ``gcs_uris``. Returns ``(indexed, failed)``.

    A document whose text cannot be extracted counts as failed here so the
    caller can report it, but it stays stored and listed.
    """
    if not gcs_uris:
        return (0, 0)

    store = get_blob_store()
    documents: list[Document] = []
    failed = 0

    for key in gcs_uris:
        try:
            data = store.get(key)
        except Exception:
            logger.exception("kb_index: could not read %s", key)
            failed += 1
            continue
        text = _extract_text(data, key)
        if not text.strip():
            failed += 1
            continue
        documents.append(
            Document(
                id=key,
                text=text,
                title=display_filename(key),
                uri=key,
                metadata={"agent_id": agent_id},
            )
        )

    if documents:
        try:
            get_retrieval().index(data_store_id, documents)
        except Exception:
            logger.exception("kb_index: indexing failed for namespace %s", data_store_id)
            return (0, len(gcs_uris))

    logger.info(
        "kb_index: namespace %s — indexed=%d failed=%d",
        data_store_id, len(documents), failed,
    )
    return (len(documents), failed)


def map_uris_to_document_ids(
    data_store_id: str, gcs_uris: set[str], *, location: str | None = None
) -> dict[str, str]:
    """Map stored keys to indexed document ids.

    The key *is* the id for providers that do not mint their own, so this
    reports which of the requested keys actually made it into the index.
    """
    if not gcs_uris:
        return {}
    try:
        indexed = {s.document_id for s in get_retrieval().document_states(data_store_id)}
    except Exception:
        logger.exception("kb_index: could not read document state for %s", data_store_id)
        return {}
    return {uri: uri for uri in gcs_uris if uri in indexed}


def delete_document(
    data_store_id: str, document_id: str, *, location: str | None = None
) -> None:
    """Remove one document from the index. Missing is not an error."""
    if not document_id:
        return
    try:
        get_retrieval().delete_document(data_store_id, document_id)
    except Exception:
        logger.exception("kb_index: delete failed for %s", document_id)


def _clean_snippet(raw: str) -> str:
    """Turn a stored fragment into plain prose.

    Snippets can carry markup and HTML entities depending on how the document
    was extracted. A model reading ``&#39;`` or ``<b>`` will quote it back to
    the user verbatim, so unescape entities, strip tags, and collapse the
    whitespace — including the non-breaking spaces that survive unescaping.
    """
    import html

    text = re.sub(r"<[^>]+>", "", raw or "")
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def display_filename(uri: str, fallback: str = "document") -> str:
    """A human-meaningful name for a stored key.

    Keys are content-hash prefixed, so strip the hash: a reader wants
    ``report.pdf``, not ``a1b2c3…-report.pdf``.
    """
    name = (uri or "").rstrip("/").rsplit("/", 1)[-1]
    if not name:
        return fallback
    without_hash = re.sub(r"^[0-9a-f]{8,64}-", "", name)
    return without_hash or name or fallback


def search(
    data_store_id: str,
    query: str,
    *,
    agent_id: str,
    agent_name: str | None = None,
    location: str | None = None,
    page_size: int = _DEFAULT_PAGE_SIZE,
) -> list[dict]:
    """Search a namespace. Returns ``[{filename, snippet}, ...]``.

    An empty list is a normal outcome, not an error — the caller surfaces it to
    the model so it can say it found nothing rather than inventing an answer.
    """
    if not (query or "").strip() or not data_store_id:
        return []
    try:
        hits = get_retrieval().search(data_store_id, query, limit=page_size)
    except Exception:
        logger.exception("kb_index: search failed in namespace %s", data_store_id)
        return []

    results: list[dict] = []
    for hit in hits:
        text = _clean_snippet(hit.text)
        if text:
            results.append(
                {
                    "filename": display_filename(hit.uri or hit.document_id),
                    "snippet": text[:_MAX_SNIPPET_CHARS],
                }
            )
    return results
