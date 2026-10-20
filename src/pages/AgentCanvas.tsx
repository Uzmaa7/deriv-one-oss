/**
 * AgentCanvas — visual multi-agent workflow builder.
 *
 * Renders all agents as draggable React Flow nodes and derives directed edges
 * from the `can_invoke` field on each agent.  Drag from any node's right handle
 * to another node to create a connection; click an edge to delete it.  All
 * mutations call PUT /api/agents/{id}/orchestration and update both sides of the
 * ACL pair (can_invoke on the source, can_be_invoked_by on the target).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  addEdge,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type Connection,
  type NodeProps,
  Handle,
  Position,
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
  MarkerType,
  useReactFlow,
  ReactFlowProvider,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertCircle, Loader2, Sparkles, Trash2, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import {
  AgentRecord,
  statusDotColor,
  statusChipClasses,
  statusLabel,
} from "../data/agents";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AgentNodeData extends Record<string, unknown> {
  agent: AgentRecord;
  onDelete: (id: string) => void;
}

// ── Layout helper ─────────────────────────────────────────────────────────────

const NODE_W = 220;
const NODE_H = 90;
const COL_GAP = 80;
const ROW_GAP = 60;
const COLS = 4;

function gridPositions(agents: AgentRecord[]): { x: number; y: number }[] {
  return agents.map((_, i) => ({
    x: (i % COLS) * (NODE_W + COL_GAP) + 40,
    y: Math.floor(i / COLS) * (NODE_H + ROW_GAP) + 40,
  }));
}

// ── Build initial nodes and edges from agent list ─────────────────────────────

function buildNodes(agents: AgentRecord[], onDelete: (id: string) => void): Node[] {
  const positions = gridPositions(agents);
  return agents.map((agent, i) => ({
    id: agent.id,
    type: "agentNode",
    position: positions[i],
    data: { agent, onDelete } satisfies AgentNodeData,
    width: NODE_W,
    height: NODE_H,
  }));
}

function buildEdges(agents: AgentRecord[]): Edge[] {
  const edges: Edge[] = [];
  const agentIds = new Set(agents.map(a => a.id));
  for (const agent of agents) {
    for (const targetId of agent.can_invoke) {
      if (agentIds.has(targetId)) {
        edges.push({
          id: `${agent.id}->${targetId}`,
          source: agent.id,
          target: targetId,
          type: "deletableEdge",
          markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
          style: { strokeWidth: 2, stroke: "#6366f1" },
        });
      }
    }
  }
  return edges;
}

// ── Orchestration API calls ───────────────────────────────────────────────────

async function putOrchestration(
  agentId: string,
  canInvoke: string[],
  canBeInvokedBy: string[],
): Promise<AgentRecord | null> {
  const res = await apiFetch(`/api/agents/${agentId}/orchestration`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ can_invoke: canInvoke, can_be_invoked_by: canBeInvokedBy }),
  });
  if (!res.ok) return null;
  return res.json();
}

// ── Custom AgentNode ──────────────────────────────────────────────────────────

function AgentNode({ data }: NodeProps) {
  const { agent } = data as AgentNodeData;
  const navigate = useNavigate();

  const typeLabel: Record<string, string> = {
    interactive: "Interactive",
    scheduled: "Scheduled",
    orchestrator: "Orchestrator",
  };
  const typeBadge: Record<string, string> = {
    interactive: "bg-blue-50 text-blue-700 border-blue-200",
    scheduled: "bg-amber-50 text-amber-700 border-amber-200",
    orchestrator: "bg-purple-50 text-purple-700 border-purple-200",
  };

  return (
    <div
      className="bg-white border-2 border-slate-200 rounded-xl shadow-sm hover:shadow-md hover:border-indigo-300 transition-all cursor-default select-none"
      style={{ width: NODE_W, minHeight: NODE_H }}
    >
      {/* Target handle — left side */}
      <Handle
        type="target"
        position={Position.Left}
        className="!w-3 !h-3 !bg-indigo-400 !border-white !border-2"
      />

      <div className="px-3 py-2.5 flex flex-col gap-1.5">
        {/* Name + status */}
        <div className="flex items-center gap-1.5 min-w-0">
          <span
            className={`w-2 h-2 rounded-full shrink-0 ${statusDotColor(agent.status)}`}
          />
          <button
            className="text-sm font-bold text-slate-900 truncate hover:underline text-left"
            onClick={() => navigate(`/agents/${agent.id}`)}
          >
            {agent.name}
          </button>
        </div>

        {/* Type badge + status chip */}
        <div className="flex items-center gap-1.5 flex-wrap">
          <span
            className={`text-[10px] font-semibold border rounded-full px-1.5 py-0.5 ${
              typeBadge[agent.agent_type] ?? "bg-slate-50 text-slate-600 border-slate-200"
            }`}
          >
            {typeLabel[agent.agent_type] ?? agent.agent_type}
          </span>
          <span
            className={`inline-flex items-center gap-1 text-[10px] font-medium border rounded-full px-1.5 py-0.5 ${statusChipClasses(agent.status)}`}
          >
            {statusLabel(agent.status)}
          </span>
        </div>

        {/* Connection counts */}
        {(agent.can_invoke.length > 0 || agent.can_be_invoked_by.length > 0) && (
          <p className="text-[10px] text-slate-400 leading-tight">
            {agent.can_invoke.length > 0 && `Invokes ${agent.can_invoke.length}`}
            {agent.can_invoke.length > 0 && agent.can_be_invoked_by.length > 0 && " · "}
            {agent.can_be_invoked_by.length > 0 &&
              (agent.can_be_invoked_by[0] === "*"
                ? "Invokable by any"
                : `Invokable by ${agent.can_be_invoked_by.length}`)}
          </p>
        )}
      </div>

      {/* Source handle — right side */}
      <Handle
        type="source"
        position={Position.Right}
        className="!w-3 !h-3 !bg-indigo-500 !border-white !border-2"
        title="Drag to connect"
      />
    </div>
  );
}

