import { useEffect, useState } from "react";
import { Loader2, Plus, Workflow } from "lucide-react";
import { apiFetch } from "@/lib/api";
import type { SkillAccess } from "@/components/computer/SkillPicker";
import FlowEditor from "@/components/computer/FlowEditor";
import {
  type Flow,
  type FlowStepOptions,
  createFlow,
  getFlowStepOptions,
  listFlows,
} from "@/lib/flowsApi";

function fmtTime(iso: string | null) {
  if (!iso) return "";
  return new Date(iso.endsWith("Z") ? iso : `${iso}Z`).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export default function FlowsTab() {
  const [flows, setFlows] = useState<Flow[]>([]);
  const [selected, setSelected] = useState<Flow | null>(null);
  const [skills, setSkills] = useState<SkillAccess[]>([]);
  const [stepOptions, setStepOptions] = useState<FlowStepOptions | null>(null);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const [flowList, skillsRes] = await Promise.all([
          listFlows(),
          apiFetch("/api/skills").then((r) => (r.ok ? r.json() : { skills: [] })),
        ]);
        if (cancelled) return;
        setFlows(flowList);
        setSkills((skillsRes?.skills as SkillAccess[]) ?? []);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load flows.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const openFlow = async (flow: Flow) => {
    setSelected(flow);
    if (!stepOptions) {
      try {
        setStepOptions(await getFlowStepOptions());
      } catch (e) {
        setError(e instanceof Error ? e.message : "Could not load skill/task options.");
      }
    }
  };

  const create = async () => {
    const name = window.prompt("Flow name")?.trim();
    if (!name) return;
    setCreating(true);
    setError(null);
    try {
      const flow = await createFlow(name);
      setFlows((prev) => [flow, ...prev]);
      void openFlow(flow);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not create flow.");
    } finally {
      setCreating(false);
    }
  };

  if (selected) {
    return (
      <div className="flex min-h-0 flex-1 flex-col">
        <button
          type="button"
          onClick={() => setSelected(null)}
          className="shrink-0 border-b border-slate-100 px-3 py-2 text-left text-[11px] font-semibold text-slate-500 hover:text-slate-800"
        >
          ← All flows
        </button>
        {stepOptions ? (
          <FlowEditor
            flow={selected}
            onFlowUpdated={(f) => {
              setSelected(f);
              setFlows((prev) => prev.map((x) => (x.id === f.id ? f : x)));
            }}
            skills={skills}
            artifactsBySlug={stepOptions.artifacts_by_slug}
          />
        ) : (
          <div className="flex flex-1 items-center justify-center text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-3 py-2">
        <span className="text-sm font-bold text-slate-900">Flows</span>
        <span className="text-[11px] text-slate-400">Chain skill steps</span>
        <button
          type="button"
          onClick={() => void create()}
          disabled={creating}
          className="ml-auto inline-flex items-center gap-1 rounded-md bg-[#FF444F] px-2.5 py-1 text-[11px] font-semibold text-white hover:bg-[#e63c46] disabled:opacity-40"
        >
          {creating ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />} New flow
        </button>
      </div>

      {error && <div className="shrink-0 border-b border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      <div className="min-h-0 flex-1 overflow-y-auto p-3">
        {loading ? (
          <div className="flex justify-center py-10 text-slate-400">
            <Loader2 className="h-4 w-4 animate-spin" />
          </div>
        ) : flows.length === 0 ? (
          <div className="flex flex-col items-center gap-2 py-10 text-center text-slate-400">
            <Workflow className="h-8 w-8" />
            <p className="text-xs">
              No flows yet. Create one to chain a skill's output into a browser task, or vice versa.
            </p>
          </div>
        ) : (
          <div className="space-y-1.5">
            {flows.map((flow) => (
              <button
                key={flow.id}
                type="button"
                onClick={() => void openFlow(flow)}
                className="flex w-full items-center gap-2 rounded-xl border border-slate-200 px-3 py-2.5 text-left hover:border-slate-300"
              >
                <Workflow className="h-4 w-4 shrink-0 text-slate-400" />
                <span className="min-w-0 flex-1 truncate text-sm font-semibold text-slate-800">{flow.name}</span>
                <span className="shrink-0 text-[11px] text-slate-400">{fmtTime(flow.updated_at)}</span>
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
