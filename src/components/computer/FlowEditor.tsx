import { useEffect, useMemo, useState } from "react";
import { Loader2, Play, Plus, Save } from "lucide-react";
import type { SkillAccess } from "@/components/computer/SkillPicker";
import FlowStepCard from "@/components/computer/FlowStepCard";
import FlowRunTimeline from "@/components/computer/FlowRunTimeline";
import {
  type ArtifactOption,
  type Flow,
  type FlowGraphNode,
  runFlow,
  updateFlow,
} from "@/lib/flowsApi";

const TRIGGER_ID = "trigger";

function stepsFromGraph(flow: Flow): FlowGraphNode[] {
  return flow.graph.nodes.filter((n) => n.id !== TRIGGER_ID && n.type !== "terminal.resolve");
}

function graphFromSteps(steps: FlowGraphNode[]) {
  const nodes = [
    { id: TRIGGER_ID, type: "trigger.manual", config: {} },
    ...steps,
    { id: "term", type: "terminal.resolve", config: { status: "resolved" } },
  ];
  const chain = [TRIGGER_ID, ...steps.map((s) => s.id), "term"];
  const edges = chain.slice(0, -1).map((from, i) => ({ from, to: chain[i + 1] }));
  return { nodes, edges };
}

function newStepId(existing: FlowGraphNode[]): string {
  let n = existing.length + 1;
  while (existing.some((s) => s.id === `s${n}`)) n += 1;
  return `s${n}`;
}

type FlowEditorProps = {
  flow: Flow;
  onFlowUpdated: (flow: Flow) => void;
  skills: SkillAccess[];
  artifactsBySlug: Record<string, ArtifactOption[]>;
};

export default function FlowEditor({
  flow,
  onFlowUpdated,
  skills,
  artifactsBySlug,
}: FlowEditorProps) {
  const [steps, setSteps] = useState<FlowGraphNode[]>(() => stepsFromGraph(flow));
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    setSteps(stepsFromGraph(flow));
    setDirty(false);
  }, [flow.id]);

  const priorWritesByIndex = useMemo(() => {
    const acc: string[][] = [];
    let seen: string[] = [];
    for (const step of steps) {
      acc.push(seen);
      const writes = Array.isArray(step.config?.context_writes) ? (step.config.context_writes as string[]) : [];
      seen = [...seen, ...writes];
    }
    return acc;
  }, [steps]);

  const updateStep = (i: number, next: FlowGraphNode) => {
    setSteps((prev) => prev.map((s, idx) => (idx === i ? next : s)));
    setDirty(true);
  };

  const removeStep = (i: number) => {
    setSteps((prev) => prev.filter((_, idx) => idx !== i));
    setDirty(true);
  };

  const addStep = () => {
    setSteps((prev) => [...prev, { id: newStepId(prev), type: "skill.run", config: {} }]);
    setDirty(true);
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const graph = graphFromSteps(steps);
      const updated = await updateFlow(flow.id, { graph });
      onFlowUpdated(updated);
      setDirty(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save flow.");
    } finally {
      setSaving(false);
    }
  };

  const run = async () => {
    setStarting(true);
    setError(null);
    try {
      if (dirty) await save();
      const { run_id } = await runFlow(flow.id, {});
      setRunId(run_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not start the flow.");
    } finally {
      setStarting(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-3 py-2">
        <span className="truncate text-sm font-bold text-slate-900">{flow.name}</span>
        <span className="ml-auto flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => void save()}
            disabled={saving || !dirty}
            className="inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50 disabled:opacity-40"
          >
            {saving ? <Loader2 className="h-3 w-3 animate-spin" /> : <Save className="h-3 w-3" />} Save
          </button>
          <button
            type="button"
            onClick={() => void run()}
            disabled={starting || steps.length === 0}
            className="inline-flex items-center gap-1 rounded-md bg-[#FF444F] px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-[#e63c46] disabled:opacity-40"
          >
            {starting ? <Loader2 className="h-3 w-3 animate-spin" /> : <Play className="h-3 w-3" />} Run
          </button>
        </span>
      </div>

      {error && (
        <div className="shrink-0 border-b border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>
      )}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {runId ? (
          <FlowRunTimeline runId={runId} onClose={() => setRunId(null)} />
        ) : (
          <div className="space-y-3">
            {steps.length === 0 && (
              <p className="rounded-xl border border-dashed border-slate-200 py-8 text-center text-xs text-slate-400">
                No steps yet. Add a skill step to get started.
              </p>
            )}
            {steps.map((step, i) => (
              <FlowStepCard
                key={step.id}
                step={step}
                index={i}
                priorWrites={priorWritesByIndex[i]}
                onChange={(next) => updateStep(i, next)}
                onRemove={() => removeStep(i)}
                skills={skills}
                artifactsBySlug={artifactsBySlug}
              />
            ))}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => addStep()}
                className="inline-flex flex-1 items-center justify-center gap-1 rounded-lg border border-dashed border-slate-300 py-2 text-xs font-semibold text-slate-500 hover:border-slate-400 hover:text-slate-700"
              >
                <Plus className="h-3.5 w-3.5" /> Skill step
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
