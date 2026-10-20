// Typed client for backend/api_flows.py — owner-scoped chains of skill.run
// steps. See docs on WorkflowDefinition.kind="flow" for how this shares tables
// with (but never mixes with) the agent-scoped playbook engine.

import { apiFetch } from "@/lib/api";

export type FlowStepType = "skill.run";

export interface FlowGraphNode {
  id: string;
  type: string;
  config: Record<string, unknown>;
}

export interface FlowGraphEdge {
  from: string;
  to: string;
  on?: string;
}

export interface FlowGraph {
  nodes: FlowGraphNode[];
  edges: FlowGraphEdge[];
}

export interface Flow {
  id: string;
  name: string;
  slug: string;
  version: number;
  status: string;
  graph: FlowGraph;
  created_at: string | null;
  updated_at: string | null;
}

export interface FlowRunEvent {
  id: string;
  event_type: string;
  step_id: string | null;
  payload: Record<string, unknown>;
  created_at: string | null;
}

export interface FlowPendingGate {
  id: string;
  type: string;
  title?: string;
  reason?: string;
  session_id?: string;
  job_id?: string;
  interrupt_id?: string;
  step_id?: string;
  created_at?: string;
}

export interface FlowRun {
  id: string;
  definition_id: string;
  definition_version: number;
  status: string;
  current_step: string;
  context: Record<string, unknown>;
  pending_gate: FlowPendingGate | null;
  started_at: string | null;
  updated_at: string | null;
  ended_at: string | null;
}

export interface FlowRunDetail extends FlowRun {
  timeline: FlowRunEvent[];
}

export interface ArtifactOption {
  name: string;
  version: number;
  summary: string;
  params_schema: Record<string, unknown>;
}

export interface FlowStepOptions {
  accessible_skill_slugs: string[];
  artifacts_by_slug: Record<string, ArtifactOption[]>;
}

async function parseApiJson(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    const snippet = text.trim().slice(0, 160);
    throw new Error(res.ok ? "Invalid JSON from server." : snippet || `Request failed (${res.status})`);
  }
}

function apiErrorDetail(data: Record<string, unknown>, fallback: string): string {
  const detail = data.detail;
  if (typeof detail === "string" && detail.trim()) return detail;
  return fallback;
}

export async function listFlows(): Promise<Flow[]> {
  const res = await apiFetch("/api/flows");
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load flows."));
  return (data as unknown as Flow[]) ?? [];
}

export async function createFlow(name: string, graph?: FlowGraph): Promise<Flow> {
  const res = await apiFetch("/api/flows", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, graph: graph ?? { nodes: [], edges: [] } }),
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not create flow."));
  return data as unknown as Flow;
}

export async function getFlow(flowId: string): Promise<Flow> {
  const res = await apiFetch(`/api/flows/${flowId}`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load flow."));
  return data as unknown as Flow;
}

export async function updateFlow(
  flowId: string,
  updates: { name?: string; graph?: FlowGraph },
): Promise<Flow> {
  const res = await apiFetch(`/api/flows/${flowId}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(updates),
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not save flow."));
  return data as unknown as Flow;
}

export async function deleteFlow(flowId: string): Promise<void> {
  const res = await apiFetch(`/api/flows/${flowId}`, { method: "DELETE" });
  if (!res.ok && res.status !== 204) {
    const data = await parseApiJson(res);
    throw new Error(apiErrorDetail(data, "Could not delete flow."));
  }
}

export async function getFlowStepOptions(): Promise<FlowStepOptions> {
  const res = await apiFetch("/api/flows/step-options");
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load skills and tasks."));
  return data as unknown as FlowStepOptions;
}

export async function runFlow(
  flowId: string,
  inputs: Record<string, unknown> = {},
): Promise<{ run_id: string }> {
  const res = await apiFetch(`/api/flows/${flowId}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ inputs }),
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not start the flow."));
  return data as unknown as { run_id: string };
}

export async function getFlowRun(runId: string): Promise<FlowRunDetail> {
  const res = await apiFetch(`/api/flow-runs/${runId}`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load run."));
  return data as unknown as FlowRunDetail;
}
