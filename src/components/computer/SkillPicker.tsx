import { useMemo, useState } from "react";
import { Lock, Search, Sparkles, X } from "lucide-react";

/** One row of `/api/skills`, trimmed to what the picker needs. */
export type SkillAccess = {
  slug: string;
  name: string;
  description: string;
  accessible: boolean;
  tier1?: string | null;
  tier2?: string | null;
  risk_level?: string | null;
  owning_department?: string | null;
};

type SkillPickerProps = {
  skills: SkillAccess[];
  selected: string[];
  onToggle: (slug: string) => void;
  onClose: () => void;
  /** Omit to hide the "Clear all" button (e.g. single-select callers). */
  onClear?: () => void;
};

/**
 * Full-panel skill browser: search, checkbox rows, access/risk badges.
 *
 * Extracted from the Computer page's composer "Skills" button so the flow
 * editor's "+ Skill step" can reuse the exact same picker rather than a
 * second implementation that could drift from it — one set of access rules
 * shown, in one place.
 */
export default function SkillPicker({ skills, selected, onToggle, onClose, onClear }: SkillPickerProps) {
  const [query, setQuery] = useState("");

  const visibleSkills = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return skills;
    return skills.filter((skill) =>
      `${skill.name} ${skill.slug} ${skill.description ?? ""}`.toLowerCase().includes(needle),
    );
  }, [skills, query]);

  return (
    <div className="absolute inset-0 z-20 flex flex-col bg-white">
      <div className="flex shrink-0 items-center gap-2 border-b border-slate-100 px-3 py-2.5">
        <Sparkles className="h-4 w-4 text-[#FF444F]" />
        <span className="text-sm font-bold text-slate-900">Skills</span>
        <span className="text-[11px] text-slate-400">{selected.length} selected</span>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close skills"
          className="ml-auto rounded-md p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="shrink-0 px-3 py-2">
        <div className="flex items-center gap-2 rounded-lg border border-slate-200 px-2.5 py-1.5 focus-within:border-slate-400">
          <Search className="h-3.5 w-3.5 shrink-0 text-slate-400" />
          <input
            autoFocus
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search skills…"
            className="w-full text-sm outline-none"
          />
        </div>
      </div>

      <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto px-3 pb-3">
        {visibleSkills.length === 0 && (
          <p className="py-8 text-center text-xs text-slate-400">
            {skills.length === 0 ? "Loading your skills…" : "No skills match that search."}
          </p>
        )}
        {visibleSkills.map((skill) => {
          const checked = selected.includes(skill.slug);
          return (
            <label
              key={skill.slug}
              className={`flex cursor-pointer items-start gap-2.5 rounded-xl border px-3 py-2.5 transition ${
                checked
                  ? "border-[#FF444F]/40 bg-[#FF444F]/5"
                  : skill.accessible
                    ? "border-slate-200 hover:border-slate-300"
                    : "border-slate-100 bg-slate-50/60"
              } ${skill.accessible ? "" : "cursor-not-allowed"}`}
            >
              <input
                type="checkbox"
                className="mt-0.5 shrink-0 accent-[#FF444F]"
                checked={checked}
                disabled={!skill.accessible}
                onChange={() => onToggle(skill.slug)}
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-1.5">
                  <span className="truncate text-sm font-semibold text-slate-800">
                    {skill.name || skill.slug}
                  </span>
                  {!skill.accessible && (
                    <span className="inline-flex shrink-0 items-center gap-0.5 rounded bg-slate-200 px-1.5 py-px text-[10px] font-bold uppercase tracking-wide text-slate-500">
                      <Lock className="h-2.5 w-2.5" /> No access
                    </span>
                  )}
                  {skill.risk_level === "high" && (
                    <span className="shrink-0 rounded bg-amber-100 px-1.5 py-px text-[10px] font-bold uppercase tracking-wide text-amber-700">
                      High risk
                    </span>
                  )}
                </span>
                {skill.description && (
                  <span className="mt-0.5 line-clamp-2 block text-[11px] leading-4 text-slate-500">
                    {skill.description}
                  </span>
                )}
                <span className="mt-1 block font-mono text-[10px] text-slate-400">{skill.slug}</span>
              </span>
            </label>
          );
        })}
      </div>

      <div className="flex shrink-0 items-center justify-between gap-2 border-t border-slate-100 px-3 py-2.5">
        {onClear ? (
          <button
            type="button"
            disabled={selected.length === 0}
            onClick={onClear}
            className="text-xs font-semibold text-slate-500 hover:text-slate-800 disabled:opacity-40"
          >
            Clear all
          </button>
        ) : (
          <span />
        )}
        <button
          type="button"
          onClick={onClose}
          className="rounded-lg bg-slate-900 px-3 py-1.5 text-xs font-semibold text-white hover:bg-slate-800"
        >
          Done
        </button>
      </div>
    </div>
  );
}