// ── Custom deletable edge ─────────────────────────────────────────────────────

function DeletableEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style,
  markerEnd,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getBezierPath({
    sourceX, sourceY, sourcePosition, targetX, targetY, targetPosition,
  });

  const onDelete = (data as { onDelete?: (id: string) => void } | undefined)?.onDelete;

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: "absolute",
            transform: `translate(-50%, -50%) translate(${labelX}px,${labelY}px)`,
            pointerEvents: "all",
          }}
          className="nodrag nopan"
        >
          <button
            onClick={() => onDelete?.(id)}
            className="flex items-center justify-center w-5 h-5 rounded-full bg-white border border-red-200 text-red-500 hover:bg-red-50 shadow-sm opacity-0 group-hover:opacity-100 transition-opacity"
            title="Delete connection"
          >
            <X className="w-2.5 h-2.5" />
          </button>
        </div>
      </EdgeLabelRenderer>
    </>
  );
}

// ── Toast notification ────────────────────────────────────────────────────────

function Toast({ message, onDismiss }: { message: string; onDismiss: () => void }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 4000);
    return () => clearTimeout(t);
  }, [onDismiss]);
  return (
    <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-50 flex items-center gap-2 bg-red-600 text-white text-xs font-medium px-3 py-2 rounded-lg shadow-lg">
      <AlertCircle className="w-3.5 h-3.5 shrink-0" />
      <span>{message}</span>
      <button onClick={onDismiss} className="ml-1 opacity-75 hover:opacity-100">
        <X className="w-3 h-3" />
      </button>
    </div>
  );
}

// ── Inner canvas (needs ReactFlowProvider context) ────────────────────────────

const nodeTypes = { agentNode: AgentNode };
const edgeTypes = { deletableEdge: DeletableEdge };

interface InnerCanvasProps {
  agents: AgentRecord[];
  onAgentsChange: (updater: (prev: AgentRecord[]) => AgentRecord[]) => void;
}

