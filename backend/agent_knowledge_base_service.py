"""Provisioning, upload and deprovisioning logic for per-agent knowledge bases.

Kept separate from the HTTP layer (``api_agent_knowledge_base.py``) so the
ingest worker and the retrieval tool handler can reuse it without importing
FastAPI.

See AGENT_KNOWLEDGE_BASE_SPEC.md §3, §4, §7.
"""

from __future__ import annotations

import hashlib
import logging
import os
from datetime import datetime

import agent_knowledge_base_index as kb_index
import agent_knowledge_base_store as kb_store
from agent_knowledge_base_naming import bucket_name_for_agent
from agents import (
    KB_DOC_PENDING,
    KB_STATUS_DEPROVISIONING,
    KB_STATUS_DISABLED,
    KB_STATUS_ERROR,
    KB_STATUS_PROVISIONING,
    KB_STATUS_READY,
    Agent,
    AgentKnowledgeBase,
    AgentKnowledgeBaseDocument,
)
from database import SessionLocal

logger = logging.getLogger(__name__)

# ── Upload limits ─────────────────────────────────────────────────────────────
# Per-file cap. the retrieval provider's own unstructured-document limit is higher,
# but a smaller cap keeps a single upload from monopolising a request worker.
MAX_FILE_BYTES = int(os.environ.get("KB_MAX_FILE_BYTES", str(50 * 1024 * 1024)))
# Per-request batch cap — bounds the size of any single import LRO.
MAX_FILES_PER_REQUEST = int(os.environ.get("KB_MAX_FILES_PER_REQUEST", "20"))
# Per-agent ceilings. Both protect cost and retrieval quality: a data store
# stuffed with thousands of documents returns noisier grounding.
MAX_DOCUMENTS_PER_AGENT = int(os.environ.get("KB_MAX_DOCUMENTS_PER_AGENT", "500"))
MAX_TOTAL_BYTES_PER_AGENT = int(
    os.environ.get("KB_MAX_TOTAL_BYTES_PER_AGENT", str(2 * 1024 * 1024 * 1024))
)

# the retrieval provider's unstructured ingestion supports these; anything else would
# import and then silently fail to index.
ALLOWED_MIME_TYPES = frozenset({
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",  # .docx
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",  # .pptx
    "application/msword",
    "text/plain",
    "text/html",
    "text/markdown",
    "text/csv",
})

ALLOWED_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc", ".pptx", ".txt", ".html", ".htm", ".md", ".csv",
})


class KnowledgeBaseError(Exception):
    """Raised for caller-fixable problems (bad input, limit exceeded, wrong state)."""


# ── Lookup ────────────────────────────────────────────────────────────────────

def get_kb(db, agent_id: str) -> AgentKnowledgeBase | None:
    return (
        db.query(AgentKnowledgeBase)
        .filter(AgentKnowledgeBase.agent_id == agent_id)
        .first()
    )


def get_usable_kb(db, agent_id: str) -> AgentKnowledgeBase | None:
    """The knowledge base only if the runtime may search it."""
    kb = get_kb(db, agent_id)
    return kb if (kb and kb.is_usable) else None


# ── Provisioning ──────────────────────────────────────────────────────────────

def enable(agent_id: str) -> AgentKnowledgeBase:
    """Mark the knowledge base as provisioning and return the row.

    The actual calls run in :func:`provision`, dispatched to a background
    task so the HTTP request does not wait on bucket + data store creation.
    """
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if not agent:
            raise KnowledgeBaseError("Agent not found.")

        kb = get_kb(db, agent_id)
        if kb and kb.status == KB_STATUS_READY:
            return kb
        if kb and kb.status == KB_STATUS_PROVISIONING:
            raise KnowledgeBaseError("Knowledge base is already being provisioned.")

        if kb is None:
            kb = AgentKnowledgeBase(agent_id=agent_id)
            db.add(kb)
        kb.enabled = True
        kb.status = KB_STATUS_PROVISIONING
        kb.error_message = None
        kb.data_store_location = (
            os.environ.get("KB_DATA_STORE_LOCATION") or "us"
        ).strip().lower()
        kb.updated_at = datetime.utcnow()
        db.commit()
        db.refresh(kb)
        return kb
    finally:
        db.close()


