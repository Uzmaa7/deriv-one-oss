'use strict';

import { useCallback, useEffect, useState } from "react";
import { ClipboardList, FileText, GitCommit, Loader2, RotateCcw } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { SkillMarkdownDiff } from "@/components/SkillMarkdownDiff";

interface VersionSummary {
  version: number;
  is_current: boolean;
  reason: string;
  source: string;
  user_email: string | null;
  user_name: string | null;
  actor_role: string;
  actor_label: string;
  lines_added: number;
  lines_removed: number;
  created_at: string | null;
}

interface VersionDetail {
  version: number;
  content: string;
  previous_content: string;
  previous_version: number | null;
  reason: string;
  source: string;
  user_email: string | null;
  user_name: string | null;
  actor_role: string;
  actor_label: string;
  lines_added: number;
  lines_removed: number;
  created_at: string | null;
}

const SOURCE_LABEL: Record<string, string> = {
  baseline: "Baseline",
  manual_edit: "Manual edit",
  ai_edit: "AI edit",
  revert: "Revert",
  skill_edit: "Edit Skill",
};

const ROLE_LABEL: Record<string, string> = {
  owner: "Owner",
  collaborator: "Collaborator",
  admin: "Admin",
};

function formatWhen(iso: string | null): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleString(undefined, {
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

function actorInitials(label: string): string {
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase();
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return "?";
}

function versionLabel(version: number): string {
  return `v${version}`;
}

function ActorBadge({ role }: { role: string }) {
  const label = ROLE_LABEL[role] ?? role;
  const cls =
    role === "owner"
      ? "bg-indigo-50 text-indigo-700 ring-indigo-100"
      : role === "collaborator"
        ? "bg-violet-50 text-violet-700 ring-violet-100"
        : "bg-slate-100 text-slate-600 ring-slate-200";
  return (
    <span className={`text-[10px] font-semibold uppercase tracking-wide px-2 py-0.5 rounded-full ring-1 ${cls}`}>
      {label}
    </span>
  );
}

function DiffBadge({ added, removed }: { added: number; removed: number }) {
  if (added === 0 && removed === 0) {
    return <span className="text-[11px] font-medium text-slate-400">No line changes</span>;
  }
  return (
    <span className="inline-flex items-center gap-1.5 text-[11px] font-mono font-medium">
      {added > 0 && <span className="text-emerald-700">+{added}</span>}
      {removed > 0 && <span className="text-red-600">−{removed}</span>}
    </span>
  );
}

interface Props {
  slug: string;
  baseHash?: string | null;
  onReverted: () => void;
}

export function SkillVersionHistoryTab({ slug, baseHash, onReverted }: Props) {
  const [versions, setVersions] = useState<VersionSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<number | null>(null);
  const [detail, setDetail] = useState<VersionDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [reverting, setReverting] = useState(false);

  const loadVersions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/skills/${slug}/versions`);
      if (!res.ok) throw new Error(`Failed to load version history (${res.status})`);
      const data = await res.json();
      const list: VersionSummary[] = data?.versions ?? [];
      setVersions(list);
      if (list.length > 0) {
        setSelected(prev => (prev && list.some(v => v.version === prev) ? prev : list[0].version));
      } else {
        setSelected(null);
        setDetail(null);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load version history.");
    } finally {
      setLoading(false);
    }
  }, [slug]);

  useEffect(() => {
    void loadVersions();
  }, [loadVersions]);

  useEffect(() => {
    if (selected == null) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    apiFetch(`/api/skills/${slug}/versions/${selected}`)
      .then(async res => {
        if (!res.ok) throw new Error(`Failed to load version (${res.status})`);
        return res.json();
      })
      .then(d => { if (!cancelled) setDetail(d as VersionDetail); })
      .catch(() => { if (!cancelled) setDetail(null); })
      .finally(() => { if (!cancelled) setDetailLoading(false); });
    return () => { cancelled = true; };
  }, [slug, selected]);

  const handleRevert = async (version: number) => {
    if (!window.confirm(`Revert live skill to version ${version}? This creates a new audit entry.`)) return;
    if (!baseHash) {
      setError("Missing content hash. Reload the skill and try again.");
      return;
    }
    setReverting(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/skills/${slug}/versions/${version}/revert`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ base_hash: baseHash }),
      });
      const d = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detailMsg = d?.detail;
        if (res.status === 409 && detailMsg && typeof detailMsg === "object" && detailMsg.code === "skill_conflict") {
          throw new Error(detailMsg.message || "Skill was updated by someone else. Reload and try again.");
        }
        throw new Error(typeof detailMsg === "string" ? detailMsg : detailMsg?.message || `Revert failed (${res.status})`);
      }
      await loadVersions();
      onReverted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Revert failed.");
    } finally {
      setReverting(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin" />
      </div>
    );
  }

  if (versions.length === 0) {
    return (
      <div className="text-center py-20 border border-dashed border-slate-200 rounded-xl bg-slate-50/60">
        <ClipboardList className="w-9 h-9 text-slate-300 mx-auto mb-3" />
        <p className="text-sm font-semibold text-slate-700">No version history yet</p>
        <p className="text-xs text-slate-500 mt-1 max-w-xs mx-auto">
          Saves by owners and collaborators will show up here with who changed what and why.
        </p>
      </div>
    );
  }

  const selectedSummary = versions.find(v => v.version === selected);
  const editCount = versions.filter(v => v.source !== "baseline").length;

  return (
    <div className="space-y-3">
      {error && (
        <p className="text-xs text-red-700 bg-red-50 border border-red-100 rounded-lg px-3 py-2">{error}</p>
      )}

      <div className="flex flex-col xl:flex-row border border-slate-200 rounded-xl overflow-hidden bg-white shadow-sm min-h-[36rem]">
        {/* Version list */}
        <aside className="xl:w-80 shrink-0 border-b xl:border-b-0 xl:border-r border-slate-200 bg-slate-50/70">
          <div className="px-4 py-3 border-b border-slate-200/80">
            <div className="flex items-center gap-2">
              <GitCommit className="w-4 h-4 text-slate-400" />
              <div>
                <p className="text-xs font-semibold text-slate-900">All versions</p>
                <p className="text-[11px] text-slate-500">
                  {versions.length} version{versions.length !== 1 ? "s" : ""}
                  {editCount > 0 ? ` · ${editCount} edit${editCount !== 1 ? "s" : ""}` : ""}
                </p>
              </div>
            </div>
          </div>

          <div className="max-h-[40rem] overflow-y-auto p-2">
            <ul className="space-y-1">
              {versions.map((v, idx) => {
                const isSelected = selected === v.version;
                const isLast = idx === versions.length - 1;
                return (
                  <li key={v.version} className="relative">
                    {!isLast && (
                      <span
                        className="absolute left-[1.125rem] top-9 bottom-0 w-px bg-slate-200"
                        aria-hidden
                      />
                    )}
                    <button
                      type="button"
                      onClick={() => setSelected(v.version)}
                      className={`relative w-full text-left rounded-lg px-2.5 py-2.5 transition-all ${
                        isSelected
                          ? "bg-white ring-1 ring-slate-200 shadow-sm"
                          : "hover:bg-white/70"
                      }`}
                    >
                      <div className="flex items-start gap-2.5">
                        <span
                          className={`mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-bold ring-2 ring-white ${
                            isSelected
                              ? "bg-[#FF444F] text-white"
                              : v.is_current
                                ? "bg-emerald-500 text-white"
                                : v.source === "baseline"
                                  ? "bg-slate-400 text-white"
                                  : "bg-slate-300 text-white"
                          }`}
                        >
                          {v.version}
                        </span>
                        <div className="min-w-0 flex-1">
                          <p className={`text-sm leading-snug ${isSelected ? "font-bold text-slate-900" : "font-semibold text-slate-800"}`}>
                            {versionLabel(v.version)}
                          </p>
                          {v.reason && v.source !== "baseline" && (
                            <p className="text-[11px] text-slate-600 mt-1 line-clamp-2">{v.reason}</p>
                          )}
                          <p className="text-[11px] text-slate-500 mt-1">
                            {formatWhen(v.created_at)}
                          </p>
                          <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                            <span className="text-[10px] text-slate-500">
                              {SOURCE_LABEL[v.source] ?? v.source}
                            </span>
                            {v.is_current && (
                              <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-700 bg-emerald-50 px-1.5 py-0.5 rounded">
                                Live
                              </span>
                            )}
                            {v.source !== "baseline" && (
                              <DiffBadge added={v.lines_added} removed={v.lines_removed} />
                            )}
                          </div>
                        </div>
                      </div>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        </aside>

        {/* Detail panel */}
        <main className="flex-1 min-w-0 flex flex-col bg-white">
          {detailLoading ? (
            <div className="flex flex-1 items-center justify-center text-slate-400">
              <Loader2 className="w-5 h-5 animate-spin" />
            </div>
          ) : detail && selectedSummary ? (
            <>
              <header className="px-6 py-4 border-b border-slate-100">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="flex items-start gap-3 min-w-0">
                    <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-slate-100 text-xs font-bold text-slate-600">
                      {actorInitials(detail.actor_label)}
                    </div>
                    <div className="min-w-0">
                      <div className="flex flex-wrap items-center gap-2">
                        <h3 className="text-base font-bold text-slate-900">{versionLabel(detail.version)}</h3>
                        <ActorBadge role={detail.actor_role} />
                        {selectedSummary.is_current && (
                          <span className="text-[10px] font-semibold uppercase tracking-wide text-emerald-700 bg-emerald-50 px-2 py-0.5 rounded-full">
                            Live
                          </span>
                        )}
                      </div>
                      <p className="text-sm text-slate-700 mt-0.5">
                        {detail.actor_label}
                        {detail.user_email && detail.actor_label !== detail.user_email ? (
                          <span className="text-slate-400"> · {detail.user_email}</span>
                        ) : null}
                      </p>
                      <p className="text-xs text-slate-500 mt-1">
                        {SOURCE_LABEL[detail.source] ?? detail.source}
                        {detail.created_at ? ` · ${formatWhen(detail.created_at)}` : ""}
                      </p>
                    </div>
                  </div>

                  {!selectedSummary.is_current && detail.source !== "baseline" && (
                    <button
                      type="button"
                      onClick={() => void handleRevert(detail.version)}
                      disabled={reverting}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold rounded-lg border border-slate-200 bg-white text-slate-700 hover:bg-slate-50 hover:border-slate-300 disabled:opacity-50 shrink-0"
                    >
                      {reverting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <RotateCcw className="w-3.5 h-3.5" />}
                      Revert to this
                    </button>
                  )}
                </div>

                {detail.reason && detail.source !== "baseline" && (
                  <blockquote className="mt-3 rounded-lg border border-slate-100 bg-slate-50 px-3 py-2.5 text-sm text-slate-700 leading-relaxed">
                    {detail.reason}
                  </blockquote>
                )}
              </header>

              <div className="flex-1 overflow-y-auto p-6 space-y-8">
                <section className="space-y-3">
                  <div className="flex items-center gap-2">
                    <FileText className="w-4 h-4 text-slate-400" />
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                      Skill content at {versionLabel(detail.version)}
                    </h4>
                  </div>
                  <pre className="bg-slate-50 border border-slate-200 rounded-xl p-4 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words text-slate-800 max-h-[32rem] overflow-y-auto">
                    {detail.content}
                  </pre>
                </section>

                {detail.previous_version != null ? (
                  <section className="space-y-3">
                    <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-700">
                      Changes from {versionLabel(detail.previous_version)}
                    </h4>
                    <SkillMarkdownDiff
                      variant="history"
                      current={detail.previous_content}
                      proposed={detail.content}
                      summary={`v${detail.previous_version} → v${detail.version}`}
                    />
                  </section>
                ) : (
                  <p className="text-xs text-slate-500">
                    This is the first recorded version — there is no earlier snapshot to compare against.
                  </p>
                )}
              </div>
            </>
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-slate-400">
              Select a version to view its content and changes
            </div>
          )}
        </main>
      </div>
    </div>
  );
}
