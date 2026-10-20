import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { Search, Star, ShieldCheck, TerminalSquare, LayoutGrid, List, Box, Lock, CheckCircle2, Clock, AlertCircle, Building2, KeyRound } from "lucide-react";
import { useSkillList } from "@/lib/SkillAccessContext";
import { apiFetch } from "@/lib/api";
import { useSkillAccess, type SkillAccessInfo } from "@/lib/SkillAccessContext";
import { SystemCriticalityBadge } from "@/components/SystemCriticalityBadge";

// ─── Registry constants ──────────────────────────────────────────────────────

const SORT_OPTIONS = [
  { id: "rating", label: "Highest rated" },
  { id: "newest", label: "Recently added" },
  { id: "name", label: "Name" },
];

function skillRating(skill: { reviews: { rating: number }[] }): number {
  if (!skill.reviews.length) return 0;
  return skill.reviews.reduce((sum, r) => sum + r.rating, 0) / skill.reviews.length;
}

const SCOPES = [
  { id: "all", label: "All" },
  { id: "internal", label: "Internal" },
  { id: "utility", label: "Utility" },
];

const CATEGORIES = [
  { id: "all", label: "All" },
  { id: "data", label: "Data & Analytics" },
  { id: "trading", label: "Markets & Operations" },
  { id: "platform", label: "Platform Engineering" },
  { id: "marketing", label: "Marketing & Growth" },
  { id: "finance", label: "Finance & Partners" },
  { id: "design", label: "Design & Comms" },
];

// Tags that place a skill in a category. Extend these lists for your own
// catalogue — a skill matching none of them simply lands in "All".
const categoryTagMap: Record<string, string[]> = {
  data: ["sql", "warehouse", "analytics", "database", "query", "reporting", "bigquery", "snowflake"],
  trading: ["trading", "markets", "orders", "positions", "margin", "risk"],
  platform: ["meta", "behavior", "standards", "workflow", "automation", "github", "api", "connectors", "data-pipeline", "engineering", "specs"],
  marketing: ["marketing", "ads", "campaigns", "crm", "email", "acquisition", "growth", "analytics"],
  finance: ["finance", "accounting", "gl", "payroll", "payments", "invoices", "partners", "commissions"],
  design: ["brand", "design", "typography", "ux", "writing", "copy", "announcements", "image", "comms", "pdf", "documents", "reporting", "docx"],
};

// ─── Registry tab ─────────────────────────────────────────────────────────────