def provision(agent_id: str) -> None:
    """Create the bucket and data store. Safe to retry.

    On partial failure the successfully-created resource is rolled back, so a
    retry does not leave an orphaned bucket billing storage for a knowledge base
    that never became usable.
    """
    db = SessionLocal()
    bucket_created: str | None = None
    try:
        agent = db.get(Agent, agent_id)
        kb = get_kb(db, agent_id)
        if not agent or not kb:
            logger.warning("kb_service: provision called for missing agent/kb %s", agent_id)
            return

        try:
            bucket_created = kb_store.create_namespace(agent_id, agent.name)
            store_id = kb_index.create_data_store(agent_id, kb.data_store_location)
        except Exception as exc:  # noqa: BLE001 — any failure lands the KB in `error`
            logger.exception("kb_service: provisioning failed for agent %s", agent_id)
            if bucket_created:
                try:
                    kb_store.delete_namespace(bucket_created)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "kb_service: rollback of bucket %s failed — may need manual cleanup",
                        bucket_created,
                    )
            kb.status = KB_STATUS_ERROR
            kb.error_message = str(exc)[:2000]
            kb.storage_namespace = None
            kb.data_store_id = None
            kb.updated_at = datetime.utcnow()
            db.commit()
            return

        kb.storage_namespace = bucket_created
        kb.data_store_id = store_id
        kb.status = KB_STATUS_READY
        kb.error_message = None
        kb.updated_at = datetime.utcnow()
        db.commit()
        logger.info(
            "kb_service: provisioned agent %s — bucket=%s store=%s",
            agent_id, bucket_created, store_id,
        )
    finally:
        db.close()


def disable(agent_id: str) -> None:
    """Tear down the data store and bucket, and forget the documents.

    This is a hard delete, not a pause — re-enabling provisions fresh. Cost stops
    immediately because both underlying resources are destroyed.
    """
    db = SessionLocal()
    try:
        kb = get_kb(db, agent_id)
        if not kb:
            return
        bucket_name, store_id = kb.storage_namespace, kb.data_store_id
        location = kb.data_store_location

        kb.status = KB_STATUS_DEPROVISIONING
        kb.updated_at = datetime.utcnow()
        db.commit()

        errors: list[str] = []
        if store_id:
            try:
                kb_index.delete_data_store(store_id, location)
            except Exception as exc:  # noqa: BLE001
                logger.exception("kb_service: data store deletion failed for %s", agent_id)
                errors.append(f"data store: {exc}")
        if bucket_name:
            try:
                kb_store.delete_namespace(bucket_name)
            except Exception as exc:  # noqa: BLE001
                logger.exception("kb_service: bucket deletion failed for %s", agent_id)
                errors.append(f"bucket: {exc}")

        if errors:
            # Leave the identifiers in place so a retry can finish the teardown —
            # clearing them would orphan resources that keep costing money.
            kb.status = KB_STATUS_ERROR
            kb.error_message = "Deprovisioning incomplete — " + "; ".join(errors)
            kb.updated_at = datetime.utcnow()
            db.commit()
            return

        db.query(AgentKnowledgeBaseDocument).filter(
            AgentKnowledgeBaseDocument.knowledge_base_id == kb.id
        ).delete(synchronize_session=False)
        kb.enabled = False
        kb.status = KB_STATUS_DISABLED
        kb.storage_namespace = None
        kb.data_store_id = None
        kb.error_message = None
        kb.updated_at = datetime.utcnow()
        db.commit()
        logger.info("kb_service: deprovisioned agent %s", agent_id)
    finally:
        db.close()


# ── Uploads ───────────────────────────────────────────────────────────────────

def validate_upload(filename: str, data: bytes, content_type: str) -> str:
    """Validate one file. Returns the MIME type to record.

    Extension and declared MIME type are both checked: browsers send an empty or
    wrong ``content-type`` often enough that trusting it alone would reject valid
    PDFs, while trusting the extension alone would accept anything renamed.
    """
    if not data:
        raise KnowledgeBaseError(f"{filename}: file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise KnowledgeBaseError(
            f"{filename}: exceeds the {MAX_FILE_BYTES // (1024 * 1024)}MB per-file limit."
        )

    ext = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
    declared = (content_type or "").split(";")[0].strip().lower()

    if ext not in ALLOWED_EXTENSIONS:
        raise KnowledgeBaseError(
            f"{filename}: unsupported file type. Allowed: "
            + ", ".join(sorted(ALLOWED_EXTENSIONS))
        )
    if declared and declared not in ALLOWED_MIME_TYPES:
        raise KnowledgeBaseError(f"{filename}: unsupported content type {declared!r}.")
    return declared or _mime_for_extension(ext)


def _mime_for_extension(ext: str) -> str:
    return {
        ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".doc": "application/msword",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".txt": "text/plain",
        ".html": "text/html",
        ".htm": "text/html",
        ".md": "text/markdown",
        ".csv": "text/csv",
    }.get(ext, "application/octet-stream")


