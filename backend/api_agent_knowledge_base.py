"""HTTP API for per-agent knowledge bases.

Endpoints (all behind the existing Cloudflare Access JWT middleware, all scoped
to agent owner/collaborator):

    POST   /api/agents/{agent_id}/knowledge-base/enable
    POST   /api/agents/{agent_id}/knowledge-base/disable
    GET    /api/agents/{agent_id}/knowledge-base
    POST   /api/agents/{agent_id}/knowledge-base/documents
    GET    /api/agents/{agent_id}/knowledge-base/documents
    DELETE /api/agents/{agent_id}/knowledge-base/documents/{doc_id}

Security posture: every endpoint resolves the target data store and bucket from
the agent row after an ownership check. No endpoint accepts a bucket name or
data store ID from the caller — that is what keeps agents' documents isolated
from each other (see AGENT_KNOWLEDGE_BASE_SPEC.md §5.2, §8).
"""

from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    Request,
    UploadFile,
)

import agent_knowledge_base_service as kb_service
from agents import Agent, kb_document_to_dict, kb_to_dict
from api_agents import (
    _my_access_status,
    _user_email_from_request,
    deps_owner_sub,
)
from database import SessionLocal

logger = logging.getLogger(__name__)

router = APIRouter()

# Only owners and collaborators may manage a knowledge base. "approved" users
# (read-only access to the agent) must not be able to add or delete documents,
# since documents change how the agent answers everyone.
_MANAGE_ACCESS = ("owner", "collaborator")


def _require_manage_access(agent_id: str, request: Request, owner_sub: str) -> Agent:
    """Load the agent and assert the caller may manage its knowledge base."""
    db = SessionLocal()
    try:
        agent = db.get(Agent, agent_id)
        if not agent or agent.status == "decommissioned":
            raise HTTPException(status_code=404, detail="Agent not found.")
        access = _my_access_status(
            agent, owner_sub, _user_email_from_request(request), db
        )
        if access not in _MANAGE_ACCESS:
            raise HTTPException(status_code=403, detail="Access denied.")
        # Detach a plain copy: the session closes before the caller uses it.
        return Agent(id=agent.id, name=agent.name)
    finally:
        db.close()


@router.get("/api/agents/{agent_id}/knowledge-base")
def get_knowledge_base(
    agent_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub)
):
    _require_manage_access(agent_id, request, owner_sub)
    db = SessionLocal()
    try:
        kb = kb_service.get_kb(db, agent_id)
        payload = kb_to_dict(kb)
        payload["limits"] = {
            "max_file_bytes": kb_service.MAX_FILE_BYTES,
            "max_files_per_request": kb_service.MAX_FILES_PER_REQUEST,
            "max_documents": kb_service.MAX_DOCUMENTS_PER_AGENT,
            "max_total_bytes": kb_service.MAX_TOTAL_BYTES_PER_AGENT,
            "allowed_extensions": sorted(kb_service.ALLOWED_EXTENSIONS),
        }
        return payload
    finally:
        db.close()


