import { useEffect, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, X } from "lucide-react";
import { type FlowRunDetail, getFlowRun } from "@/lib/flowsApi";

const TERMINAL_STATUSES = new Set(["resolved", "manual_review", "cancelled"]);
const POLL_MS = 2000;

function statusChip(status: string) {
  if (status === "resolved") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700">
        <CheckCircle2 className="h-3 w-3" /> Resolved
      </span>
    );
  }
  if (status === "manual_review") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-red-50 px-2 py-0.5 text-[11px] font-semibold text-red-700">
        <AlertTriangle className="h-3 w-3" /> Needs review
      </span>
    );
  }
  if (status === "pending_hitl") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-[11px] font-semibold text-amber-700">
        <AlertTriangle className="h-3 w-3" /> Waiting on you
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-sky-50 px-2 py-0.5 text-[11px] font-semibold text-sky-700">
      <Loader2 className="h-3 w-3 animate-spin" /> Running
    </span>
  );
}

export default function FlowRunTimeline({ runId, onClose }: { runId: string; onClose: () => void }) {
  const [run, setRun] = useState<FlowRunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    let cancelled = false;

    const poll = async () => {
      try {
        const detail = await getFlowRun(runId);
        if (cancelled) return;
        setRun(detail);
        if (TERMINAL_STATUSES.has(detail.status) && timerRef.current) {
          clearInterval(timerRef.current);
          timerRef.current = null;
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load run status.");
      }
    };

    void poll();
    timerRef.current = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, [runId]);

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        {run && statusChip(run.status)}
        <span className="text-xs text-slate-400">step: {run?.current_step || "—"}</span>
        <button
          type="button"
          onClick={onClose}
          className="ml-auto inline-flex items-center gap-1 rounded-md border border-slate-200 px-2 py-1 text-[11px] font-semibold text-slate-600 hover:bg-slate-50"
        >
          <X className="h-3 w-3" /> Back to editor
        </button>
      </div>

      {error && <div className="rounded-lg border border-red-100 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</div>}

      {run && Object.keys(run.context).length > 0 && (
        <div>
          <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Context</p>
          <pre className="overflow-x-auto rounded-lg bg-slate-900 p-2.5 text-[11px] leading-relaxed text-slate-200">
            {JSON.stringify(run.context, null, 2)}
          </pre>
        </div>
      )}

      <div>
        <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-400">Timeline</p>
        <div className="space-y-1.5">
          {(run?.timeline ?? []).map((e) => (
            <div key={e.id} className="rounded-lg border border-slate-100 px-2.5 py-1.5 text-xs">
              <div className="flex items-center gap-2">
                <span className="font-semibold text-slate-700">{e.event_type}</span>
                {e.step_id && <span className="font-mono text-[10px] text-slate-400">{e.step_id}</span>}
              </div>
              {Object.keys(e.payload).length > 0 && (
                <pre className="mt-1 overflow-x-auto text-[10px] text-slate-500">
                  {JSON.stringify(e.payload)}
                </pre>
              )}
            </div>
          ))}
          {!run && <p className="py-6 text-center text-xs text-slate-400">Loading…</p>}
        </div>
      </div>
    </div>
  );
}