def add_document(
    agent_id: str, filename: str, data: bytes, content_type: str, uploader_email: str
) -> AgentKnowledgeBaseDocument:
    """Store one document in the blob store and queue it for indexing."""
    mime = validate_upload(filename, data, content_type)
    content_hash = hashlib.sha256(data).hexdigest()

    db = SessionLocal()
    try:
        kb = get_kb(db, agent_id)
        if not kb or kb.status != KB_STATUS_READY or not kb.storage_namespace:
            raise KnowledgeBaseError("Knowledge base is not ready for uploads.")

        existing = (
            db.query(AgentKnowledgeBaseDocument)
            .filter(
                AgentKnowledgeBaseDocument.knowledge_base_id == kb.id,
                AgentKnowledgeBaseDocument.content_hash == content_hash,
            )
            .first()
        )
        if existing:
            raise KnowledgeBaseError(
                f"{filename}: identical content was already uploaded "
                f"as {existing.original_filename!r}."
            )

        count = (
            db.query(AgentKnowledgeBaseDocument)
            .filter(AgentKnowledgeBaseDocument.knowledge_base_id == kb.id)
            .count()
        )
        if count >= MAX_DOCUMENTS_PER_AGENT:
            raise KnowledgeBaseError(
                f"This knowledge base already holds the maximum of "
                f"{MAX_DOCUMENTS_PER_AGENT} documents."
            )

        used = (
            db.query(AgentKnowledgeBaseDocument)
            .filter(AgentKnowledgeBaseDocument.knowledge_base_id == kb.id)
            .with_entities(AgentKnowledgeBaseDocument.size_bytes)
            .all()
        )
        if sum(s or 0 for (s,) in used) + len(data) > MAX_TOTAL_BYTES_PER_AGENT:
            raise KnowledgeBaseError(
                f"This upload would exceed the per-agent storage cap of "
                f"{MAX_TOTAL_BYTES_PER_AGENT // (1024 * 1024)}MB."
            )

        obj_path = kb_store.object_path(content_hash, filename)
        kb_store.upload_document(kb.storage_namespace, obj_path, data, mime)

        doc = AgentKnowledgeBaseDocument(
            knowledge_base_id=kb.id,
            uploaded_by_email=(uploader_email or "").lower(),
            original_filename=filename[:512],
            object_path=obj_path,
            content_hash=content_hash,
            size_bytes=len(data),
            mime_type=mime,
            index_status=KB_DOC_PENDING,
        )
        db.add(doc)
        db.commit()
        db.refresh(doc)
        return doc
    finally:
        db.close()


def list_documents(agent_id: str) -> list[AgentKnowledgeBaseDocument]:
    db = SessionLocal()
    try:
        kb = get_kb(db, agent_id)
        if not kb:
            return []
        return (
            db.query(AgentKnowledgeBaseDocument)
            .filter(AgentKnowledgeBaseDocument.knowledge_base_id == kb.id)
            .order_by(AgentKnowledgeBaseDocument.created_at.desc())
            .all()
        )
    finally:
        db.close()


def delete_document(agent_id: str, doc_id: str) -> None:
    """Remove a document from the index, the bucket, and the database.

    Note the de-index is not instantaneous: like insertion, removal from search
    results propagates asynchronously, so a just-deleted document may briefly
    still match a query. Accepted for the same reason indexing latency is
    (spec §4) rather than blocking the response on a verification probe.
    """
    db = SessionLocal()
    try:
        kb = get_kb(db, agent_id)
        if not kb:
            raise KnowledgeBaseError("Knowledge base not found.")
        doc = (
            db.query(AgentKnowledgeBaseDocument)
            .filter(
                AgentKnowledgeBaseDocument.id == doc_id,
                # Scoped to this agent's KB so a guessed document ID from
                # another agent cannot be deleted.
                AgentKnowledgeBaseDocument.knowledge_base_id == kb.id,
            )
            .first()
        )
        if not doc:
            raise KnowledgeBaseError("Document not found.")

        if doc.index_document_id and kb.data_store_id:
            try:
                kb_index.delete_document(
                    kb.data_store_id,
                    doc.index_document_id,
                    location=kb.data_store_location,
                )
            except Exception:  # noqa: BLE001
                logger.exception("kb_service: index delete failed for doc %s", doc_id)
        if kb.storage_namespace:
            kb_store.delete_document(kb.storage_namespace, doc.object_path)

        db.delete(doc)
        db.commit()
    finally:
        db.close()


# ── Retrieval ─────────────────────────────────────────────────────────────────

def search_for_agent(agent_id: str, query: str, agent_name: str | None = None) -> list[dict]:
    """Search this agent's knowledge base.

    The data store is resolved from the agent's own row — never from a caller- or
    model-supplied identifier — which is what keeps one agent from reading
    another's documents.
    """
    db = SessionLocal()
    try:
        kb = get_usable_kb(db, agent_id)
        if not kb:
            return []
        store_id, location = kb.data_store_id, kb.data_store_location
    finally:
        db.close()

    results = kb_index.search(
        store_id,
        query,
        agent_id=agent_id,
        agent_name=agent_name,
        location=location,
    )
    _record_search_usage(agent_id)
    return results


def _record_search_usage(agent_id: str) -> None:
    """Count one billable the retrieval provider query against this agent.

    the retrieval provider bills queries at project level, so per-agent cost is
    derived from this counter rather than from billing export (spec §9).
    Best-effort: a usage-logging failure must not fail the user's search.
    """
    try:
        from kb_usage import record_kb_usage

        record_kb_usage(agent_id=agent_id, event_type="search", unit_count=1)
    except Exception:  # noqa: BLE001
        logger.debug("kb_service: usage recording skipped", exc_info=True)
