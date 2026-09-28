"""REST API for owner-scoped Flows — chains of skill.run steps.

Deliberately separate from api_workflows.py (agent-scoped "playbooks"), which
has a live ImportError on its HITL approve path (`workflow_runtime_hooks` does
not exist) and is reached by no UI. Flows share the same WorkflowDefinition /
WorkflowRun tables (see workflow_models.WORKFLOW_DEFINITION_KINDS) but are
owned by a user rather than an agent, and are driven entirely by
workflow_engine's skill.run step executor — never ai_action or
delegate_wait, so this router never touches api_workflows.py's broken path.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from auth_policy import AuthContext
from cf_access import build_auth_context
from database import SessionLocal
from tenancy import get_tenant_or_404
from workflow_engine import (
    advance_run,
    first_step_after_trigger,
    noop_llm_json,
    noop_post_slack,
    parse_graph,
)
from workflow_models import (
    WorkflowDefinition,
    WorkflowRun,
    WorkflowRunEvent,
    _json_dumps,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/flows", tags=["flows"])

# Tracked so an unheld background task cannot be garbage-collected mid-flight.
_flow_tasks: set[asyncio.Task] = set()


def _auth(request: Request) -> AuthContext:
    try:
        return build_auth_context(request.headers)
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Unauthorized") from exc


def _track(task: asyncio.Task) -> None:
    _flow_tasks.add(task)
    task.add_done_callback(_flow_tasks.discard)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _defn_to_dict(d: WorkflowDefinition) -> dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "slug": d.slug,
        "version": d.version,
        "status": d.status,
        "graph": json.loads(d.graph_json or "{}"),
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _run_to_dict(run: WorkflowRun) -> dict[str, Any]:
    return {
        "id": run.id,
        "definition_id": run.definition_id,
        "definition_version": run.definition_version,
        "status": run.status,
        "current_step": run.current_step,
        "context": run.context,
        "pending_gate": run.pending_gate,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "ended_at": run.ended_at.isoformat() if run.ended_at else None,
    }


class CreateFlowBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    graph: dict[str, Any] = Field(default_factory=dict)


class UpdateFlowBody(BaseModel):
    name: Optional[str] = None
    graph: Optional[dict[str, Any]] = None


class RunFlowBody(BaseModel):
    inputs: dict[str, Any] = Field(default_factory=dict)


def _get_flow_or_404(db: Any, flow_id: str, owner_sub: str) -> WorkflowDefinition:
    row = get_tenant_or_404(db, WorkflowDefinition, flow_id, owner_sub)
    if row.kind != "flow":
        raise HTTPException(status_code=404, detail="Not found")
    return row


@router.get("")
def list_flows(auth: AuthContext = Depends(_auth)) -> list[dict[str, Any]]:
    db = SessionLocal()
    try:
        rows = (
            db.query(WorkflowDefinition)
            .filter(WorkflowDefinition.owner_sub == auth.sub, WorkflowDefinition.kind == "flow")
            .order_by(WorkflowDefinition.updated_at.desc())
            .all()
        )
        return [_defn_to_dict(r) for r in rows]
    finally:
        db.close()


@router.post("", status_code=201)
def create_flow(
    body: CreateFlowBody, auth: AuthContext = Depends(_auth)
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = WorkflowDefinition(
            owner_sub=auth.sub,
            kind="flow",
            agent_id=None,
            slug=body.name.strip().lower().replace(" ", "-")[:120] or "flow",
            name=body.name.strip(),
            graph_json=_json_dumps(body.graph or {"nodes": [], "edges": []}),
            status="draft",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return _defn_to_dict(row)
    finally:
        db.close()


@router.get("/step-options")
async def step_options(auth: AuthContext = Depends(_auth)) -> dict[str, Any]:
    """Palette data the flow editor needs beyond what /api/skills already has:
    which accessible skills have an approved artifact (for the artifact-vs-
    agentic mode toggle).
    """
    from artifact_runtime import load_approved_artifacts
    from skill_router import resolve_user_skill_access

    _, _, accessible_slugs = await resolve_user_skill_access(auth.sub, auth.email)
    db = SessionLocal()
    try:
        artifacts = load_approved_artifacts(db, list(accessible_slugs))
        artifacts_by_slug: dict[str, list[dict[str, Any]]] = {}
        for art in artifacts:
            artifacts_by_slug.setdefault(art.slug, []).append(
                {
                    "name": art.name,
                    "version": art.version,
                    "summary": art.api_summary or f"{art.entrypoint}(...)",
                    "params_schema": json.loads(art.params_schema_json or "{}"),
                }
            )
        return {
            "accessible_skill_slugs": sorted(accessible_slugs),
            "artifacts_by_slug": artifacts_by_slug,
        }
    finally:
        db.close()


@router.get("/{flow_id}")
def get_flow(flow_id: str, auth: AuthContext = Depends(_auth)) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _get_flow_or_404(db, flow_id, auth.sub)
        return _defn_to_dict(row)
    finally:
        db.close()


@router.put("/{flow_id}")
def update_flow(
    flow_id: str,
    body: UpdateFlowBody,
    auth: AuthContext = Depends(_auth),
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        row = _get_flow_or_404(db, flow_id, auth.sub)
        if body.name is not None:
            row.name = body.name.strip() or row.name
        if body.graph is not None:
            row.graph_json = _json_dumps(body.graph)
        row.updated_at = _utcnow()
        db.commit()
        db.refresh(row)
        return _defn_to_dict(row)
    finally:
        db.close()


@router.delete("/{flow_id}", status_code=204)
def delete_flow(flow_id: str, auth: AuthContext = Depends(_auth)):
    db = SessionLocal()
    try:
        row = _get_flow_or_404(db, flow_id, auth.sub)
        db.query(WorkflowRun).filter(WorkflowRun.definition_id == row.id).delete()
        db.delete(row)
        db.commit()
    finally:
        db.close()
    return Response(status_code=204)


async def _run_flow_tracked(run_id: str, owner_sub: str, owner_email: str) -> None:
    try:
        await advance_run(
            None,
            run_id,
            llm_json=noop_llm_json,
            post_slack=noop_post_slack,
            owner_sub=owner_sub,
            owner_email=owner_email,
        )
    except Exception:
        logger.exception("flow run failed run_id=%s", run_id)


@router.post("/{flow_id}/run", status_code=202)
def run_flow(
    flow_id: str,
    body: RunFlowBody,
    auth: AuthContext = Depends(_auth),
) -> dict[str, Any]:
    db = SessionLocal()
    try:
        defn = _get_flow_or_404(db, flow_id, auth.sub)
        graph = parse_graph(defn)
        if not graph["nodes"]:
            raise HTTPException(status_code=400, detail="This flow has no steps")
        first = first_step_after_trigger(graph)
        if not first:
            raise HTTPException(status_code=400, detail="This flow has no trigger, or no step after it")

        run = WorkflowRun(
            owner_sub=auth.sub,
            agent_id=None,
            definition_id=defn.id,
            definition_version=defn.version,
            status="active",
            current_step=first,
            context_json=_json_dumps(dict(body.inputs or {})),
        )
        db.add(run)
        db.flush()
        db.add(
            WorkflowRunEvent(
                run_id=run.id,
                event_type="run_started",
                payload_json=_json_dumps({"inputs": list((body.inputs or {}).keys())}),
            )
        )
        db.commit()
        run_id = run.id
    finally:
        db.close()

    _track(asyncio.create_task(_run_flow_tracked(run_id, auth.sub, auth.email)))
    return {"run_id": run_id}


# /api/flow-runs/{id} lives outside the /api/flows prefix, so it gets its own
# router — registered separately in main.py alongside `router`.
flow_runs_router = APIRouter(prefix="/api/flow-runs", tags=["flows"])


@flow_runs_router.get("/{run_id}")
def get_flow_run(run_id: str, auth: AuthContext = Depends(_auth)) -> dict[str, Any]:
    db = SessionLocal()
    try:
        run = get_tenant_or_404(db, WorkflowRun, run_id, auth.sub)
        events = (
            db.query(WorkflowRunEvent)
            .filter(WorkflowRunEvent.run_id == run_id)
            .order_by(WorkflowRunEvent.created_at.asc())
            .all()
        )
        return {
            **_run_to_dict(run),
            "timeline": [
                {
                    "id": e.id,
                    "event_type": e.event_type,
                    "step_id": e.step_id,
                    "payload": json.loads(e.payload_json or "{}"),
                    "created_at": e.created_at.isoformat() if e.created_at else None,
                }
                for e in events
            ],
        }
    finally:
        db.close()
