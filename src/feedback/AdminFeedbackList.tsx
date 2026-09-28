import { type FC, useCallback, useEffect, useState } from "react";
import { Clock, Loader2, AlertCircle, Check } from "lucide-react";
import { listFeedback, listFeedbackAssignees, patchFeedback, type FeedbackAssignee, type FeedbackRow } from "./api";
import { parseUTC, timeAgo } from "@/lib/time";

type StatusFilter = "all" | "open" | "triaged" | "in_progress" | "done" | "wont_do";

const STATUS_LABELS: Record<string, string> = {
  open: "Open",
  triaged: "Triaged",
  in_progress: "In Progress",
  done: "Done",
  wont_do: "Won't Do",
};

const CATEGORY_COLORS: Record<string, string> = {
  bug: "bg-red-100 text-red-700",
  feature: "bg-blue-100 text-blue-700",
  ux: "bg-purple-100 text-purple-700",
  other: "bg-slate-100 text-slate-600",
};

const PRIORITY_LABELS: Record<string, string> = {
  low: "Low",
  medium: "Medium",
  high: "High",
  urgent: "Urgent",
};

const PRIORITY_COLORS: Record<string, string> = {
  low: "bg-slate-100 text-slate-600 border-slate-200",
  medium: "bg-blue-50 text-blue-700 border-blue-200",
  high: "bg-amber-50 text-amber-700 border-amber-200",
  urgent: "bg-red-50 text-red-700 border-red-200",
};

const FILTERS: { id: StatusFilter; label: string }[] = [
  { id: "all", label: "All" },
  { id: "open", label: "Open" },
  { id: "triaged", label: "Triaged" },
  { id: "in_progress", label: "In Progress" },
  { id: "done", label: "Done" },
  { id: "wont_do", label: "Won't Do" },
];

interface FeedbackCardProps {
  row: FeedbackRow;
  assignees: FeedbackAssignee[];
  assigneeLabel: (email: string | null) => string;
  onUpdate: (updated: FeedbackRow) => void;
}

