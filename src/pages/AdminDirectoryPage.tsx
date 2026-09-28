import { useState, useEffect, useMemo } from "react";
import {
  BookUser,
  Building2,
  ChevronDown,
  ChevronRight,
  Loader2,
  RefreshCw,
  Search,
  Shield,
  Users,
  X,
  Zap,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

// ── Types ──────────────────────────────────────────────────────────────────────

interface GrantedSkill {
  skill_slug: string;
  grant_type: string | null;
  expires_at: string | null;
}

interface EmployeeSkills {
  tier1: string[];
  granted: GrantedSkill[];
  pending: string[];
}

interface Employee {
  email: string;
  name: string;
  department: string;
  sage_department: string;
  job_title: string;
  manager: string;
  team_level2: string;
  skills: EmployeeSkills;
}

interface SkillMeta {
  name: string;
  risk_level: "standard" | "elevated" | "critical";
  departments: string[];
  description: string;
  is_universal: boolean;
}

interface DirectoryData {
  employees: Employee[];
  skill_meta: Record<string, SkillMeta>;
  total: number;
}

// ── Helpers ────────────────────────────────────────────────────────────────────

const RISK_COLORS: Record<string, string> = {
  standard: "text-emerald-700 bg-emerald-50 border-emerald-200",
  elevated: "text-amber-700 bg-amber-50 border-amber-200",
  critical: "text-red-700 bg-red-50 border-red-200",
};

function formatExpiry(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

function skillDisplayName(slug: string, meta: Record<string, SkillMeta>): string {
  return meta[slug]?.name ?? slug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());
}

// ── Skill badge ────────────────────────────────────────────────────────────────

function SkillBadge({
  slug,
  meta,
  variant,
  expiry,
}: {
  slug: string;
  meta: Record<string, SkillMeta>;
  variant: "tier1" | "granted" | "pending";
  expiry?: string | null;
}) {
  const risk = meta[slug]?.risk_level ?? "standard";
  const base = "inline-flex items-center gap-1 text-[10px] font-semibold px-2 py-0.5 rounded-full border";
  const styles =
    variant === "tier1"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : variant === "granted"
      ? "bg-blue-50 text-blue-700 border-blue-200"
      : "bg-amber-50 text-amber-700 border-amber-200";
  return (
    <span className={`${base} ${styles}`} title={meta[slug]?.description ?? slug}>
      {variant === "tier1" && <Shield className="w-2.5 h-2.5" />}
      {variant === "granted" && <Zap className="w-2.5 h-2.5" />}
      {skillDisplayName(slug, meta)}
      {expiry && <span className="opacity-60">· {formatExpiry(expiry)}</span>}
      {variant !== "tier1" && (
        <span className={`ml-0.5 text-[8px] uppercase tracking-wide px-1 rounded border ${RISK_COLORS[risk]}`}>
          {risk}
        </span>
      )}
    </span>
  );
}

// ── User row / expandable card ─────────────────────────────────────────────────

function UserRow({
  emp,
  meta,
  defaultOpen,
}: {
  emp: Employee;
  meta: Record<string, SkillMeta>;
  defaultOpen?: boolean;
}) {
  const [open, setOpen] = useState(defaultOpen ?? false);
  const totalSkills =
    emp.skills.tier1.length + emp.skills.granted.length + emp.skills.pending.length;

  return (
    <div className="bg-white border border-gray-200 rounded-xl overflow-hidden">
      <button
        className="w-full px-4 py-3 flex items-center gap-3 hover:bg-gray-50 transition-colors text-left"
        onClick={() => setOpen(o => !o)}
      >
        <div className="w-8 h-8 rounded-full bg-[#FF444F]/10 border border-[#FF444F]/30 flex items-center justify-center text-xs font-bold text-[#FF444F] shrink-0">
          {(emp.name || emp.email).slice(0, 2).toUpperCase()}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-semibold text-slate-900">{emp.name || emp.email}</span>
            {emp.job_title && (
              <span className="text-xs text-slate-400 truncate">{emp.job_title}</span>
            )}
          </div>
          <div className="flex items-center gap-2 mt-0.5">
            <span className="text-xs text-slate-500">{emp.department || "—"}</span>
            {emp.team_level2 && (
              <span className="text-[10px] text-slate-400">· {emp.team_level2}</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <span className="text-[10px] text-slate-400 font-medium">{totalSkills} skill{totalSkills !== 1 ? "s" : ""}</span>
          {open ? <ChevronDown className="w-4 h-4 text-slate-400" /> : <ChevronRight className="w-4 h-4 text-slate-400" />}
        </div>
      </button>

      {open && (
        <div className="px-4 pb-4 pt-0 border-t border-gray-100 space-y-3">
          <div className="text-[10px] text-slate-400 pt-2">{emp.email}</div>

          {emp.skills.tier1.length > 0 && (
            <div>
              <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">
                Tier 1 — Automatic ({emp.skills.tier1.length})
              </div>
              <div className="flex flex-wrap gap-1.5">
                {emp.skills.tier1.map(slug => (
                  <SkillBadge key={slug} slug={slug} meta={meta} variant="tier1" />
                ))}
              </div>
            </div>
          )}

          {emp.skills.granted.length > 0 && (
            <div>
              <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">
                Granted — Cross-dept ({emp.skills.granted.length})
              </div>
              <div className="flex flex-wrap gap-1.5">
                {emp.skills.granted.map(g => (
                  <SkillBadge key={g.skill_slug} slug={g.skill_slug} meta={meta} variant="granted" expiry={g.expires_at} />
                ))}
              </div>
            </div>
          )}

          {emp.skills.pending.length > 0 && (
            <div>
              <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-1.5">
                Pending Requests ({emp.skills.pending.length})
              </div>
              <div className="flex flex-wrap gap-1.5">
                {emp.skills.pending.map(slug => (
                  <SkillBadge key={slug} slug={slug} meta={meta} variant="pending" />
                ))}
              </div>
            </div>
          )}

          {totalSkills === 0 && (
            <div className="text-xs text-slate-400 italic">No skill access configured.</div>
          )}
        </div>
      )}
    </div>
  );
}

// ── Users tab ─────────────────────────────────────────────────────────────────

function UsersTab({ data }: { data: DirectoryData }) {
  const [search, setSearch] = useState("");
  const [deptFilter, setDeptFilter] = useState("");

  const departments = useMemo(() => {
    const s = new Set(data.employees.map(e => e.department).filter(Boolean));
    return Array.from(s).sort();
  }, [data.employees]);

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return data.employees.filter(e => {
      if (deptFilter && e.department !== deptFilter) return false;
      if (!q) return true;
      return (
        e.name.toLowerCase().includes(q) ||
        e.email.toLowerCase().includes(q) ||
        e.department.toLowerCase().includes(q) ||
        e.job_title.toLowerCase().includes(q)
      );
    });
  }, [data.employees, search, deptFilter]);

  return (
    <div className="flex flex-col gap-4 h-full overflow-hidden">
      {/* Filters */}
      <div className="flex items-center gap-3 flex-wrap shrink-0">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#FF444F]/30 focus:border-[#FF444F]"
            placeholder="Search by name, email, department, title…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
          {search && (
            <button className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600" onClick={() => setSearch("")}>
              <X className="w-3.5 h-3.5" />
            </button>
          )}
        </div>
        <select
          className="px-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#FF444F]/30 focus:border-[#FF444F] bg-white"
          value={deptFilter}
          onChange={e => setDeptFilter(e.target.value)}
        >
          <option value="">All Departments</option>
          {departments.map(d => <option key={d} value={d}>{d}</option>)}
        </select>
        <span className="text-xs text-slate-400 shrink-0">
          {filtered.length} of {data.total} employees
        </span>
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto space-y-2 pr-1">
        {filtered.length === 0 ? (
          <div className="text-sm text-slate-400 text-center py-12">No employees match the current filters.</div>
        ) : (
          filtered.map(emp => (
            <UserRow key={emp.email} emp={emp} meta={data.skill_meta} />
          ))
        )}
      </div>
    </div>
  );
}

// ── Departments tab ───────────────────────────────────────────────────────────

function DepartmentsTab({ data }: { data: DirectoryData }) {
  const [selectedDept, setSelectedDept] = useState<string | null>(null);
  const [search, setSearch] = useState("");

  // Build department summary
  const deptSummary = useMemo(() => {
    const map = new Map<string, {
      employees: Employee[];
      ownedSlugs: string[];
    }>();

    for (const emp of data.employees) {
      const dept = emp.department || "Unknown";
      if (!map.has(dept)) {
        map.set(dept, { employees: [], ownedSlugs: [] });
      }
      map.get(dept)!.employees.push(emp);
    }

    // Mark owned skills (dept is in skill's departments list)
    for (const [slug, meta] of Object.entries(data.skill_meta)) {
      if (meta.is_universal) continue;
      for (const d of meta.departments) {
        if (map.has(d)) {
          map.get(d)!.ownedSlugs.push(slug);
        }
      }
    }

    return Array.from(map.entries())
      .map(([dept, info]) => ({ dept, ...info }))
      .sort((a, b) => b.employees.length - a.employees.length);
  }, [data]);

  const filteredDepts = useMemo(() => {
    const q = search.toLowerCase();
    return q ? deptSummary.filter(d => d.dept.toLowerCase().includes(q)) : deptSummary;
  }, [deptSummary, search]);

  const selected = selectedDept ? deptSummary.find(d => d.dept === selectedDept) : null;

  // Cross-dept access INTO selected dept's skills
  const inboundGrants = useMemo(() => {
    if (!selected) return [];
    return data.employees.filter(emp =>
      emp.department !== selected.dept &&
      emp.skills.granted.some(g => selected.ownedSlugs.includes(g.skill_slug))
    );
  }, [selected, data.employees]);

  // Members of selected dept with grants OUT to other depts
  const outboundGrants = useMemo(() => {
    if (!selected) return [];
    return selected.employees.filter(emp => emp.skills.granted.length > 0);
  }, [selected]);

  return (
    <div className="flex gap-4 h-full overflow-hidden">
      {/* Left — dept list */}
      <div className="w-64 shrink-0 flex flex-col gap-3 overflow-hidden">
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
          <input
            className="w-full pl-8 pr-3 py-2 text-sm border border-gray-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-[#FF444F]/30 focus:border-[#FF444F]"
            placeholder="Filter departments…"
            value={search}
            onChange={e => setSearch(e.target.value)}
          />
        </div>
        <div className="flex-1 overflow-y-auto space-y-1 pr-1">
          {filteredDepts.map(({ dept, employees, ownedSlugs }) => (
            <button
              key={dept}
              onClick={() => setSelectedDept(dept === selectedDept ? null : dept)}
              className={`w-full text-left px-3 py-2.5 rounded-lg transition-colors border ${
                selectedDept === dept
                  ? "bg-[#FF444F] text-white border-[#FF444F]"
                  : "bg-white border-gray-200 hover:bg-gray-50 text-slate-700"
              }`}
            >
              <div className="text-sm font-semibold truncate">{dept}</div>
              <div className={`text-[10px] mt-0.5 flex items-center gap-2 ${selectedDept === dept ? "text-white/70" : "text-slate-400"}`}>
                <span>{employees.length} member{employees.length !== 1 ? "s" : ""}</span>
                {ownedSlugs.length > 0 && <span>· {ownedSlugs.length} skill{ownedSlugs.length !== 1 ? "s" : ""}</span>}
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Right — detail panel */}
      <div className="flex-1 overflow-y-auto">
        {!selected ? (
          <div className="flex flex-col items-center justify-center h-full text-slate-400 gap-2">
            <Building2 className="w-10 h-10 opacity-30" />
            <span className="text-sm">Select a department to view details</span>
          </div>
        ) : (
          <div className="space-y-5">
            {/* Header */}
            <div className="bg-white rounded-xl border border-gray-200 p-5">
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-bold text-slate-900">{selected.dept}</h2>
                  <p className="text-sm text-slate-500 mt-0.5">
                    {selected.employees.length} active employee{selected.employees.length !== 1 ? "s" : ""}
                    {selected.ownedSlugs.length > 0 && ` · owns ${selected.ownedSlugs.length} skill${selected.ownedSlugs.length !== 1 ? "s" : ""}`}
                  </p>
                </div>
              </div>

              {/* Owned skills */}
              {selected.ownedSlugs.length > 0 && (
                <div className="mt-4">
                  <div className="text-[10px] font-bold text-slate-500 uppercase tracking-wide mb-2">Skills Owned By This Department</div>
                  <div className="flex flex-wrap gap-1.5">
                    {selected.ownedSlugs.map(slug => {
                      const risk = data.skill_meta[slug]?.risk_level ?? "standard";
                      return (
                        <span key={slug} className={`inline-flex items-center text-[10px] font-semibold px-2 py-0.5 rounded-full border ${RISK_COLORS[risk]}`}>
                          {skillDisplayName(slug, data.skill_meta)}
                          <span className="ml-1 opacity-60">{risk}</span>
                        </span>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>

            {/* Members */}
            <section className="space-y-2">
              <div className="flex items-center gap-2">
                <Users className="w-4 h-4 text-slate-400" />
                <span className="text-sm font-bold text-slate-900">Members</span>
                <span className="text-xs bg-slate-100 text-slate-500 rounded-full px-2 py-0.5">{selected.employees.length}</span>
              </div>
              {selected.employees.slice(0, 50).map(emp => (
                <UserRow key={emp.email} emp={emp} meta={data.skill_meta} />
              ))}
              {selected.employees.length > 50 && (
                <p className="text-xs text-slate-400 text-center py-2">Showing first 50 of {selected.employees.length} members.</p>
              )}
            </section>

            {/* Cross-dept access IN */}
            {inboundGrants.length > 0 && (
              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <Shield className="w-4 h-4 text-blue-500" />
                  <span className="text-sm font-bold text-slate-900">External Access — Into This Dept's Skills</span>
                  <span className="text-xs bg-blue-50 text-blue-600 border border-blue-200 rounded-full px-2 py-0.5">{inboundGrants.length}</span>
                </div>
                <p className="text-xs text-slate-400">Employees from other departments with approved grants to skills owned here.</p>
                <div className="space-y-2">
                  {inboundGrants.map(emp => (
                    <div key={emp.email} className="bg-white border border-blue-100 rounded-xl px-4 py-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-slate-900">{emp.name || emp.email}</span>
                        <span className="text-xs text-slate-400">from <strong>{emp.department}</strong></span>
                      </div>
                      <div className="flex flex-wrap gap-1.5 mt-1.5">
                        {emp.skills.granted
                          .filter(g => selected.ownedSlugs.includes(g.skill_slug))
                          .map(g => (
                            <SkillBadge key={g.skill_slug} slug={g.skill_slug} meta={data.skill_meta} variant="granted" expiry={g.expires_at} />
                          ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {/* Cross-dept grants OUT */}
            {outboundGrants.length > 0 && (
              <section className="space-y-2">
                <div className="flex items-center gap-2">
                  <Zap className="w-4 h-4 text-amber-500" />
                  <span className="text-sm font-bold text-slate-900">Members With Cross-Dept Access</span>
                  <span className="text-xs bg-amber-50 text-amber-600 border border-amber-200 rounded-full px-2 py-0.5">{outboundGrants.length}</span>
                </div>
                <p className="text-xs text-slate-400">Employees in this department who have been granted access to skills from other departments.</p>
                <div className="space-y-2">
                  {outboundGrants.map(emp => (
                    <div key={emp.email} className="bg-white border border-amber-100 rounded-xl px-4 py-3">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold text-slate-900">{emp.name || emp.email}</span>
                        <span className="text-xs text-slate-400">{emp.job_title}</span>
                      </div>
                      <div className="flex flex-wrap gap-1.5 mt-1.5">
                        {emp.skills.granted.map(g => (
                          <SkillBadge key={g.skill_slug} slug={g.skill_slug} meta={data.skill_meta} variant="granted" expiry={g.expires_at} />
                        ))}
                      </div>
                    </div>
                  ))}
                </div>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

type Tab = "users" | "departments";

export default function AdminDirectoryPage() {
  const [tab, setTab] = useState<Tab>("users");
  const [data, setData] = useState<DirectoryData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/admin/directory");
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail ?? `HTTP ${res.status}`);
      }
      const json = await res.json() as DirectoryData;
      setData(json);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Failed to load directory.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-6 py-3 bg-white border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-2">
          <BookUser className="w-4 h-4 text-[#FF444F]" />
          <span className="text-sm font-semibold text-slate-700">
            Admin <span className="text-slate-400 font-normal">— User Directory</span>
          </span>
          {data && (
            <span className="ml-1 text-xs text-slate-400 bg-slate-100 rounded-full px-2 py-0.5">
              {data.total} employees
            </span>
          )}
        </div>
        <button
          onClick={load}
          disabled={loading}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-700 border border-slate-200 px-3 py-1.5 rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 px-6 py-2 bg-white border-b border-slate-100 shrink-0">
        {([
          { id: "users", label: "Users", icon: Users },
          { id: "departments", label: "Departments", icon: Building2 },
        ] as { id: Tab; label: string; icon: typeof Users }[]).map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setTab(id)}
            className={`flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg transition-colors ${
              tab === id
                ? "bg-[#FF444F] text-white"
                : "text-slate-500 hover:text-slate-700 hover:bg-slate-100"
            }`}
          >
            <Icon className="w-3.5 h-3.5" />
            {label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-hidden px-6 py-4">
        {loading && (
          <div className="flex flex-col items-center justify-center h-full gap-3 text-slate-400">
            <Loader2 className="w-6 h-6 animate-spin text-[#FF444F]" />
            <span className="text-sm">Loading employee directory…</span>
          </div>
        )}

        {error && !loading && (
          <div className="flex flex-col items-center justify-center h-full gap-3">
            <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-5 py-4 max-w-md text-center">
              <p className="font-semibold mb-1">Failed to load directory</p>
              <p className="text-xs opacity-80">{error}</p>
            </div>
            <button onClick={load} className="text-xs text-[#FF444F] hover:underline">Try again</button>
          </div>
        )}

        {data && !loading && (
          tab === "users"
            ? <UsersTab data={data} />
            : <DepartmentsTab data={data} />
        )}
      </div>
    </div>
  );
}
