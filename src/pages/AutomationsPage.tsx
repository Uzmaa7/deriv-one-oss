import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import {
  Play, Trash2, CheckCircle2, XCircle,
  Loader2, ChevronDown, ChevronRight, Circle, Search,
  Zap, Clock, Hash,
} from "lucide-react";
import SchedulePicker, { describeCron } from "../components/SchedulePicker";
import { apiFetch } from "@/lib/api";
import { useSkillAccess, useSkillList } from "@/lib/SkillAccessContext";
import { parseUTC, timeAgo } from "@/lib/time";
import {
  type Run, fmtDuration, renderRunMarkdown, extractFinalOutput, humanizeRunError,
} from "@/src/lib/automationRuns";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AutomationSummary {
  id: string;
  name: string;
  schedule: string;
  is_active: boolean;
  run_count: number;
  last_run: { id: string; started_at: string; status: string; duration_secs: number | null; error?: string | null } | null;
}

interface AutomationDetail extends AutomationSummary {
  instructions: string;
  skill_id: string | null;
  slack_channel: string | null;
  created_at: string;
  updated_at: string;
}

function fmtTime(iso: string) {
  return new Date(iso).toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

// ── Status chip ───────────────────────────────────────────────────────────────

function statusLabel(status: string): string {
  if (status === "success") return "Succeeded";
  if (status === "error") return "Failed";
  if (status === "running") return "Running";
  return status;
}

function StatusChip({ status }: { status: string }) {
  if (status === "success") return (
    <span className="flex items-center gap-1 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-full px-2 py-0.5 font-medium">
      <CheckCircle2 className="w-3 h-3" /> Succeeded
    </span>
  );
  if (status === "error") return (
    <span className="flex items-center gap-1 text-xs text-red-700 bg-red-50 border border-red-200 rounded-full px-2 py-0.5 font-medium">
      <XCircle className="w-3 h-3" /> Failed
    </span>
  );
  return (
    <span className="flex items-center gap-1 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5 font-medium">
      <Loader2 className="w-3 h-3 animate-spin" /> running
    </span>
  );
}

// ── Automation form ───────────────────────────────────────────────────────────

interface FormState {
  name: string; instructions: string; skill_id: string; schedule: string; is_active: boolean; slack_channel: string;
}

const emptyForm = (): FormState => ({
  name: "", instructions: "", skill_id: "", schedule: "0 9 * * *", is_active: true, slack_channel: "",
});

function AutomationForm({
  initial, onSave, onCancel, saving, slackUserTokenIntegration = false,
}: {
  initial?: FormState;
  onSave: (f: FormState) => void;
  onCancel: () => void;
  saving: boolean;
  slackUserTokenIntegration?: boolean;
}) {
  const [form, setForm] = useState<FormState>(initial ?? emptyForm());
  const set = (k: keyof FormState, v: string | boolean) => setForm(p => ({ ...p, [k]: v }));
  const { accessibleSlugs, loading: accessLoading } = useSkillAccess();
  const allSkills = useSkillList();
  const accessibleSkills = accessLoading ? allSkills : allSkills.filter(s => accessibleSlugs.has(s.slug));

  return (
    <form onSubmit={e => {
      e.preventDefault();
      const out = slackUserTokenIntegration ? form : { ...form, slack_channel: "" };
      onSave(out);
    }} className="flex flex-col gap-5 p-6 overflow-y-auto h-full">
      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Name</label>
        <input required value={form.name} onChange={e => set("name", e.target.value)}
          placeholder="Daily PSP balance report"
          className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400" />
      </div>

      <div className="shrink-0">
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Instructions</label>
        <textarea
          required
          value={form.instructions}
          onChange={e => set("instructions", e.target.value)}
          placeholder="Describe what Claude should do each time this automation runs..."
          rows={6}
          className="w-full min-h-[9rem] text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 resize-y leading-relaxed"
        />
      </div>

      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
          Skill context <span className="text-slate-400 font-normal">(optional)</span>
        </label>
        <div className="relative">
          <select value={form.skill_id} onChange={e => set("skill_id", e.target.value)}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg appearance-none focus:outline-none focus:border-slate-400 bg-white">
            <option value="">No skill — general assistant</option>
            {accessibleSkills.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
          <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
        </div>
        {form.skill_id && (
          <p className="text-[10px] text-slate-500 mt-1 flex items-center gap-1">
            <Zap className="w-2.5 h-2.5 text-amber-500" />
            {allSkills.find(s => s.id === form.skill_id)?.description?.slice(0, 90)}…
          </p>
        )}
      </div>

      <div>
        <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Schedule</label>
        <SchedulePicker value={form.schedule} onChange={v => set("schedule", v)} />
      </div>

      {slackUserTokenIntegration ? (
        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
            Post to Slack <span className="text-slate-400 font-normal">(optional)</span>
          </label>
          <div className="relative">
            <Hash className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
            <input
              type="text"
              value={form.slack_channel}
              onChange={e => set("slack_channel", e.target.value)}
              placeholder="general or C012AB3CD"
              className="w-full text-sm pl-8 pr-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
            />
          </div>
          <p className="text-[10px] text-slate-400 mt-1">
            Posted after each run using the hub&apos;s Slack token (restricted to designated accounts).
          </p>
        </div>
      ) : (
        <p className="text-[10px] text-slate-400 border border-slate-100 rounded-lg px-3 py-2 bg-slate-50">
          Slack channel posting is not enabled for your account — only JWT subjects listed server-side may use it.
        </p>
      )}

      <div className="flex items-center justify-between py-2 border-t border-slate-100">
        <span className="text-sm text-slate-700 font-medium">Active when saved</span>
        <button type="button" onClick={() => set("is_active", !form.is_active)}
          className={`relative inline-flex h-5 w-9 rounded-full transition-colors ${form.is_active ? "bg-emerald-500" : "bg-slate-300"}`}>
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white shadow transition-transform ${form.is_active ? "translate-x-4" : ""}`} />
        </button>
      </div>

      <div className="flex gap-2 pt-2 border-t border-slate-100 shrink-0">
        <button type="button" onClick={onCancel}
          className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors">
          Cancel
        </button>
        <button type="submit" disabled={saving}
          className="flex-1 px-4 py-2 text-sm bg-[#FF444F] text-white rounded-lg hover:bg-[#e63d48] transition-colors disabled:opacity-50 flex items-center justify-center gap-2">
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Save automation
        </button>
      </div>
    </form>
  );
}

// ── Run history ───────────────────────────────────────────────────────────────

function RunHistory({ automationId, refreshKey }: { automationId: string; refreshKey: number }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [didAutoExpand, setDidAutoExpand] = useState(false);

  useEffect(() => {
    setLoading(true);
    apiFetch(`/api/automations/${automationId}/runs`)
      .then(r => r.json())
      .then(data => {
        setRuns(data);
        setLoading(false);
        if (!didAutoExpand) {
          const failed = (data as Run[]).find(r => r.status === "error");
          if (failed) setExpanded(failed.id);
          setDidAutoExpand(true);
        }
      })
      .catch(() => setLoading(false));
  }, [automationId, refreshKey]);

  if (loading) return <div className="flex items-center justify-center py-12"><Loader2 className="w-5 h-5 animate-spin text-slate-400" /></div>;

  if (runs.length === 0) return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <Clock className="w-8 h-8 text-slate-300 mb-3" />
      <p className="text-sm text-slate-500">No runs yet.</p>
      <p className="text-xs text-slate-400 mt-1">Click "Run Now" to trigger the first execution.</p>
    </div>
  );

  return (
    <div className="overflow-y-auto h-full">
      <table className="w-full text-xs">
        <thead className="sticky top-0 bg-white">
          <tr className="border-b border-slate-100">
            <th className="text-left font-semibold text-slate-500 px-4 py-2.5 uppercase tracking-wider">Time</th>
            <th className="text-left font-semibold text-slate-500 px-4 py-2.5 uppercase tracking-wider">Duration</th>
            <th className="text-left font-semibold text-slate-500 px-4 py-2.5 uppercase tracking-wider">Status</th>
            <th className="px-4 py-2.5 w-8" />
          </tr>
        </thead>
        <tbody>
          {runs.map(run => (
            <>
              <tr key={run.id} className="border-b border-slate-50 hover:bg-slate-50 cursor-pointer"
                onClick={() => setExpanded(expanded === run.id ? null : run.id)}>
                <td className="px-4 py-3 font-mono text-slate-700">{fmtTime(run.started_at)}</td>
                <td className="px-4 py-3 text-slate-600">{fmtDuration(run.duration_secs)}</td>
                <td className="px-4 py-3"><StatusChip status={run.status} /></td>
                <td className="px-4 py-3 text-slate-400">
                  {expanded === run.id ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
                </td>
              </tr>
              {expanded === run.id && (
                <tr key={`${run.id}-exp`}>
                  <td colSpan={4} className="px-4 py-3 bg-slate-50">
                    {run.status === "error"
                      ? <div className="text-xs text-red-800 bg-red-50 border border-red-200 rounded-lg p-3 whitespace-pre-wrap leading-relaxed">{humanizeRunError(run.error)}</div>
                      : run.output
                        ? <div
                            className="prose-sm text-sm text-slate-700 bg-white border border-slate-200 rounded-xl p-4 overflow-y-auto max-h-[32rem]"
                            dangerouslySetInnerHTML={{ __html: renderRunMarkdown(extractFinalOutput(run.output)) }}
                          />
                        : <p className="text-xs text-slate-500 italic">No output captured.</p>}
                  </td>
                </tr>
              )}
            </>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Detail panel ──────────────────────────────────────────────────────────────

function DetailPanel({
  automation, onUpdate, onDelete, slackUserTokenIntegration,
}: {
  automation: AutomationDetail;
  onUpdate: (a: AutomationDetail) => void;
  onDelete: (id: string) => void;
  slackUserTokenIntegration: boolean;
}) {
  const allSkills = useSkillList();
  const [tab, setTab] = useState<"config" | "runs">("config");
  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [runningNow, setRunningNow] = useState(false);
  const [runsRefreshKey, setRunsRefreshKey] = useState(0);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const handleSave = async (form: FormState) => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        instructions: form.instructions,
        skill_id: form.skill_id || null,
        schedule: form.schedule,
        is_active: form.is_active,
      };
      if (slackUserTokenIntegration) {
        const s = form.slack_channel.trim();
        payload.slack_channel = s === "" ? null : s;
      }
      const res = await apiFetch(`/api/automations/${automation.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      onUpdate(await res.json());
      setEditing(false);
    } finally { setSaving(false); }
  };

  const handleToggle = async () => {
    const res = await apiFetch(`/api/automations/${automation.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ is_active: !automation.is_active }),
    });
    onUpdate(await res.json());
  };

  const handleDelete = async () => {
    if (!confirm(`Delete "${automation.name}"? This removes all run history.`)) return;
    await apiFetch(`/api/automations/${automation.id}`, { method: "DELETE" });
    onDelete(automation.id);
  };

  const handleRunNow = async () => {
    if (runningNow) return;
    setRunningNow(true);
    setTab("runs");
    await apiFetch(`/api/automations/${automation.id}/run`, { method: "POST" });
    setRunsRefreshKey(k => k + 1);

    pollRef.current = setInterval(async () => {
      setRunsRefreshKey(k => k + 1);
      try {
        const res = await apiFetch(`/api/automations/${automation.id}/runs`);
        const runs: Run[] = await res.json();
        if (runs[0] && runs[0].status !== "running") {
          clearInterval(pollRef.current!);
          setRunningNow(false);
        }
      } catch { clearInterval(pollRef.current!); setRunningNow(false); }
    }, 2000);
  };

  if (editing) {
    return (
      <AutomationForm
        slackUserTokenIntegration={slackUserTokenIntegration}
        initial={{ name: automation.name, instructions: automation.instructions, skill_id: automation.skill_id ?? "", schedule: automation.schedule, is_active: automation.is_active, slack_channel: automation.slack_channel ?? "" }}
        onSave={handleSave}
        onCancel={() => setEditing(false)}
        saving={saving}
      />
    );
  }

  const skill = allSkills.find(s => s.id === automation.skill_id);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="px-6 py-4 border-b border-slate-200 bg-white shrink-0">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h2 className="text-base font-bold text-slate-900 truncate">{automation.name}</h2>
            <div className="flex items-center gap-2 mt-1 flex-wrap">
              <span className={`flex items-center gap-1 text-xs font-medium rounded-full px-2 py-0.5 ${
                automation.is_active ? "bg-emerald-50 text-emerald-700 border border-emerald-200" : "bg-slate-100 text-slate-500 border border-slate-200"
              }`}>
                <Circle className="w-1.5 h-1.5 fill-current" />
                {automation.is_active ? "Active" : "Paused"}
              </span>
              <span className="text-xs text-slate-500 font-mono">{describeCron(automation.schedule)}</span>
              {skill && (
                <span className="flex items-center gap-1 text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-full px-2 py-0.5">
                  <Zap className="w-2.5 h-2.5" /> {skill.name}
                </span>
              )}
            </div>
          </div>
          <div className="flex items-center gap-1.5 shrink-0">
            <button onClick={handleRunNow} disabled={runningNow}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-[#FF444F] text-white rounded-lg hover:bg-[#e63d48] transition-colors disabled:opacity-50">
              {runningNow ? <><Loader2 className="w-3 h-3 animate-spin" /> Running…</> : <><Play className="w-3 h-3" /> Run Now</>}
            </button>
            <button onClick={handleToggle} className="px-3 py-1.5 text-xs border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">
              {automation.is_active ? "Pause" : "Resume"}
            </button>
            <button onClick={() => setEditing(true)} className="px-3 py-1.5 text-xs border border-slate-200 rounded-lg text-slate-600 hover:bg-slate-50 transition-colors">
              Edit
            </button>
            <button onClick={handleDelete} title="Delete" className="p-1.5 text-slate-400 hover:text-red-500 rounded-lg hover:bg-red-50 transition-colors">
              <Trash2 className="w-3.5 h-3.5" />
            </button>
          </div>
        </div>
        <div className="flex items-center gap-4 mt-4">
          {(["config", "runs"] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className={`text-xs font-semibold pb-2 border-b-2 transition-colors ${
                tab === t ? "text-slate-900 border-slate-900" : "text-slate-400 border-transparent hover:text-slate-700"
              }`}>
              {t === "runs" ? `Run History (${automation.run_count})` : "Config"}
            </button>
          ))}
          {runningNow && (
            <span className="ml-auto flex items-center gap-1 text-xs text-amber-600">
              <Loader2 className="w-3 h-3 animate-spin" /> Waiting for result…
            </span>
          )}
        </div>
      </div>

      <div className="flex-1 overflow-hidden">
        {tab === "config" ? (
          <div className="p-6 overflow-y-auto h-full space-y-5">
            <div>
              <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-2">Instructions</p>
              <pre className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-xl p-4 font-mono whitespace-pre-wrap leading-relaxed">
                {automation.instructions}
              </pre>
            </div>
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Schedule</p>
                <p className="text-sm text-slate-700">{describeCron(automation.schedule)}</p>
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Skill</p>
                {skill ? (
                  <p className="text-sm text-slate-700 flex items-center gap-1"><Zap className="w-3 h-3 text-amber-500" /> {skill.name}</p>
                ) : <p className="text-sm text-slate-500 italic">None (general assistant)</p>}
              </div>
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Slack channel</p>
                {automation.slack_channel ? (
                  <p className="text-sm text-slate-700 flex items-center gap-1">
                    <Hash className="w-3 h-3 text-purple-500" /> {automation.slack_channel}
                  </p>
                ) : <p className="text-sm text-slate-400 italic">Not configured</p>}
                {automation.slack_channel && !slackUserTokenIntegration && (
                  <p className="text-[10px] text-slate-400 mt-1">
                    Notifications are skipped for your account unless this JWT subject is authorized on the server.
                  </p>
                )}
              </div>
            </div>
            {automation.last_run && (
              <div>
                <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-1">Last run</p>
                <p className="text-sm text-slate-600">{fmtTime(automation.last_run.started_at)}</p>
                <div className="mt-1"><StatusChip status={automation.last_run.status} /></div>
                {automation.last_run.status === "error" && (
                  <p className="text-xs text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2 mt-2 leading-relaxed">
                    {humanizeRunError(automation.last_run.error)}
                  </p>
                )}
              </div>
            )}
          </div>
        ) : (
          <RunHistory automationId={automation.id} refreshKey={runsRefreshKey} />
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AutomationsPage({ onRequestCreate }: { onRequestCreate?: (trigger: () => void) => void } = {}) {
  // Deep-link support: /automations?id=xyz pre-selects a specific automation
  // (used by the workspace chat's "Open Automation" proposal-approval link).
  const [searchParams] = useSearchParams();
  const [automations, setAutomations] = useState<AutomationSummary[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(() => searchParams.get("id"));
  const [selectedDetail, setSelectedDetail] = useState<AutomationDetail | null>(null);
  const [creating, setCreating] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [slackUserTokenIntegration, setSlackUserTokenIntegration] = useState(false);

  useEffect(() => {
    onRequestCreate?.(() => { setCreating(true); setSelectedId(null); });
  }, [onRequestCreate]);

  useEffect(() => {
    apiFetch("/api/me")
      .then(r => r.json())
      .then((me: { slack_user_token_integration?: boolean }) => {
        setSlackUserTokenIntegration(Boolean(me.slack_user_token_integration));
      })
      .catch(() => setSlackUserTokenIntegration(false));
  }, []);

  const loadAutomations = useCallback(async () => {
    try {
      const res = await apiFetch("/api/automations");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setLoadError("Couldn't load automations. Please refresh the page.");
        return;
      }
      const data: AutomationSummary[] = await res.json();
      setLoadError(null);
      setAutomations(data);
      if (data.length > 0 && !selectedId) setSelectedId(data[0].id);
    } catch {
      setLoadError("Couldn't connect to the server. Please refresh the page.");
    } finally { setLoading(false); }
  }, [selectedId]);

  useEffect(() => { loadAutomations(); }, []);

  useEffect(() => {
    if (!selectedId) { setSelectedDetail(null); return; }
    apiFetch(`/api/automations/${selectedId}`)
      .then(r => r.json())
      .then(setSelectedDetail)
      .catch(() => setSelectedDetail(null));
  }, [selectedId]);

  const handleCreate = async (form: FormState) => {
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        name: form.name,
        instructions: form.instructions,
        skill_id: form.skill_id || null,
        schedule: form.schedule,
        is_active: form.is_active,
      };
      if (slackUserTokenIntegration) {
        const s = form.slack_channel.trim();
        payload.slack_channel = s === "" ? null : s;
      }
      const res = await apiFetch("/api/automations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const created: AutomationDetail = await res.json();
      setAutomations(prev => [created, ...prev]);
      setSelectedId(created.id);
      setSelectedDetail(created);
      setCreating(false);
    } finally { setSaving(false); }
  };

  const handleUpdate = (updated: AutomationDetail) => {
    setSelectedDetail(updated);
    setAutomations(prev => prev.map(a => a.id === updated.id
      ? { ...a, name: updated.name, schedule: updated.schedule, is_active: updated.is_active }
      : a));
  };

  const handleDelete = (id: string) => {
    setAutomations(prev => prev.filter(a => a.id !== id));
    setSelectedId(null);
    setSelectedDetail(null);
  };

  const filtered = automations.filter(a =>
    a.name.toLowerCase().includes(search.toLowerCase()) ||
    describeCron(a.schedule).toLowerCase().includes(search.toLowerCase())
  );

  return (
    <div className="flex h-full overflow-hidden">
      {/* Left panel */}
      <div className="w-64 border-r border-slate-200 bg-white flex flex-col shrink-0">
        <div className="px-4 pt-4 pb-3 border-b border-slate-100">
          <div className="mb-3">
            <h2 className="text-xs font-bold text-slate-500 uppercase tracking-wider">Automations</h2>
          </div>
          <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5">
            <Search className="w-3 h-3 text-slate-400 shrink-0" />
            <input type="text" value={search} onChange={e => setSearch(e.target.value)} placeholder="Search..."
              className="flex-1 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none" />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto py-2">
          {loading ? (
            <div className="flex items-center justify-center py-8"><Loader2 className="w-4 h-4 animate-spin text-slate-400" /></div>
          ) : loadError ? (
            <div className="px-4 py-6 text-center">
              <p className="text-xs text-red-500">{loadError}</p>
              <button onClick={() => { setLoading(true); setLoadError(null); loadAutomations(); }} className="mt-2 text-xs text-slate-900 underline underline-offset-2">Retry</button>
            </div>
          ) : filtered.length === 0 ? (
            <div className="px-4 py-6 text-center">
              <p className="text-xs text-slate-500">{search ? "No matching automations." : "No automations yet."}</p>
              {!search && <button onClick={() => setCreating(true)} className="mt-2 text-xs text-slate-900 underline underline-offset-2">Create your first</button>}
            </div>
          ) : filtered.map(a => (
            <button key={a.id} onClick={() => { setSelectedId(a.id); setCreating(false); }}
              className={`w-full text-left px-4 py-3 transition-colors border-b border-slate-50 hover:bg-slate-50 ${
                selectedId === a.id && !creating ? "bg-slate-50 border-l-2 border-l-[#FF444F]" : ""
              }`}>
              <div className="flex items-center gap-2 mb-0.5">
                <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${a.is_active ? "bg-emerald-500" : "bg-slate-300"}`} />
                <span className="text-xs font-semibold text-slate-900 truncate">{a.name}</span>
              </div>
              <p className="text-[10px] text-slate-500 ml-3.5 truncate">{describeCron(a.schedule)}</p>
              {a.last_run && (
                <p className="text-[10px] text-slate-400 ml-3.5 mt-0.5">
                  Last: {fmtTime(a.last_run.started_at)} · <span className={a.last_run.status === "error" ? "text-red-500" : "text-emerald-600"}>{statusLabel(a.last_run.status)}</span>
                </p>
              )}
            </button>
          ))}
        </div>
      </div>

      {/* Right panel */}
      <div className="flex-1 overflow-hidden bg-white">
        {creating ? (
          <AutomationForm slackUserTokenIntegration={slackUserTokenIntegration} onSave={handleCreate} onCancel={() => setCreating(false)} saving={saving} />
        ) : selectedDetail ? (
          <div key={selectedDetail.id} className="h-full overflow-hidden"><DetailPanel slackUserTokenIntegration={slackUserTokenIntegration} automation={selectedDetail} onUpdate={handleUpdate} onDelete={handleDelete} /></div>
        ) : (
          <div className="flex flex-col items-center justify-center h-full text-center px-8">
            <div className="w-14 h-14 rounded-2xl bg-slate-100 border border-slate-200 flex items-center justify-center mb-4">
              <Clock className="w-6 h-6 text-slate-400" />
            </div>
            <h3 className="text-base font-bold text-slate-900 mb-2">No automation selected</h3>
            <p className="text-sm text-slate-500 max-w-xs leading-relaxed">
              Select an automation from the list or use <strong className="text-slate-700">+ New Automation</strong> above to create one.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