function InnerCanvas({ agents, onAgentsChange }: InnerCanvasProps) {
  const [toast, setToast] = useState<string | null>(null);

  // Derive a stable agent map for quick lookups
  const agentMap = useMemo(() => {
    const m = new Map<string, AgentRecord>();
    for (const a of agents) m.set(a.id, a);
    return m;
  }, [agents]);

  // ── React Flow state ──────────────────────────────────────────────────────

  const deleteEdge = useCallback(
    (edgeId: string) => {
      setEdges(eds => eds.filter(e => e.id !== edgeId));
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );

  const initialNodes = useMemo(
    () => buildNodes(agents, deleteEdge),
    // Only run on first render — positions are local after that
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [],
  );
  const initialEdges = useMemo(() => buildEdges(agents), []);  // eslint-disable-line react-hooks/exhaustive-deps

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  // Inject the delete handler into edge data whenever edges change
  const edgesWithDeleteHandler = useMemo(
    () =>
      edges.map(e => ({
        ...e,
        data: { ...((e.data as object | undefined) ?? {}), onDelete: handleEdgeDelete },
      })),
    // handleEdgeDelete is defined below — memo deps are stable via ref
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [edges],
  );

  // Keep a ref to the latest agentMap and onAgentsChange to avoid stale closures
  const agentMapRef = useRef(agentMap);
  useEffect(() => { agentMapRef.current = agentMap; }, [agentMap]);
  const onAgentsChangeRef = useRef(onAgentsChange);
  useEffect(() => { onAgentsChangeRef.current = onAgentsChange; }, [onAgentsChange]);

  // ── Create connection ─────────────────────────────────────────────────────

  const onConnect = useCallback(
    async (connection: Connection) => {
      const { source, target } = connection;
      if (!source || !target || source === target) return;

      const sourceAgent = agentMapRef.current.get(source);
      const targetAgent = agentMapRef.current.get(target);
      if (!sourceAgent || !targetAgent) return;

      // Guard: already connected
      if (sourceAgent.can_invoke.includes(target)) return;

      // Optimistic update
      const newEdge: Edge = {
        id: `${source}->${target}`,
        source,
        target,
        type: "deletableEdge",
        markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
        style: { strokeWidth: 2, stroke: "#6366f1" },
      };
      setEdges(eds => addEdge(newEdge, eds));

      // Persist both sides of the ACL
      const newSourceCanInvoke = [...sourceAgent.can_invoke, target];
      const newTargetCanBeInvokedBy = targetAgent.can_be_invoked_by.includes("*")
        ? targetAgent.can_be_invoked_by
        : [...targetAgent.can_be_invoked_by.filter(id => id !== "*"), source];

      const [updatedSource, updatedTarget] = await Promise.all([
        putOrchestration(source, newSourceCanInvoke, sourceAgent.can_be_invoked_by),
        putOrchestration(target, targetAgent.can_invoke, newTargetCanBeInvokedBy),
      ]);

      if (!updatedSource || !updatedTarget) {
        // Revert optimistic update
        setEdges(eds => eds.filter(e => e.id !== newEdge.id));
        setToast("Could not save connection. Please try again.");
        return;
      }

      onAgentsChangeRef.current(prev =>
        prev.map(a => {
          if (a.id === source) return updatedSource;
          if (a.id === target) return updatedTarget;
          return a;
        }),
      );
    },
    [setEdges],
  );

  // ── Delete connection ─────────────────────────────────────────────────────

  async function handleEdgeDelete(edgeId: string) {
    const [source, target] = edgeId.split("->");
    const sourceAgent = agentMapRef.current.get(source);
    const targetAgent = agentMapRef.current.get(target);
    if (!sourceAgent || !targetAgent) return;

    // Optimistic update
    setEdges(eds => eds.filter(e => e.id !== edgeId));

    const newSourceCanInvoke = sourceAgent.can_invoke.filter(id => id !== target);
    const newTargetCanBeInvokedBy = targetAgent.can_be_invoked_by.filter(id => id !== source);

    const [updatedSource, updatedTarget] = await Promise.all([
      putOrchestration(source, newSourceCanInvoke, sourceAgent.can_be_invoked_by),
      putOrchestration(target, targetAgent.can_invoke, newTargetCanBeInvokedBy),
    ]);

    if (!updatedSource || !updatedTarget) {
      // Revert: re-add the edge
      setEdges(eds => [
        ...eds,
        {
          id: edgeId,
          source,
          target,
          type: "deletableEdge",
          markerEnd: { type: MarkerType.ArrowClosed, width: 18, height: 18 },
          style: { strokeWidth: 2, stroke: "#6366f1" },
        },
      ]);
      setToast("Could not remove connection. Please try again.");
      return;
    }

    onAgentsChangeRef.current(prev =>
      prev.map(a => {
        if (a.id === source) return updatedSource;
        if (a.id === target) return updatedTarget;
        return a;
      }),
    );
  }

  return (
    <div className="relative w-full h-full">
      <ReactFlow
        nodes={nodes}
        edges={edgesWithDeleteHandler}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.25}
        maxZoom={2}
        proOptions={{ hideAttribution: true }}
        className="group"
      >
        <Background gap={20} size={1} color="#e2e8f0" />
        <Controls className="!shadow-none !border !border-slate-200 !rounded-xl overflow-hidden" />
        <MiniMap
          nodeColor={node => {
            const a = agentMap.get(node.id);
            if (!a) return "#e2e8f0";
            const colors: Record<string, string> = {
              active: "#10b981",
              paused: "#f59e0b",
              decommissioned: "#94a3b8",
              registered: "#cbd5e1",
            };
            return colors[a.status] ?? "#cbd5e1";
          }}
          className="!border !border-slate-200 !rounded-xl overflow-hidden !shadow-none"
          pannable
          zoomable
        />
      </ReactFlow>

      {/* Legend */}
      <div className="absolute top-3 right-3 bg-white/90 backdrop-blur-sm border border-slate-200 rounded-lg px-3 py-2 text-[10px] text-slate-500 space-y-1 shadow-sm z-10">
        <p className="font-semibold text-slate-700 mb-1">Legend</p>
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-4 h-0.5 bg-indigo-500" />
          can invoke
        </div>
        <div className="flex items-center gap-1.5">
          <span className="inline-block w-3 h-3 rounded-full bg-indigo-500 border-2 border-white shadow-sm" />
          drag to connect
        </div>
        <div className="flex items-center gap-1.5">
          <X className="w-2.5 h-2.5 text-red-500" />
          click edge to delete
        </div>
      </div>

      {toast && <Toast message={toast} onDismiss={() => setToast(null)} />}
    </div>
  );
}

// ── Public AgentCanvas (wraps provider) ──────────────────────────────────────

interface AgentCanvasProps {
  agents: AgentRecord[];
  loading: boolean;
  error: string | null;
  onAgentsChange: (updater: (prev: AgentRecord[]) => AgentRecord[]) => void;
}

export default function AgentCanvas({
  agents,
  loading,
  error,
  onAgentsChange,
}: AgentCanvasProps) {
  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex-1 flex items-center justify-center h-full px-6">
        <div className="flex items-center gap-2 bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3">
          <AlertCircle className="w-4 h-4 shrink-0" />
          {error}
        </div>
      </div>
    );
  }

  if (agents.length === 0) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center h-full text-center">
        <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center mb-4">
          <Sparkles className="w-6 h-6 text-slate-400" />
        </div>
        <h3 className="text-base font-bold text-slate-900 mb-2">No agents yet</h3>
        <p className="text-sm text-slate-500 max-w-xs leading-relaxed">
          Register an agent first, then return here to build multi-agent workflows.
        </p>
      </div>
    );
  }

  return (
    <ReactFlowProvider>
      <InnerCanvas agents={agents} onAgentsChange={onAgentsChange} />
    </ReactFlowProvider>
  );
}
