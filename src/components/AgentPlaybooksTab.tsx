import { useCallback, useEffect, useState } from "react";
import { CheckCircle2, Loader2, Play, RefreshCw } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface WorkflowRow {
  id: string;
  slug: string;
  name: string;
  status: string;
  version: number;
}

interface RunRow {
  id: string;
  status: string;
  current_step: string;
  started_at: string | null;
  updated_at: string | null;
}

interface PendingGate {
  run_id: string;
  agent_id: string;
  current_step: string;
  gate: {
    id: string;
    title?: string;
    payload?: string;
  };
}

interface AgentPlaybooksTabProps {
  agentId: string;
  readonly?: boolean;
}

export function AgentPlaybooksTab({ agentId, readonly = false }: AgentPlaybooksTabProps) {
  const [workflows, setWorkflows] = useState<WorkflowRow[]>([]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [gates, setGates] = useState<PendingGate[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [intakeChannel, setIntakeChannel] = useState("");
  const [delegateUserId, setDelegateUserId] = useState("");
  const [editGateMessage, setEditGateMessage] = useState<Record<string, string>>({});

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [wfRes, runRes, gateRes] = await Promise.all([
        apiFetch(`/api/agents/${agentId}/workflows`),
        apiFetch(`/api/agents/${agentId}/workflow-runs`),
        apiFetch(`/api/workflow-runs/pending-gates?agent_id=${encodeURIComponent(agentId)}`),
      ]);
      if (wfRes.ok) setWorkflows(await wfRes.json());
      if (runRes.ok) setRuns(await runRes.json());
      if (gateRes.ok) setGates(await gateRes.json());
    } catch {
      setError("Could not load playbooks.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    load();
  }, [load]);

  const seedAndPublish = async () => {
    setBusy(true);
    setError(null);
    try {
      const seedRes = await apiFetch(`/api/agents/${agentId}/workflows/seed-vertical-slice`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          intake_channel: intakeChannel.trim(),
          delegate_user_id: delegateUserId.trim(),
        }),
      });
      if (!seedRes.ok) throw new Error("Seed failed");
      const defn = await seedRes.json();
      const pubRes = await apiFetch(
        `/api/agents/${agentId}/workflows/${defn.id}/publish`,
        { method: "POST" }
      );
      if (!pubRes.ok) throw new Error("Publish failed");
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Setup failed");
    } finally {
      setBusy(false);
    }
  };

  const decideGate = async (runId: string, gateId: string, approved: boolean) => {
    setBusy(true);
    setError(null);
    const message = editGateMessage[gateId] ?? "";
    try {
      const res = await apiFetch(`/api/workflow-runs/${runId}/gates/${gateId}/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ approved, message }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Approval action failed.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-500 py-8">
        <Loader2 className="w-4 h-4 animate-spin" /> Loading playbooks…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">Agent playbooks</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          Stateful workflows: mention the bot in Slack to start a published playbook. Delegate
          steps pause until a thread reply; approvals appear below.
        </p>
      </div>

      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg px-3 py-2">
          {error}
        </p>
      )}

      {!readonly && (
        <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-700">Vertical slice template</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div>
              <label className="block text-[11px] font-semibold text-slate-500 mb-1">
                Intake channel ID
              </label>
              <input
                value={intakeChannel}
                onChange={(e) => setIntakeChannel(e.target.value)}
                placeholder="C0ABL6CBZPY"
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            </div>
            <div>
              <label className="block text-[11px] font-semibold text-slate-500 mb-1">
                Investigator Slack user ID
              </label>
              <input
                value={delegateUserId}
                onChange={(e) => setDelegateUserId(e.target.value)}
                placeholder="U0AESTZFPFB"
                className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm"
              />
            </div>
          </div>
          <button
            type="button"
            disabled={busy}
            onClick={seedAndPublish}
            className="inline-flex items-center gap-1.5 text-xs px-3 py-2 bg-slate-900 text-white rounded-lg font-semibold disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
            Seed &amp; publish template
          </button>
        </div>
      )}

      <div className="flex items-center justify-between">
        <h4 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Definitions</h4>
        <button type="button" onClick={load} className="text-slate-400 hover:text-slate-600">
          <RefreshCw className="w-3.5 h-3.5" />
        </button>
      </div>
      {workflows.length === 0 ? (
        <p className="text-sm text-slate-500">No workflows yet.</p>
      ) : (
        <ul className="border border-slate-200 rounded-xl divide-y divide-slate-100 bg-white">
          {workflows.map((w) => (
            <li key={w.id} className="px-4 py-3 flex justify-between text-sm">
              <span className="font-medium text-slate-900">{w.name}</span>
              <span
                className={`text-[11px] font-semibold px-2 py-0.5 rounded-full ${
                  w.status === "published"
                    ? "bg-emerald-50 text-emerald-700"
                    : "bg-slate-100 text-slate-600"
                }`}
              >
                {w.status}
              </span>
            </li>
          ))}
        </ul>
      )}

      <h4 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Runs</h4>
      {runs.length === 0 ? (
        <p className="text-sm text-slate-500">No runs yet.</p>
      ) : (
        <div className="border border-slate-200 rounded-xl overflow-hidden bg-white">
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-[11px] text-slate-500 uppercase">
              <tr>
                <th className="text-left px-3 py-2">Run</th>
                <th className="text-left px-3 py-2">Step</th>
                <th className="text-left px-3 py-2">Status</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((r) => (
                <tr key={r.id} className="border-t border-slate-100">
                  <td className="px-3 py-2 font-mono text-xs text-slate-600">{r.id.slice(0, 8)}…</td>
                  <td className="px-3 py-2 text-slate-800">{r.current_step}</td>
                  <td className="px-3 py-2 text-slate-600">{r.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <h4 className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Approvals</h4>
      {gates.length === 0 ? (
        <p className="text-sm text-slate-500">No pending approvals.</p>
      ) : (
        <ul className="space-y-3">
          {gates.map((g) => (
            <li key={g.gate.id} className="border border-amber-200 bg-amber-50/50 rounded-xl p-4">
              <p className="text-sm font-semibold text-slate-900">{g.gate.title ?? "Approval"}</p>
              <p className="text-xs text-slate-500 mt-1 font-mono">run {g.run_id.slice(0, 8)}…</p>
              <textarea
                className="mt-2 w-full rounded-lg border border-slate-200 px-3 py-2 text-sm min-h-[72px]"
                defaultValue={g.gate.payload ?? ""}
                onChange={(e) =>
                  setEditGateMessage((prev) => ({ ...prev, [g.gate.id]: e.target.value }))
                }
              />
              <div className="flex gap-2 mt-2">
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => decideGate(g.run_id, g.gate.id, true)}
                  className="inline-flex items-center gap-1 text-xs px-3 py-1.5 bg-emerald-600 text-white rounded-lg font-semibold"
                >
                  <CheckCircle2 className="w-3.5 h-3.5" /> Approve
                </button>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => decideGate(g.run_id, g.gate.id, false)}
                  className="text-xs px-3 py-1.5 border border-slate-200 rounded-lg font-semibold text-slate-700"
                >
                  Reject
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