function RegistryTab() {
  const { bySlug: accessBySlug, loading: accessLoading } = useSkillAccess();
  const allSkills = useSkillList();
  const [searchQuery, setSearchQuery] = useState("");
  const [sortMethod, setSortMethod] = useState("rating");
  const [selectedScope, setSelectedScope] = useState("all");
  const [selectedCategory, setSelectedCategory] = useState("all");
  const [viewMode, setViewMode] = useState<"list" | "grid">("list");
  const [hideUnverified, setHideUnverified] = useState(false);
  const [mySkillsOnly, setMySkillsOnly] = useState(false);
  const [hasSecretsOnly, setHasSecretsOnly] = useState(false);

  const filteredSkills = allSkills.filter((skill) => {
    const matchesSearch =
      skill.name.toLowerCase().includes(searchQuery.toLowerCase()) ||
      skill.description.toLowerCase().includes(searchQuery.toLowerCase()) ||
      skill.tags.some((tag) => tag.toLowerCase().includes(searchQuery.toLowerCase()));
    const matchesScope = selectedScope === "all" || skill.scope === selectedScope;
    let matchesCategory = true;
    if (selectedCategory !== "all") {
      const allowed = categoryTagMap[selectedCategory] ?? [];
      matchesCategory = skill.tags.some((t) => allowed.includes(t.toLowerCase()));
    }
    const matchesVerified = hideUnverified ? skill.verified : true;
    const matchesMySkills = mySkillsOnly ? (accessBySlug.get(skill.slug)?.accessible ?? false) : true;
    const matchesSecrets = hasSecretsOnly ? (accessBySlug.get(skill.slug)?.has_secrets ?? false) : true;
    return matchesSearch && matchesScope && matchesCategory && matchesVerified && matchesMySkills && matchesSecrets;
  }).sort((a, b) => {
    if (sortMethod === "rating") return skillRating(b) - skillRating(a);
    if (sortMethod === "name") return a.name.localeCompare(b.name);
    if (sortMethod === "newest") return b.id.localeCompare(a.id);
    return 0;
  });

  return (
    <div className="flex flex-1 min-h-0 items-start">
      {/* Sidebar */}
      <aside className="w-52 shrink-0 flex flex-col gap-6 py-6 px-3 border-r border-slate-200 self-start sticky top-0">
        <section>
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 px-3">Sort By</h3>
          <nav className="space-y-0.5">
            {SORT_OPTIONS.map((opt) => (
              <button key={opt.id} onClick={() => setSortMethod(opt.id)}
                className={`w-full flex items-center px-3 py-2 text-sm font-medium rounded-md transition-colors ${sortMethod === opt.id ? "bg-slate-100 text-slate-900" : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"}`}>
                {opt.label}
              </button>
            ))}
          </nav>
        </section>
        <section>
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 px-3">Scope</h3>
          <nav className="space-y-0.5">
            {SCOPES.map((s) => (
              <button key={s.id} onClick={() => setSelectedScope(s.id)}
                className={`w-full flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-md transition-colors ${selectedScope === s.id ? "text-slate-900" : "text-slate-600 hover:text-slate-900"}`}>
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                  selectedScope === s.id
                    ? s.id === "internal" ? "bg-blue-500" : s.id === "utility" ? "bg-violet-500" : "bg-slate-900"
                    : "bg-transparent border border-slate-300"
                }`} />
                {s.label}
              </button>
            ))}
          </nav>
        </section>
        <section>
          <h3 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest mb-2 px-3">Categories</h3>
          <nav className="space-y-0.5">
            {CATEGORIES.map((cat) => (
              <button key={cat.id} onClick={() => setSelectedCategory(cat.id)}
                className={`w-full flex items-center gap-2 px-3 py-2 text-sm font-medium rounded-md transition-colors ${selectedCategory === cat.id ? "text-slate-900" : "text-slate-600 hover:text-slate-900"}`}>
                <span className={`w-1.5 h-1.5 rounded-full ${selectedCategory === cat.id ? "bg-slate-900" : "bg-transparent"}`} />
                {cat.label}
              </button>
            ))}
          </nav>
        </section>
      </aside>

      {/* Main */}
      <main className="flex-1 min-w-0 flex flex-col px-8 py-6">
        <div className="flex justify-between items-end mb-5">
          <h1 className="text-2xl font-bold tracking-tight text-slate-900">
            Skills <span className="text-sm font-medium text-slate-400 ml-2">{allSkills.length}</span>
          </h1>
        </div>

        <div className="relative mb-5">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
          <input type="text" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search skills..."
            className="w-full pl-10 pr-4 py-2.5 bg-white border border-slate-200 rounded-md text-sm text-slate-900 placeholder:text-slate-400 focus:outline-none focus:ring-1 focus:ring-slate-900 transition-all font-medium" />
        </div>

        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 mb-4 pb-4 border-b border-slate-200 text-sm">
          <div className="text-slate-500 font-medium">{filteredSkills.length} results</div>
          <div className="flex items-center gap-5">
            <label className="flex items-center gap-2 text-slate-600 cursor-pointer hover:text-slate-900 transition-colors">
              <input type="checkbox" checked={mySkillsOnly} onChange={(e) => setMySkillsOnly(e.target.checked)}
                className="rounded border-slate-300 text-slate-900 focus:ring-slate-900" />
              <span className="font-medium text-xs flex items-center gap-1">
                <CheckCircle2 className="w-3 h-3 text-emerald-500" /> My Skills
              </span>
            </label>
            <label className="flex items-center gap-2 text-slate-600 cursor-pointer hover:text-slate-900 transition-colors">
              <input type="checkbox" checked={hideUnverified} onChange={(e) => setHideUnverified(e.target.checked)}
                className="rounded border-slate-300 text-slate-900 focus:ring-slate-900" />
              <span className="font-medium text-xs">Verified only</span>
            </label>
            <label className="flex items-center gap-2 text-slate-600 cursor-pointer hover:text-slate-900 transition-colors">
              <input type="checkbox" checked={hasSecretsOnly} onChange={(e) => setHasSecretsOnly(e.target.checked)}
                className="rounded border-slate-300 text-slate-900 focus:ring-slate-900" />
              <span className="font-medium text-xs flex items-center gap-1">
                <KeyRound className="w-3 h-3 text-violet-500" /> Has Secrets
              </span>
            </label>
            <div className="flex bg-slate-100 rounded-md p-1 border border-slate-200">
              <button onClick={() => setViewMode("list")}
                className={`p-1.5 rounded transition-all ${viewMode === "list" ? "bg-white shadow-sm text-slate-900" : "text-slate-500 hover:text-slate-900"}`}>
                <List className="w-4 h-4" />
              </button>
              <button onClick={() => setViewMode("grid")}
                className={`p-1.5 rounded transition-all ${viewMode === "grid" ? "bg-white shadow-sm text-slate-900" : "text-slate-500 hover:text-slate-900"}`}>
                <LayoutGrid className="w-4 h-4" />
              </button>
            </div>
          </div>
        </div>

        {filteredSkills.length === 0 ? (
          <div className="text-center py-24 bg-slate-50 rounded-xl border border-slate-200 border-dashed mt-4">
            <TerminalSquare className="w-12 h-12 text-slate-300 mx-auto mb-4" />
            <h3 className="font-bold text-slate-900">No skills found</h3>
            <p className="text-slate-500 mt-1 text-sm">Try a different search term or remove filters.</p>
          </div>
        ) : (
          <div className={viewMode === "list" ? "flex flex-col" : "grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4"}>
            {filteredSkills.map((skill) => (
              <Link key={skill.id} to={`/skills/${skill.slug}`}
                className={`group block transition-colors duration-200 hover:bg-slate-50 ${viewMode === "list" ? "py-4 border-b border-slate-100 last:border-0" : "p-5 border border-slate-200 rounded-xl bg-white hover:border-slate-400 flex flex-col h-full"}`}>
                {viewMode === "list" ? (
                  <div className="flex items-start gap-4">
                    <div className="w-10 h-10 rounded bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0 mt-1">
                      <Box className="w-5 h-5 text-slate-600" />
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-xs font-mono text-slate-500">@{skill.publisher}</span>
                        <span className="text-slate-300">/</span>
                        <h3 className="text-base font-bold text-slate-900 truncate group-hover:text-blue-600 transition-colors">{skill.name}</h3>
                        {skill.verified && <ShieldCheck className="w-3.5 h-3.5 text-blue-500 shrink-0" />}
                        <AccessBadge access={accessBySlug.get(skill.slug)} />
                        <SystemCriticalityBadge riskLevel={skill.maxSystemRiskLevel} />
                        {skill.scope === "utility" && (
                          <span className="text-[9px] font-bold px-1.5 py-0.5 rounded bg-violet-50 text-violet-600 border border-violet-100 shrink-0">UTILITY</span>
                        )}
                      </div>
                      <p className="text-sm text-slate-600 mb-3 line-clamp-2 pr-4">{skill.description}</p>
                      <div className="flex flex-wrap items-center gap-4 text-xs font-mono text-slate-400">
                        {skill.reviews.length > 0 ? (
                          <div className="flex items-center gap-1.5 text-amber-600">
                            <Star className="w-3.5 h-3.5 fill-current" />
                            <span>{skillRating(skill).toFixed(1)}</span>
                            <span className="text-slate-400">({skill.reviews.length})</span>
                          </div>
                        ) : (
                          <span className="text-slate-300">No reviews yet</span>
                        )}
                      </div>
                    </div>
                  </div>
                ) : (
                  <>
                    <div className="flex items-start gap-3 mb-3">
                      <div className="w-8 h-8 rounded bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0">
                        <Box className="w-4 h-4 text-slate-600" />
                      </div>
                      <div className="flex-1 min-w-0 pb-1 border-b border-slate-100">
                        <div className="text-[10px] font-mono text-slate-500 mb-0.5 mt-0.5 truncate flex items-center gap-1">
                          @{skill.publisher}
                          {skill.verified && <ShieldCheck className="w-3 h-3 text-blue-500 shrink-0" />}
                        </div>
                        <h3 className="text-sm font-bold text-slate-900 truncate group-hover:text-blue-600 transition-colors">{skill.name}</h3>
                      </div>
                      <SystemCriticalityBadge riskLevel={skill.maxSystemRiskLevel} />
                    </div>
                    <p className="text-xs text-slate-600 mb-4 line-clamp-3 leading-relaxed flex-1">{skill.description}</p>
                    <div className="flex items-center justify-between mt-auto">
                      <div className="flex gap-1.5">
                        {skill.tags.slice(0, 2).map((tag) => (
                          <span key={tag} className="text-[9px] px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 font-medium uppercase tracking-wider">{tag}</span>
                        ))}
                      </div>
                      <div className="flex items-center gap-3 text-[10px] font-mono text-slate-400">
                        {skill.reviews.length > 0 ? (
                          <div className="flex items-center gap-1 text-amber-600">
                            <Star className="w-3 h-3 fill-current" />
                            {skillRating(skill).toFixed(1)}
                            <span className="text-slate-400">({skill.reviews.length})</span>
                          </div>
                        ) : (
                          <span className="text-slate-300 text-[9px]">No reviews</span>
                        )}
                      </div>
                    </div>
                  </>
                )}
              </Link>
            ))}
          </div>
        )}
      </main>
    </div>
  );
}

// ─── Access badge (tiny inline indicator for Registry) ────────────────────────

function AccessBadge({ access }: { access?: SkillAccessInfo }) {
  if (!access) return null;
  if (access.tier1) return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-emerald-50 text-emerald-700 border border-emerald-200">
      <CheckCircle2 className="w-2.5 h-2.5" /> YOURS
    </span>
  );
  if (access.tier2) return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-violet-50 text-violet-700 border border-violet-200">
      <CheckCircle2 className="w-2.5 h-2.5" /> GRANTED
    </span>
  );
  if (access.pending_request_id) return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-amber-50 text-amber-700 border border-amber-200">
      <Clock className="w-2.5 h-2.5" /> PENDING
    </span>
  );
  if (!access.can_request) return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-red-50 text-red-600 border border-red-200">
      <Lock className="w-2.5 h-2.5" /> RESTRICTED
    </span>
  );
  return (
    <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[9px] font-bold bg-slate-100 text-slate-500 border border-slate-200">
      <Lock className="w-2.5 h-2.5" /> REQUEST
    </span>
  );
}

// ─── Access Request Modal ─────────────────────────────────────────────────────

function AccessRequestModal({
  slug,
  acl,
  onClose,
  onSubmitted,
}: {
  slug: string;
  acl: SkillAccessInfo | null;
  onClose: () => void;
  onSubmitted: () => void;
}) {
  const skillList = useSkillList();
  const [justification, setJustification] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const skill = skillList.find(s => s.slug === slug);
  const riskColors = {
    standard: "text-emerald-700 bg-emerald-50 border-emerald-200",
    elevated: "text-amber-700 bg-amber-50 border-amber-200",
    critical: "text-red-700 bg-red-50 border-red-200",
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (justification.trim().length < 20) {
      setError("Please provide at least 20 characters of justification.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch("/api/access/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_slug: slug, justification: justification.trim() }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail || `Request failed (HTTP ${res.status})`);
      }
      onSubmitted();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not submit request.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg border border-slate-200">
        <div className="px-6 pt-5 pb-4 border-b border-slate-100">
          <div className="flex items-start justify-between">
            <div>
              <h2 className="text-base font-bold text-slate-900">Request Access</h2>
              <p className="text-sm text-slate-500 mt-0.5">{skill?.name ?? slug}</p>
            </div>
            <button onClick={onClose} className="text-slate-400 hover:text-slate-700 transition-colors text-lg leading-none">✕</button>
          </div>
        </div>

        <form onSubmit={handleSubmit} className="px-6 py-4 space-y-4">
          {acl && (
            <div className="space-y-2 text-xs">
              {acl.owning_department && (
                <div className="flex items-center gap-2">
                  <Building2 className="w-3.5 h-3.5 text-slate-400" />
                  <span className="text-slate-600">Owned by <strong>{acl.owning_department}</strong></span>
                </div>
              )}
              {acl.integrations.length > 0 && (
                <div className="bg-slate-50 border border-slate-200 rounded-lg p-3">
                  <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">Integrations this skill accesses</div>
                  <div className="flex flex-wrap gap-1.5">
                    {acl.integrations.map(i => (
                      <span key={i} className="px-2 py-0.5 rounded bg-white border border-slate-200 text-slate-700 font-medium">{i}</span>
                    ))}
                  </div>
                </div>
              )}
              <div className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${riskColors[acl.risk_level]}`}>
                Risk: {acl.risk_level}
              </div>
            </div>
          )}

          <div>
            <label className="block text-xs font-bold text-slate-700 mb-1.5">
              Justification <span className="text-red-500">*</span>
            </label>
            <textarea
              required
              rows={5}
              value={justification}
              onChange={e => setJustification(e.target.value)}
              placeholder="Explain why you need access to this skill. What business task does it support? Include any relevant project or ticket reference."
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg resize-none focus:outline-none focus:border-slate-400 placeholder:text-slate-400"
              maxLength={2000}
            />
            <div className="text-[10px] text-slate-400 mt-1 text-right">{justification.length}/2000</div>
          </div>

          {error && (
            <div className="flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
              <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" /> {error}
            </div>
          )}

          <div className="flex gap-2 pt-1">
            <button type="button" onClick={onClose}
              className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors font-medium">
              Cancel
            </button>
            <button type="submit" disabled={submitting}
              className="flex-1 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-1.5">
              {submitting ? "Submitting…" : "Submit Request"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ─── (Dojo and Training tabs removed — Registry is the single view) ──────────


// ─── Main export ──────────────────────────────────────────────────────────────

export default function SkillsPage() {
  return (
    <div className="flex flex-col flex-1 h-full">
      <div className="flex items-center gap-1 px-6 pt-4 pb-0 border-b border-slate-200 bg-white shrink-0">
        <span className="px-4 py-2 text-sm font-semibold rounded-t-md border-b-2 border-slate-900 text-slate-900">
          Registry
        </span>
      </div>
      <RegistryTab />
    </div>
  );
}
