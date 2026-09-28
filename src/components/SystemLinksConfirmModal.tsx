import { useEffect, useState } from "react";
import { CheckCircle2, Loader2, Plus, ShieldAlert, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { SystemCriticalityBadge } from "@/components/SystemCriticalityBadge";

type Direction = "read" | "write" | "read_write";

interface SystemLinkRow {
  system_name: string;
  risk_level: string | null;
  direction: Direction;
  confidence?: string;
  source?: string;
  integration_tag?: string;
  manual?: boolean;
}

interface KnownSystem {
  system_name: string;
  risk_level: string | null;
}

const DIRECTION_LABELS: Record<Direction, string> = {
  read: "Read",
  write: "Write",
  read_write: "Read/Write",
};

/**
 * Human confirmation gate for a skill's auto-detected system access, shown
 * once a submission's review_status is "awaiting_confirmation" (AI security
 * review passed, but the skill has not actually published yet). The
 * publisher can remove a wrongly-detected system, change read/write
 * direction, or add one that was missed — then confirming triggers publish.
 */
export function SystemLinksConfirmModal({
  submissionId,
  slug,
  onDone,
  onClose,
}: {
  submissionId: string;
  slug: string;
  onDone: () => void;
  onClose?: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [rows, setRows] = useState<SystemLinkRow[]>([]);
  const [knownSystems, setKnownSystems] = useState<KnownSystem[]>([]);
  const [addInput, setAddInput] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      apiFetch(`/api/skills/submissions/${submissionId}`).then((r) => (r.ok ? r.json() : null)),
      apiFetch("/api/skills/known-systems").then((r) => (r.ok ? r.json() : null)),
    ])
      .then(([sub, known]) => {
        if (cancelled) return;
        const proposed = Array.isArray(sub?.proposed_system_links) ? sub.proposed_system_links : [];
        setRows(
          proposed
            .filter((p: any) => p?.system_name)
            .map((p: any) => ({
              system_name: p.system_name,
              risk_level: p.risk_level ?? null,
              direction: (p.direction ?? "read") as Direction,
              confidence: p.confidence,
              source: p.source,
              integration_tag: p.integration_tag,
            }))
        );
        setKnownSystems(Array.isArray(known?.systems) ? known.systems : []);
      })
      .catch(() => {
        if (!cancelled) setError("Failed to load detected system access.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [submissionId]);

  const removeRow = (name: string) =>
    setRows((prev) => prev.filter((r) => r.system_name !== name));

  const setDirection = (name: string, direction: Direction) =>
    setRows((prev) => prev.map((r) => (r.system_name === name ? { ...r, direction } : r)));

  const addSystem = (name: string) => {
    const trimmed = name.trim();
    if (!trimmed) return;
    if (rows.some((r) => r.system_name.toLowerCase() === trimmed.toLowerCase())) {
      setAddInput("");
      return;
    }
    const known = knownSystems.find((s) => s.system_name.toLowerCase() === trimmed.toLowerCase());
    setRows((prev) => [
      ...prev,
      {
        system_name: known?.system_name ?? trimmed,
        risk_level: known?.risk_level ?? null,
        direction: "read",
        manual: true,
      },
    ]);
    setAddInput("");
  };

  const handleConfirm = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/skills/submissions/${submissionId}/confirm-systems`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          system_links: rows.map((r) => ({
            system_name: r.system_name,
            direction: r.direction,
            integration_tag: r.integration_tag,
          })),
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail || `Confirmation failed (${res.status})`);
      }
      onDone();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Confirmation failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg border border-slate-200 flex flex-col max-h-[85vh]">
        <div className="px-6 pt-5 pb-4 border-b border-slate-100 flex items-start justify-between shrink-0">
          <div>
            <h2 className="text-base font-bold text-slate-900">Confirm system access</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Before <code className="font-mono">{slug}</code> goes live, review the systems it
              accesses. Remove anything wrong, adjust direction, or add one we missed.
            </p>
          </div>
          {onClose && (
            <button
              onClick={onClose}
              className="text-slate-400 hover:text-slate-700 text-lg leading-none ml-4 shrink-0"
            >
              ✕
            </button>
          )}
        </div>

        <div className="overflow-y-auto flex-1 px-6 py-5 space-y-4">
          {loading ? (
            <div className="flex justify-center py-8 text-slate-400">
              <Loader2 className="w-6 h-6 animate-spin" />
            </div>
          ) : (
            <>
              {rows.length === 0 && (
                <p className="text-sm text-slate-500">
                  No systems detected. Add one below if this skill accesses one.
                </p>
              )}
              <div className="space-y-2">
                {rows.map((row) => (
                  <div
                    key={row.system_name}
                    className="flex items-center gap-2 border border-slate-200 rounded-lg px-3 py-2"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="text-sm font-semibold text-slate-800 truncate">
                          {row.system_name}
                        </span>
                        <SystemCriticalityBadge riskLevel={row.risk_level} />
                        {row.manual && (
                          <span className="text-[9px] font-bold uppercase tracking-wider text-slate-400">
                            Manually added
                          </span>
                        )}
                      </div>
                    </div>
                    <select
                      value={row.direction}
                      onChange={(e) => setDirection(row.system_name, e.target.value as Direction)}
                      className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-white"
                    >
                      {(Object.keys(DIRECTION_LABELS) as Direction[]).map((d) => (
                        <option key={d} value={d}>
                          {DIRECTION_LABELS[d]}
                        </option>
                      ))}
                    </select>
                    <button
                      type="button"
                      onClick={() => removeRow(row.system_name)}
                      className="text-slate-400 hover:text-red-600 shrink-0"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>

              <div className="flex gap-2">
                <input
                  type="text"
                  value={addInput}
                  onChange={(e) => setAddInput(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      addSystem(addInput);
                    }
                  }}
                  list="known-systems-list"
                  placeholder="Add a system this skill accesses…"
                  className="flex-1 bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
                />
                <datalist id="known-systems-list">
                  {knownSystems.map((s) => (
                    <option key={s.system_name} value={s.system_name} />
                  ))}
                </datalist>
                <button
                  type="button"
                  onClick={() => addSystem(addInput)}
                  className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-semibold rounded-md transition-colors flex items-center gap-1"
                >
                  <Plus className="w-3.5 h-3.5" />
                  Add
                </button>
              </div>

              {error && (
                <div className="flex items-start gap-2 bg-red-50 border border-red-200 rounded-lg px-3 py-2 text-xs text-red-700">
                  <ShieldAlert className="w-3.5 h-3.5 flex-shrink-0 mt-0.5" />
                  {error}
                </div>
              )}
            </>
          )}
        </div>

        <div className="px-6 py-4 border-t border-slate-100 shrink-0 flex justify-end">
          <button
            type="button"
            disabled={loading || submitting}
            onClick={handleConfirm}
            className="flex items-center gap-2 px-4 py-2 bg-slate-900 text-white text-sm font-bold rounded-lg hover:bg-slate-800 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <CheckCircle2 className="w-4 h-4" />}
            Confirm &amp; Publish
          </button>
        </div>
      </div>
    </div>
  );
}