@router.post("/api/agents/{agent_id}/knowledge-base/enable")
def enable_knowledge_base(
    agent_id: str,
    request: Request,
    background: BackgroundTasks,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Provision a bucket + data store for this agent.

    Returns immediately with ``status: provisioning``; the calls run in the
    background because bucket and data store creation together can take several
    seconds. The client polls GET /knowledge-base until ``ready`` or ``error``.
    """
    _require_manage_access(agent_id, request, owner_sub)
    try:
        kb = kb_service.enable(agent_id)
    except kb_service.KnowledgeBaseError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    if kb.status == kb_service.KB_STATUS_PROVISIONING:
        background.add_task(kb_service.provision, agent_id)
    return kb_to_dict(kb)


@router.post("/api/agents/{agent_id}/knowledge-base/disable")
def disable_knowledge_base(
    agent_id: str,
    request: Request,
    background: BackgroundTasks,
    owner_sub: str = Depends(deps_owner_sub),
):
    """Destroy this agent's data store, bucket, and document records.

    This is a hard delete, not a pause — all uploaded documents are lost and
    re-enabling starts empty. cost stops as soon as teardown completes.
    """
    _require_manage_access(agent_id, request, owner_sub)
    background.add_task(kb_service.disable, agent_id)
    return {"status": kb_service.KB_STATUS_DEPROVISIONING}


@router.post("/api/agents/{agent_id}/knowledge-base/documents")
async def upload_documents(
    agent_id: str,
    request: Request,
    background: BackgroundTasks,
    files: list[UploadFile] = File(...),
    owner_sub: str = Depends(deps_owner_sub),
):
    """Upload one or more documents.

    Each file is validated and stored independently, so one rejected file does
    not discard the rest of the batch — the response reports per-file outcomes.

    Indexing is asynchronous — the response returns before it completes, because
    an import LRO takes 30 seconds to 15 minutes. It is kicked off immediately in
    the background here so documents do not sit until the worker's next poll; the
    `kb-ingest-worker` deployment remains the durable path that retries anything
    this process fails to finish (e.g. if the pod is recycled mid-import).
    """
    _require_manage_access(agent_id, request, owner_sub)
    uploader = _user_email_from_request(request)

    if not files:
        raise HTTPException(status_code=400, detail="No files supplied.")
    if len(files) > kb_service.MAX_FILES_PER_REQUEST:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Too many files in one request "
                f"(limit {kb_service.MAX_FILES_PER_REQUEST})."
            ),
        )

    accepted: list[dict] = []
    rejected: list[dict] = []
    for upload in files:
        filename = upload.filename or "document"
        try:
            data = await upload.read()
            doc = kb_service.add_document(
                agent_id, filename, data, upload.content_type or "", uploader
            )
            accepted.append(kb_document_to_dict(doc))
        except kb_service.KnowledgeBaseError as exc:
            rejected.append({"filename": filename, "error": str(exc)})
        except Exception:  # noqa: BLE001
            # Never surface an internal error verbatim to the client.
            logger.exception("kb_api: upload failed for agent %s (%s)", agent_id, filename)
            rejected.append({"filename": filename, "error": "Upload failed."})
        finally:
            await upload.close()

    if not accepted and rejected:
        raise HTTPException(status_code=400, detail={"rejected": rejected})

    if accepted:
        # Start importing straight away. Both this and the worker claim rows via
        # the same compare-and-set lease, so concurrent runs cannot double-import.
        db = SessionLocal()
        try:
            kb = kb_service.get_kb(db, agent_id)
            kb_id = kb.id if kb else None
        finally:
            db.close()
        if kb_id:
            from kb_ingest_worker import ingest_pending_for_kb

            background.add_task(ingest_pending_for_kb, kb_id)

    return {"accepted": accepted, "rejected": rejected}


@router.get("/api/agents/{agent_id}/knowledge-base/documents")
def list_knowledge_base_documents(
    agent_id: str, request: Request, owner_sub: str = Depends(deps_owner_sub)
):
    """List documents and their index status.

    This is a plain database read — the client may poll it while documents are
    indexing without incurring any cost.
    """
    _require_manage_access(agent_id, request, owner_sub)
    docs = kb_service.list_documents(agent_id)
    return {"documents": [kb_document_to_dict(d) for d in docs]}


@router.delete("/api/agents/{agent_id}/knowledge-base/documents/{doc_id}")
def delete_knowledge_base_document(
    agent_id: str,
    doc_id: str,
    request: Request,
    owner_sub: str = Depends(deps_owner_sub),
):
    _require_manage_access(agent_id, request, owner_sub)
    try:
        kb_service.delete_document(agent_id, doc_id)
    except kb_service.KnowledgeBaseError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": doc_id}
