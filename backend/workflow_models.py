"""SQLAlchemy models for Agent Workflows (vertical slice)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text

from database import Base, OWNER_LEGACY_SENTINEL, _uuid

WORKFLOW_DEFINITION_STATUSES = ("draft", "published")
WORKFLOW_RUN_STATUSES = (
    "active",
    "waiting",
    "pending_hitl",
    "resolved",
    "manual_review",
    "cancelled",
)
# "playbook" = the original agent-scoped, Slack-triggered graphs (api_workflows.py).
# "flow" = owner-scoped chains of skill.run steps (api_flows.py).
WORKFLOW_DEFINITION_KINDS = ("playbook", "flow")


def _json_loads(raw: Optional[str], default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _json_dumps(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class WorkflowDefinition(Base):
    __tablename__ = "workflow_definitions"

    id = Column(String, primary_key=True, default=_uuid)
    # NULL for owner-scoped flows (kind="flow"); set for agent-scoped playbooks.
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True, index=True)
    # Real user sub for kind="flow"; OWNER_LEGACY_SENTINEL for pre-existing
    # playbook rows.
    owner_sub = Column(String, nullable=False, index=True, default=lambda: OWNER_LEGACY_SENTINEL)
    kind = Column(String(16), nullable=False, default="playbook")
    slug = Column(String(120), nullable=False)
    name = Column(String(200), nullable=False, default="")
    version = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="draft")
    graph_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    published_at = Column(DateTime, nullable=True)

    __table_args__ = (
        {"sqlite_autoincrement": True},
    )


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id = Column(String, primary_key=True, default=_uuid)
    # NULL for flow runs; set for playbook runs.
    agent_id = Column(String, ForeignKey("agents.id"), nullable=True, index=True)
    owner_sub = Column(String, nullable=False, index=True, default=lambda: OWNER_LEGACY_SENTINEL)
    definition_id = Column(String, ForeignKey("workflow_definitions.id"), nullable=False, index=True)
    definition_version = Column(Integer, nullable=False, default=1)
    status = Column(String(24), nullable=False, default="active", index=True)
    current_step = Column(String(80), nullable=False, default="")
    context_json = Column(Text, nullable=False, default="{}")
    pending_gate_json = Column(Text, nullable=True)
    started_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at = Column(DateTime, nullable=True)

    @property
    def context(self) -> dict[str, Any]:
        return _json_loads(self.context_json, {})

    def set_context(self, ctx: dict[str, Any]) -> None:
        self.context_json = _json_dumps(ctx)

    @property
    def pending_gate(self) -> Optional[dict[str, Any]]:
        return _json_loads(self.pending_gate_json, None) if self.pending_gate_json else None

    def set_pending_gate(self, gate: Optional[dict[str, Any]]) -> None:
        self.pending_gate_json = _json_dumps(gate) if gate else None


class WorkflowRunEvent(Base):
    __tablename__ = "workflow_run_events"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    event_type = Column(String(64), nullable=False)
    step_id = Column(String(80), nullable=True)
    payload_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkflowWaitBinding(Base):
    __tablename__ = "workflow_wait_bindings"

    id = Column(String, primary_key=True, default=_uuid)
    run_id = Column(String, ForeignKey("workflow_runs.id"), nullable=False, index=True)
    wait_id = Column(String, nullable=False)
    kind = Column(String(32), nullable=False, default="slack_thread")
    channel_id = Column(String(32), nullable=True, index=True)
    thread_ts = Column(String(32), nullable=True, index=True)
    expected_senders_json = Column(Text, nullable=False, default="[]")
    expires_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


def init_workflow_tables(engine) -> None:
    Base.metadata.create_all(
        engine,
        tables=[
            WorkflowDefinition.__table__,
            WorkflowRun.__table__,
            WorkflowRunEvent.__table__,
            WorkflowWaitBinding.__table__,
        ],
    )