const FeedbackCard: FC<FeedbackCardProps> = ({ row, assignees, assigneeLabel, onUpdate }) => {
  const [status, setStatus] = useState(row.status);
  const [assignee, setAssignee] = useState(row.assignee ?? "");
  const [priority, setPriority] = useState(row.priority ?? "");
  const [note, setNote] = useState(row.resolution_note ?? "");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const updated = await patchFeedback(row.id, {
        status,
        assignee: assignee.trim() || null,
        priority: priority || null,
        resolution_note: note,
      });
      onUpdate(updated);
      setSaved(true);
      setTimeout(() => setSaved(false), 1500);
    } finally {
      setSaving(false);
    }
  };

  const isDirty =
    status !== row.status
    || assignee !== (row.assignee ?? "")
    || priority !== (row.priority ?? "")
    || note !== (row.resolution_note ?? "");

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
      {/* Top meta bar */}
      <div className="flex items-center justify-between px-5 py-2.5 bg-slate-50 border-b border-slate-100">
        <div className="flex items-center gap-2 flex-wrap">
          <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide ${CATEGORY_COLORS[row.category] ?? "bg-slate-100 text-slate-600"}`}>
            {row.category}
          </span>
          {row.priority && (
            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full uppercase tracking-wide border ${PRIORITY_COLORS[row.priority] ?? "bg-slate-100 text-slate-600 border-slate-200"}`}>
              {PRIORITY_LABELS[row.priority] ?? row.priority}
            </span>
          )}
          {row.assignee && (
            <span className="text-[10px] font-medium px-2 py-0.5 rounded-full bg-violet-50 text-violet-700 border border-violet-200">
              {assigneeLabel(row.assignee)}
            </span>
          )}
          <span className="text-[10px] font-mono text-slate-400">{row.route}</span>
        </div>
        <div className="flex items-center gap-1 text-[10px] text-slate-400">
          <Clock className="w-3 h-3" />
          {timeAgo(row.created_at)}
          <span className="ml-1 text-slate-300">·</span>
          <span className="text-slate-500">{row.created_by_email}</span>
        </div>
      </div>

      <div className="px-5 py-4">
        {/* Element context */}
        {(row.element_context?.label || row.element_context?.selector) && (
          <div className="mb-3 px-3 py-2 bg-slate-50 border border-slate-100 rounded-lg">
            {row.element_context.label && (
              <p className="text-xs font-medium text-slate-700 truncate">{row.element_context.label}</p>
            )}
            {row.element_context.selector && (
              <p className="text-[10px] font-mono text-slate-400 truncate mt-0.5">{row.element_context.selector}</p>
            )}
          </div>
        )}

        {/* Message */}
        <p className="text-sm text-slate-800 leading-relaxed whitespace-pre-wrap">{row.user_message}</p>

        {/* Triage controls */}
        <div className="mt-4 pt-4 border-t border-slate-100 flex flex-col gap-3">
          <div className="flex items-center gap-3">
            <label className="text-xs font-semibold text-slate-600 w-14 shrink-0">Status</label>
            <select
              value={status}
              onChange={e => setStatus(e.target.value as FeedbackRow["status"])}
              className="text-xs border border-slate-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-700"
            >
              {Object.entries(STATUS_LABELS).map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-3">
            <label className="text-xs font-semibold text-slate-600 w-14 shrink-0">Priority</label>
            <select
              value={priority}
              onChange={e => setPriority(e.target.value)}
              className="text-xs border border-slate-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-700"
            >
              <option value="">Unset</option>
              {Object.entries(PRIORITY_LABELS).map(([v, l]) => (
                <option key={v} value={v}>{l}</option>
              ))}
            </select>
          </div>

          <div className="flex items-center gap-3">
            <label className="text-xs font-semibold text-slate-600 w-14 shrink-0">Assignee</label>
            <select
              value={assignee}
              onChange={e => setAssignee(e.target.value)}
              disabled={assignees.length === 0}
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2.5 py-1.5 focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-700 disabled:bg-slate-50 disabled:text-slate-400"
            >
              <option value="">Unassigned</option>
              {assignees.map(a => (
                <option key={a.email} value={a.email}>{a.name} ({a.email})</option>
              ))}
              {assignee && !assignees.some(a => a.email === assignee) && (
                <option value={assignee}>{assigneeLabel(assignee)} (current)</option>
              )}
            </select>
          </div>

          <div className="flex items-start gap-3">
            <label className="text-xs font-semibold text-slate-600 w-14 shrink-0 mt-2">Note</label>
            <textarea
              value={note}
              onChange={e => setNote(e.target.value)}
              rows={2}
              placeholder="Resolution note (optional)"
              className="flex-1 text-xs border border-slate-200 rounded-lg px-2.5 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-700 placeholder-slate-400"
            />
          </div>

          {isDirty && (
            <div className="flex justify-end">
              <button
                onClick={save}
                disabled={saving}
                className="flex items-center gap-1.5 text-xs font-semibold bg-slate-900 text-white px-3 py-1.5 rounded-lg hover:bg-slate-800 disabled:opacity-50 transition-colors"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : saved ? <Check className="w-3 h-3" /> : null}
                {saved ? "Saved" : "Save"}
              </button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default function AdminFeedbackList() {
  const [rows, setRows] = useState<FeedbackRow[]>([]);
  const [assignees, setAssignees] = useState<FeedbackAssignee[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<StatusFilter>("open");

  const assigneeLabel = useCallback((email: string | null) => {
    if (!email) return "";
    return assignees.find(a => a.email === email)?.name ?? email;
  }, [assignees]);

  const fetchRows = useCallback(async () => {
    try {
      const [data, adminUsers] = await Promise.all([
        listFeedback(),
        listFeedbackAssignees(),
      ]);
      setRows(data);
      setAssignees(adminUsers);
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchRows(); }, [fetchRows]);

  const handleUpdate = (updated: FeedbackRow) => {
    setRows(prev => prev.map(r => r.id === updated.id ? updated : r));
  };

  const countFor = (f: StatusFilter) =>
    f === "all" ? rows.length : rows.filter(r => r.status === f).length;

  const visible = filter === "all" ? rows : rows.filter(r => r.status === filter);

  return (
    <div className="flex flex-1 h-full overflow-hidden">
      {/* Sidebar */}
      <aside className="w-44 shrink-0 border-r border-slate-200 bg-white overflow-y-auto py-5 px-3">
        <nav className="space-y-0.5">
          {FILTERS.map(f => {
            const count = countFor(f.id);
            return (
              <button
                key={f.id}
                onClick={() => setFilter(f.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-left transition-colors ${
                  filter === f.id
                    ? "bg-slate-100 text-slate-900"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                <span className="text-sm font-medium">{f.label}</span>
                {count > 0 && <span className="text-xs font-mono text-slate-400">{count}</span>}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Main */}
      <div className="flex-1 overflow-y-auto bg-[#F9FAFB]">
        <div className="max-w-3xl mx-auto px-8 py-8">
          <div className="flex items-center justify-between mb-6">
            <h2 className="text-sm font-bold text-slate-900 uppercase tracking-widest">
              {FILTERS.find(f => f.id === filter)?.label ?? "All"}
              <span className="ml-2 text-sm font-mono text-slate-400 normal-case">{visible.length}</span>
            </h2>
          </div>

          {loading && (
            <div className="flex items-center justify-center py-20 text-slate-400 gap-2">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">Loading feedback…</span>
            </div>
          )}

          {error && !loading && (
            <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {!loading && !error && visible.length === 0 && (
            <div className="text-center py-20 text-slate-400">
              <div className="text-4xl mb-3">✓</div>
              <p className="text-sm font-medium">No feedback entries here.</p>
            </div>
          )}

          {!loading && !error && visible.length > 0 && (
            <div className="space-y-4">
              {visible.map(row => (
                <FeedbackCard
                  key={row.id}
                  row={row}
                  assignees={assignees}
                  assigneeLabel={assigneeLabel}
                  onUpdate={handleUpdate}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
