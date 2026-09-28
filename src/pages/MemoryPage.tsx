import { type FC, useState, useEffect, useCallback } from "react";
import { Check, X, Clock, RefreshCw, Loader2, AlertCircle, ChevronDown } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { parseUTC, timeAgo } from "@/lib/time";

// ── Types ─────────────────────────────────────────────────────────────────────

type MemoryCategory = "pending" | "people" | "active_work" | "capabilities" | "preferences" | "organization";

interface MemoryEntry {
  id: string;
  key: string;
  title: string;
  category: string;
  content: string;
  source: string | null;
  status: "pending" | "approved";
  last_confirmed_at: string;
  updated_at: string;
}

interface SyncStatus {
  started_at: string | null;
  finished_at: string | null;
  status: string | null;
}

const CATEGORIES: { id: MemoryCategory; label: string }[] = [
  { id: "pending",      label: "Pending" },
  { id: "people",       label: "People" },
  { id: "active_work",  label: "Active Work" },
  { id: "capabilities", label: "Capabilities" },
  { id: "preferences",  label: "Preferences" },
  { id: "organization", label: "Organization" },
];

// ── Helpers ───────────────────────────────────────────────────────────────────

// ── MemoryCard ────────────────────────────────────────────────────────────────

interface MemoryCardProps {
  entry: MemoryEntry;
  isPending: boolean;
  onApprove: () => void;
  onDismiss: () => void;
}

const MemoryCard: FC<MemoryCardProps> = ({ entry, isPending, onApprove, onDismiss }) => {
  const [expanded, setExpanded] = useState(isPending);

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden shadow-sm">
      {isPending && (
        <div className="flex items-center justify-between px-5 py-2.5 bg-amber-50 border-b border-amber-100">
          <span className="text-xs font-bold text-amber-700 uppercase tracking-widest">
            Pending Review
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={onApprove}
              className="flex items-center gap-1.5 text-xs font-bold text-white bg-emerald-600 hover:bg-emerald-700 px-3 py-1 rounded-md transition-colors"
            >
              <Check className="w-3.5 h-3.5" /> Approve
            </button>
            <button
              onClick={onDismiss}
              className="flex items-center gap-1.5 text-xs font-bold text-slate-600 bg-white border border-slate-200 hover:bg-slate-50 px-3 py-1 rounded-md transition-colors"
            >
              <X className="w-3.5 h-3.5" /> Dismiss
            </button>
          </div>
        </div>
      )}

      <div className="px-5 py-4">
        <button className="w-full text-left" onClick={() => setExpanded(v => !v)}>
          <div className="flex items-start justify-between gap-4">
            <div>
              <h3 className="text-sm font-bold text-slate-900">{entry.title}</h3>
              {entry.source && (
                <p className="text-xs font-mono text-slate-400 mt-0.5 truncate max-w-md">
                  {entry.source}
                </p>
              )}
            </div>
            <div className="flex items-center gap-1.5 text-xs text-slate-400 shrink-0 mt-0.5">
              <Clock className="w-3 h-3" />
              {timeAgo(entry.updated_at)}
              <ChevronDown className={`w-3.5 h-3.5 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`} />
            </div>
          </div>
        </button>

        {expanded && (
          <div className="mt-4 pt-4 border-t border-slate-100">
            <p className="text-sm text-slate-600 leading-relaxed">{entry.content}</p>
          </div>
        )}
      </div>

      {!isPending && (
        <div className="px-5 pb-3 flex justify-end">
          <button
            onClick={onDismiss}
            className="text-xs text-slate-400 hover:text-red-500 transition-colors"
          >
            Remove
          </button>
        </div>
      )}
    </div>
  );
};

// ── Main page ─────────────────────────────────────────────────────────────────

