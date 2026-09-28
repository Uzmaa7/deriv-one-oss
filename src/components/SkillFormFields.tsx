import { useState, useEffect, useRef } from "react";
import { ChevronDown, ChevronUp, Tag, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { SystemCriticalityBadge } from "@/components/SystemCriticalityBadge";

// ── Types ─────────────────────────────────────────────────────────────────────

export type RiskLevel = "standard" | "elevated" | "critical";
export type CrossDeptPolicy = "requestable" | "restricted" | "blocked";
export type CrossDeptDefaultGrant =
  | "time_limited_30d"
  | "time_limited_90d"
  | "permanent"
  | "restricted_read_only";

// ── Constants ─────────────────────────────────────────────────────────────────

// Departments offered in the picker. "*" means everyone; the rest are whatever
// the deployment's own ACL manifest already names, fetched at runtime. Baking an
// organisation chart in here would make the picker wrong for every other
// deployment, so the fallback is deliberately just the wildcard.
export const ALL_DEPARTMENTS = ["*"];

export const RISK_LEVEL_OPTIONS: { value: RiskLevel; label: string; hint: string }[] = [
  { value: "standard", label: "Standard", hint: "Read-only or low-impact operations" },
  { value: "elevated", label: "Elevated", hint: "Write access, PII, or financial data" },
  { value: "critical", label: "Critical", hint: "Broad destructive or highly sensitive ops" },
];

export const CROSS_DEPT_POLICY_OPTIONS: {
  value: CrossDeptPolicy;
  label: string;
  hint: string;
}[] = [
  { value: "requestable", label: "Requestable", hint: "Others can request access" },
  { value: "restricted", label: "Restricted", hint: "Requires manual admin approval" },
  { value: "blocked", label: "Blocked", hint: "No cross-dept access permitted" },
];

export const DEFAULT_GRANT_OPTIONS: { value: CrossDeptDefaultGrant; label: string }[] = [
  { value: "time_limited_30d", label: "30-day time-limited" },
  { value: "time_limited_90d", label: "90-day time-limited" },
  { value: "permanent", label: "Permanent" },
  { value: "restricted_read_only", label: "Restricted read-only" },
];

// ── DepartmentPicker ──────────────────────────────────────────────────────────

export function DepartmentPicker({
  selected,
  onChange,
}: {
  selected: string[];
  onChange: (depts: string[]) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", handleClick);
    return () => document.removeEventListener("mousedown", handleClick);
  }, []);

  const toggle = (dept: string) => {
    if (dept === "*") {
      onChange(selected.includes("*") ? [] : ["*"]);
      return;
    }
    const without = selected.filter((d) => d !== "*");
    if (without.includes(dept)) {
      onChange(without.filter((d) => d !== dept));
    } else {
      onChange([...without, dept]);
    }
  };

  const label =
    selected.includes("*")
      ? "Everyone (all departments)"
      : selected.length === 0
      ? "Select departments…"
      : selected.length === 1
      ? selected[0]
      : `${selected.length} departments selected`;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="w-full flex items-center justify-between bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2.5 text-sm text-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-900 hover:border-slate-300 transition-colors"
      >
        <span className={selected.length === 0 ? "text-slate-400" : ""}>{label}</span>
        {open ? (
          <ChevronUp className="w-4 h-4 text-slate-400 flex-shrink-0" />
        ) : (
          <ChevronDown className="w-4 h-4 text-slate-400 flex-shrink-0" />
        )}
      </button>

      {open && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg max-h-64 overflow-y-auto">
          {ALL_DEPARTMENTS.map((dept) => {
            const isChecked = selected.includes(dept);
            return (
              <label
                key={dept}
                className="flex items-center gap-2.5 px-3 py-2 hover:bg-slate-50 cursor-pointer text-sm"
              >
                <input
                  type="checkbox"
                  checked={isChecked}
                  onChange={() => toggle(dept)}
                  className="w-4 h-4 rounded border-slate-300 text-slate-900 accent-slate-900"
                />
                <span
                  className={
                    dept === "*" ? "font-semibold text-slate-700" : "text-slate-700"
                  }
                >
                  {dept === "*" ? "Everyone (all departments)" : dept}
                </span>
              </label>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── IntegrationTags ───────────────────────────────────────────────────────────

interface IntegrationMatch {
  system_name: string | null;
  risk_level: string | null;
  match_confidence: "exact" | "fuzzy" | "unmatched";
}

export function IntegrationTags({
  tags,
  onChange,
}: {
  tags: string[];
  onChange: (tags: string[]) => void;
}) {
  const [input, setInput] = useState("");
  const [matches, setMatches] = useState<Record<string, IntegrationMatch>>({});
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Best-effort catalogue resolution preview — debounced, purely informational.
  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (tags.length === 0) {
      setMatches({});
      return;
    }
    debounceRef.current = setTimeout(() => {
      apiFetch("/api/skills/resolve-integrations", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ integrations: tags }),
      })
        .then((r) => (r.ok ? r.json() : null))
        .then((data) => {
          if (!data) return;
          const next: Record<string, IntegrationMatch> = {};
          for (const r of data.results ?? []) {
            next[r.integration_tag] = {
              system_name: r.system_name,
              risk_level: r.risk_level,
              match_confidence: r.match_confidence,
            };
          }
          setMatches(next);
        })
        .catch(() => {
          // Best-effort preview — silently ignore failures (e.g. catalogue unavailable).
        });
    }, 500);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [tags]);

  const add = () => {
    const val = input.trim();
    if (val && !tags.includes(val)) {
      onChange([...tags, val]);
    }
    setInput("");
  };

  const remove = (tag: string) => onChange(tags.filter((t) => t !== tag));

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <input
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add();
            }
          }}
          placeholder="e.g. Data Warehouse (read), Slack (write)…"
          className="flex-1 bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
        />
        <button
          type="button"
          onClick={add}
          className="px-3 py-2 bg-slate-100 hover:bg-slate-200 text-slate-700 text-sm font-semibold rounded-md transition-colors"
        >
          Add
        </button>
      </div>
      {tags.length > 0 && (
        <div className="flex flex-col gap-1.5">
          {tags.map((tag) => {
            const match = matches[tag];
            return (
              <div key={tag} className="flex items-center gap-1.5 flex-wrap">
                <span className="inline-flex items-center gap-1 bg-slate-100 text-slate-700 text-xs font-medium px-2 py-1 rounded-full">
                  <Tag className="w-3 h-3" />
                  {tag}
                  <button
                    type="button"
                    onClick={() => remove(tag)}
                    className="ml-0.5 text-slate-400 hover:text-slate-700"
                    aria-label={`Remove ${tag}`}
                  >
                    <X className="w-3 h-3" />
                  </button>
                </span>
                {match && match.match_confidence !== "unmatched" && (
                  <span className="text-[10px] text-slate-500 flex items-center gap-1">
                    ✓ {match.system_name}
                    <SystemCriticalityBadge riskLevel={match.risk_level} />
                    {match.match_confidence === "fuzzy" && (
                      <span className="text-amber-600">(best-effort match)</span>
                    )}
                  </span>
                )}
                {match && match.match_confidence === "unmatched" && (
                  <span className="text-[10px] text-slate-400">⚠ not in the catalogue</span>
                )}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