export default function MemoryPage() {
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [syncStatus, setSyncStatus] = useState<SyncStatus>({ started_at: null, finished_at: null, status: null });
  const [selectedCategory, setSelectedCategory] = useState<MemoryCategory>("pending");
  const [loading, setLoading] = useState(true);
  const [syncing, setSyncing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchMemory = useCallback(async () => {
    try {
      const res = await apiFetch("/api/memory");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setEntries(data.entries ?? []);
      setSyncStatus(data.last_sync ?? { started_at: null, finished_at: null, status: null });
      setError(null);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { fetchMemory(); }, [fetchMemory]);

  const triggerSync = async () => {
    setSyncing(true);
    try {
      await apiFetch("/api/memory/sync", { method: "POST" });
      // Poll until the sync finishes (up to 60s)
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const res = await apiFetch("/api/memory");
        const data = await res.json();
        setEntries(data.entries ?? []);
        setSyncStatus(data.last_sync ?? syncStatus);
        if (data.last_sync?.status !== "running") break;
      }
    } finally {
      setSyncing(false);
    }
  };

  const approve = async (id: string) => {
    await apiFetch(`/api/memory/${id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ status: "approved" }),
    });
    setEntries(prev =>
      prev.map(e => e.id === id ? { ...e, status: "approved" as const } : e)
    );
  };

  const dismiss = async (id: string) => {
    await apiFetch(`/api/memory/${id}`, { method: "DELETE" });
    setEntries(prev => prev.filter(e => e.id !== id));
  };

  const pendingCount = entries.filter(e => e.status === "pending").length;

  const visibleEntries =
    selectedCategory === "pending"
      ? entries.filter(e => e.status === "pending")
      : entries.filter(e => e.category === selectedCategory && e.status === "approved");

  const countFor = (cat: MemoryCategory): number =>
    cat === "pending"
      ? entries.filter(e => e.status === "pending").length
      : entries.filter(e => e.category === cat && e.status === "approved").length;

  return (
    <div className="flex flex-1 h-full overflow-hidden">
      {/* Left sidebar */}
      <aside className="w-56 shrink-0 border-r border-slate-200 bg-white overflow-y-auto py-5 px-3">
        {/* Sync status */}
        <div className="px-3 pb-4 mb-3 border-b border-slate-100">
          <button
            onClick={triggerSync}
            disabled={syncing}
            className="w-full flex items-center justify-center gap-2 text-xs font-semibold text-slate-700 bg-slate-100 hover:bg-slate-200 disabled:opacity-50 px-3 py-2 rounded-lg transition-colors"
          >
            {syncing
              ? <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Syncing…</>
              : <><RefreshCw className="w-3.5 h-3.5" /> Sync Now</>
            }
          </button>
          {syncStatus.finished_at && (
            <p className="text-[10px] text-slate-400 text-center mt-1.5">
              Last synced {timeAgo(syncStatus.finished_at)}
              {syncStatus.status === "error" && (
                <span className="text-red-500 ml-1">· failed</span>
              )}
            </p>
          )}
          {!syncStatus.finished_at && (
            <p className="text-[10px] text-slate-400 text-center mt-1.5">Never synced</p>
          )}
        </div>

        <nav className="space-y-0.5">
          {CATEGORIES.map((cat) => {
            const count = countFor(cat.id);
            const isPending = cat.id === "pending";
            return (
              <button
                key={cat.id}
                onClick={() => setSelectedCategory(cat.id)}
                className={`w-full flex items-center justify-between px-3 py-2 rounded-lg text-left transition-colors ${
                  selectedCategory === cat.id
                    ? "bg-slate-100 text-slate-900"
                    : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                }`}
              >
                <div className="flex items-center gap-2">
                  {isPending && count > 0 && (
                    <span className="w-2 h-2 rounded-full bg-amber-400 shrink-0" />
                  )}
                  <span className="text-sm font-medium">{cat.label}</span>
                </div>
                {count > 0 && (
                  <span className="text-xs font-mono text-slate-400">{count}</span>
                )}
              </button>
            );
          })}
        </nav>
      </aside>

      {/* Main content */}
      <div className="flex-1 overflow-y-auto bg-[#F9FAFB]">
        <div className="max-w-3xl mx-auto px-8 py-8">
          {/* Header */}
          <div className="flex items-center justify-between mb-6">
            <h1 className="text-lg font-bold text-slate-900 uppercase tracking-widest text-sm">
              {selectedCategory.replace("_", " ")}
              <span className="ml-2 text-sm font-mono text-slate-400 normal-case">
                {visibleEntries.length}
              </span>
            </h1>
            {selectedCategory === "pending" && pendingCount > 0 && (
              <div className="flex gap-2">
                <button
                  onClick={async () => {
                    for (const e of entries.filter(e => e.status === "pending")) {
                      await apiFetch(`/api/memory/${e.id}`, {
                        method: "PUT",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ status: "approved" }),
                      });
                    }
                    setEntries(prev =>
                      prev.map(e => e.status === "pending" ? { ...e, status: "approved" as const } : e)
                    );
                  }}
                  className="text-xs font-semibold text-emerald-700 bg-emerald-50 border border-emerald-200 px-3 py-1.5 rounded-md hover:bg-emerald-100 transition-colors"
                >
                  Approve all
                </button>
                <button
                  onClick={async () => {
                    for (const e of entries.filter(e => e.status === "pending")) {
                      await apiFetch(`/api/memory/${e.id}`, { method: "DELETE" });
                    }
                    setEntries(prev => prev.filter(e => e.status !== "pending"));
                  }}
                  className="text-xs font-semibold text-slate-600 bg-white border border-slate-200 px-3 py-1.5 rounded-md hover:bg-slate-50 transition-colors"
                >
                  Dismiss all
                </button>
              </div>
            )}
          </div>

          {/* Loading / error */}
          {loading && (
            <div className="flex items-center justify-center py-20 text-slate-400 gap-2">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">Loading memory…</span>
            </div>
          )}

          {error && !loading && (
            <div className="flex items-center gap-3 p-4 bg-red-50 border border-red-200 rounded-xl text-red-700 text-sm">
              <AlertCircle className="w-4 h-4 shrink-0" />
              {error}
            </div>
          )}

          {!loading && !error && visibleEntries.length === 0 && (
            <div className="text-center py-20 text-slate-400">
              <div className="text-4xl mb-3">
                {selectedCategory === "pending" ? "✓" : "🧠"}
              </div>
              <p className="text-sm font-medium">
                {selectedCategory === "pending"
                  ? "All caught up — no pending memories."
                  : "No entries yet. Click Sync Now to mine your conversations."}
              </p>
            </div>
          )}

          {!loading && !error && visibleEntries.length > 0 && (
            <div className="space-y-4">
              {visibleEntries.map(entry => (
                <MemoryCard
                  key={entry.id}
                  entry={entry}
                  isPending={entry.status === "pending"}
                  onApprove={() => approve(entry.id)}
                  onDismiss={() => dismiss(entry.id)}
                />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
