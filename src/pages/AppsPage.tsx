import { useEffect, useRef, useState, type FC } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  Box,
  Check,
  CheckCircle2,
  ExternalLink,
  FlaskConical,
  Globe,
  Loader2,
  Lock,
  Plus,
  Search,
  Sheet,
  Trash2,
  Users,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useSkillList } from "@/lib/SkillAccessContext";
import { timeAgo } from "@/lib/time";
import { trackEvent } from "@/src/lib/analytics";
import { useLabDraft } from "@/lib/useLabDraft";

interface AppRecord {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  creator_email: string;
  visibility: "private" | "shared" | "workspace";
  status: "draft" | "deployed" | "error" | "archived";
  tech_stack: "static" | "python";
  mode?: "standard" | "overnight";
  file_manifest: string[];
  skill_ids: string[];
  env_var_keys: string[];
  build_phase?: "building" | "completed" | "failed" | null;
  created_at: string;
  updated_at: string;
  is_mine: boolean;
}

type Tab = "shared" | "mine";

export default function AppsPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const [apps, setApps] = useState<AppRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showNew, setShowNew] = useState(false);
  const [tab, setTab] = useState<Tab>("shared");

  useEffect(() => {
    if ((location.state as { openNew?: boolean } | null)?.openNew) {
      setShowNew(true);
      trackEvent("lab.modal_opened", { source: "location_state" });
      navigate(location.pathname, { replace: true, state: {} });
    }
  }, [location.state]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch("/api/apps");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = (await res.json()) as AppRecord[];
      setApps(Array.isArray(data) ? data : []);
    } catch {
      setError("Couldn't load your apps. Please refresh the page.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const hasActiveBuild = apps.some(a => a.build_phase === "building");

  useEffect(() => {
    if (!hasActiveBuild) return;
    const t = setInterval(() => { void load(); }, 5000);
    return () => clearInterval(t);
  }, [hasActiveBuild]);

  const myApps = apps.filter(a => a.is_mine);
  // Only show live, deployed apps in Shared Apps — drafts/archived aren't useful to visit.
  const sharedApps = apps.filter(a => !a.is_mine && a.status === "deployed");
  const visibleApps = tab === "mine" ? myApps : sharedApps;

  const openApp = (app: AppRecord) => navigate(`/lab/${app.id}`);

  const handleDelete = async (app: AppRecord) => {
    if (!confirm(`Delete "${app.name}"? This cannot be undone.`)) return;
    try {
      await apiFetch(`/api/apps/${app.id}`, { method: "DELETE" });
      setApps(prev => prev.filter(a => a.id !== app.id));
    } catch {
      setError("Couldn't delete this app. Please try again.");
    }
  };

  if (loading) {
    return (
      <div className="flex-1 w-full flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <div className="flex-1 w-full h-full overflow-y-auto bg-[#F9FAFB]">
      <div className="max-w-5xl mx-auto px-6 py-6">
        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3 flex items-center gap-2">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex items-center mb-5">
          <div className="flex items-center bg-white border border-slate-200 rounded-lg p-0.5 gap-0.5">
            <button
              onClick={() => setTab("shared")}
              className={`flex items-center gap-1.5 text-xs px-3 py-1 rounded-md font-medium transition-colors ${
                tab === "shared"
                  ? "bg-slate-900 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              <Globe className="w-3.5 h-3.5" /> Shared Apps ({sharedApps.length})
            </button>
            <button
              onClick={() => setTab("mine")}
              className={`flex items-center gap-1.5 text-xs px-3 py-1 rounded-md font-medium transition-colors ${
                tab === "mine"
                  ? "bg-slate-900 text-white shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              <FlaskConical className="w-3.5 h-3.5" /> My Apps ({myApps.length})
            </button>
          </div>
        </div>

        {visibleApps.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-24 text-center">
            <FlaskConical className="w-12 h-12 text-slate-200 mb-4" />
            {tab === "mine" ? (
              <>
                <h2 className="text-base font-bold text-slate-900 mb-1">No apps yet</h2>
                <p className="text-sm text-slate-500 mb-6 max-w-xs leading-relaxed">
                  Build small web apps with AI. Describe what you want and the agent writes the code.
                </p>
                <button
                  onClick={() => { setShowNew(true); trackEvent("lab.modal_opened", { source: "empty_state" }); }}
                  className="flex items-center gap-2 bg-slate-900 text-white text-sm font-bold px-4 py-2 rounded-lg hover:bg-slate-700 transition-colors"
                >
                  <Plus className="w-4 h-4" /> New App
                </button>
              </>
            ) : (
              <>
                <h2 className="text-base font-bold text-slate-900 mb-1">Nothing here yet</h2>
                <p className="text-sm text-slate-500 max-w-xs leading-relaxed">
                  Live apps published to the whole workspace, or shared with you directly, will show up here.
                </p>
              </>
            )}
          </div>
        ) : (
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {visibleApps.map(app => (
              tab === "shared" ? (
                <SharedAppCard key={app.id} app={app} />
              ) : (
                <AppCard
                  key={app.id}
                  app={app}
                  onOpen={() => { openApp(app); }}
                  onDelete={() => { void handleDelete(app); }}
                />
              )
            ))}
          </div>
        )}
      </div>

      {showNew && (
        <NewAppModal
          onClose={() => setShowNew(false)}
          onCreate={(id) => navigate(`/lab/${id}`)}
        />
      )}
    </div>
  );
}

function StatusChip({ status }: { status: AppRecord["status"] }) {
  const map: Record<string, string> = {
    draft: "bg-slate-100 text-slate-500",
    deployed: "bg-emerald-50 text-emerald-700",
    error: "bg-red-50 text-red-600",
    archived: "bg-slate-100 text-slate-400",
  };
  return (
    <span className={`inline-flex items-center text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full ${map[status] ?? map.draft}`}>
      {status}
    </span>
  );
}

const SharedAppCard: FC<{ app: AppRecord }> = ({ app }) => {
  const visit = () => window.open(`/apps/${app.slug}/`, "_blank", "noopener,noreferrer");

  return (
    <div
      onClick={visit}
      className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3 cursor-pointer opacity-80 hover:opacity-100 hover:border-slate-300 hover:shadow-sm transition-all"
    >
      <div className="min-w-0">
        <h3 className="text-sm font-bold text-slate-900 truncate">{app.name}</h3>
        {app.description && (
          <p className="text-xs text-slate-500 mt-0.5 line-clamp-2 leading-relaxed">
            {app.description}
          </p>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        <StatusChip status={app.status} />
        {app.visibility === "workspace" ? (
          <span className="flex items-center gap-1 text-[10px] text-indigo-600 font-medium">
            <Globe className="w-3 h-3" /> Workspace
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-amber-600 font-medium">
            <Users className="w-3 h-3" /> Shared
          </span>
        )}
        <span className="text-[10px] text-slate-300 ml-auto">{timeAgo(app.updated_at)}</span>
      </div>

      <div className="pt-1 border-t border-slate-100">
        <button
          onClick={e => { e.stopPropagation(); visit(); }}
          className="w-full flex items-center justify-center gap-1.5 text-xs font-bold bg-slate-900 text-white px-3 py-1.5 rounded-lg hover:bg-slate-700 transition-colors"
        >
          Visit <ExternalLink className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
};

type AppCardProps = {
  app: AppRecord;
  onOpen: () => void | Promise<void>;
  onDelete: () => void | Promise<void>;
};

const AppCard: FC<AppCardProps> = ({ app, onOpen, onDelete }) => {
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 flex flex-col gap-3 opacity-80 hover:opacity-100 hover:border-slate-300 hover:shadow-sm transition-all">
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="text-sm font-bold text-slate-900 truncate">{app.name}</h3>
          {app.description && (
            <p className="text-xs text-slate-500 mt-0.5 line-clamp-2 leading-relaxed">
              {app.description}
            </p>
          )}
        </div>
        {app.is_mine && (
          <button
            onClick={e => { e.stopPropagation(); onDelete(); }}
            className="text-slate-300 hover:text-red-500 transition-colors p-0.5 shrink-0"
            title="Delete app"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        )}
      </div>

      <div className="flex items-center gap-2 flex-wrap">
        {!app.is_mine && (
          <span className="text-[10px] text-slate-400 truncate max-w-[9rem]" title={app.creator_email}>
            by {app.creator_email}
          </span>
        )}
        <StatusChip status={app.status} />
        {app.build_phase === "building" && (
          <span className="inline-flex items-center gap-1 text-[10px] font-semibold text-indigo-600 bg-indigo-50 border border-indigo-200 px-2 py-0.5 rounded-full animate-pulse">
            <Loader2 className="w-2.5 h-2.5 animate-spin" /> Building…
          </span>
        )}
        {app.visibility === "workspace" ? (
          <span className="flex items-center gap-1 text-[10px] text-indigo-600 font-medium">
            <Globe className="w-3 h-3" /> Workspace
          </span>
        ) : app.visibility === "shared" ? (
          <span className="flex items-center gap-1 text-[10px] text-amber-600 font-medium">
            <Users className="w-3 h-3" /> Shared
          </span>
        ) : (
          <span className="flex items-center gap-1 text-[10px] text-slate-400">
            <Lock className="w-3 h-3" /> Private
          </span>
        )}
        <span className="text-[10px] text-slate-300 ml-auto">{timeAgo(app.updated_at)}</span>
      </div>

      <div className="flex items-center gap-2 pt-1 border-t border-slate-100">
        <button
          onClick={onOpen}
          className="flex-1 text-xs font-bold bg-slate-900 text-white px-3 py-1.5 rounded-lg hover:bg-slate-700 transition-colors"
        >
          Open in Lab
        </button>
        {app.status === "deployed" && (
          <a
            href={`/apps/${app.slug}/`}
            target="_blank"
            rel="noopener noreferrer"
            className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 px-2 py-1.5 border border-slate-200 rounded-lg transition-colors"
            onClick={e => e.stopPropagation()}
          >
            <ExternalLink className="w-3.5 h-3.5" />
          </a>
        )}
      </div>
    </div>
  );
}

type AppMode = "gws" | "skills";

interface SheetValidation {
  name: string;
  columns: string[] | null;
  row_estimate: number | null;
}

function _apiError(body: Record<string, unknown>, status: number): string {
  const d = body?.detail;
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((e: { msg?: string }) => e.msg ?? JSON.stringify(e)).join("; ");
  return `HTTP ${status}`;
}

function NewAppModal({
  onClose,
  onCreate,
}: {
  onClose: () => void;
  onCreate: (id: string) => void;
}) {
  const [step, setStep] = useState<"pick" | "form">("pick");
  const [mode, setMode] = useState<AppMode>("skills");

  // Shared fields
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // GWS-specific
  const [gwsConnected, setGwsConnected] = useState<boolean | null>(null);
  const [sheetUrl, setSheetUrl] = useState("");
  const [validating, setValidating] = useState(false);
  const [sheetValidation, setSheetValidation] = useState<SheetValidation | null>(null);
  const [sheetError, setSheetError] = useState<string | null>(null);

  // Skills-specific
  const allSkills = useSkillList();
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [classifying, setClassifying] = useState(false);
  const [suggestedSlugs, setSuggestedSlugs] = useState<Set<string>>(new Set());
  const [skillSearch, setSkillSearch] = useState("");
  const classifyTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Server-backed auto-save draft. Restore on mount, debounced save on change.
  const draft = useLabDraft();
  const [draftHydrated, setDraftHydrated] = useState(false);

  draft.registerRestore((d) => {
    if (d.mode === "gws" || d.mode === "skills") setMode(d.mode);
    if (d.step === "pick" || d.step === "form") setStep(d.step);
    setName(d.name ?? "");
    setDescription(d.description ?? "");
    setSheetUrl(d.sheet_url ?? "");
    if (Array.isArray(d.skill_ids)) setSelectedSlugs(new Set(d.skill_ids));
  });

  useEffect(() => {
    if (!draftHydrated) return;
    draft.scheduleSave({
      step,
      mode,
      name,
      description,
      skill_ids: Array.from(selectedSlugs),
      sheet_url: sheetUrl,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [step, mode, name, description, selectedSlugs, sheetUrl]);

  useEffect(() => {
    if (draft.loaded && !draftHydrated) setDraftHydrated(true);
  }, [draft.loaded, draftHydrated]);

  // Load GWS connection status when entering GWS mode
  useEffect(() => {
    if (step !== "form" || mode !== "gws") return;
    apiFetch("/api/apps/gws-preflight")
      .then(r => r.json())
      .then((d: { connected: boolean }) => setGwsConnected(d.connected))
      .catch(() => setGwsConnected(false));
  }, [step, mode]);

  const validateSheet = async (url: string) => {
    setValidating(true);
    setSheetError(null);
    setSheetValidation(null);
    trackEvent("lab.validate_sheet.clicked");
    try {
      const res = await apiFetch("/api/apps/gws-validate-sheet", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url_or_id: url }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(_apiError(body, res.status));
      setSheetValidation(body as SheetValidation);
    } catch (err) {
      const msg = err instanceof Error ? err.message : "Could not validate sheet.";
      setSheetError(msg);
      trackEvent("lab.validate_sheet.failed", { error: msg });
    } finally {
      setValidating(false);
    }
  };

  const triggerClassify = (n: string, d: string) => {
    if (classifyTimerRef.current) clearTimeout(classifyTimerRef.current);
    if (!d.trim()) return;
    classifyTimerRef.current = setTimeout(async () => {
      setClassifying(true);
      try {
        const res = await apiFetch("/api/apps/classify-preview", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: n || "App", description: d }),
        });
        if (res.ok) {
          const data = (await res.json()) as { skill_ids: string[] };
          const suggested = new Set(data.skill_ids);
          setSuggestedSlugs(suggested);
          setSelectedSlugs(prev => {
            // Add newly suggested slugs; keep any the user already added manually
            const next = new Set(prev);
            suggested.forEach(s => next.add(s));
            return next;
          });
        }
      } catch { /* silent — user can still pick manually */ }
      finally { setClassifying(false); }
    }, 600);
  };

  const toggleSkill = (slug: string) => {
    setSelectedSlugs(prev => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug); else next.add(slug);
      return next;
    });
  };

  const submit = async () => {
    if (!name.trim()) { setError("Name is required."); return; }
    if (mode === "skills" && !description.trim()) { setError("Description is required."); return; }
    if (mode === "gws" && !sheetValidation) { setError("Please validate your Google Sheet first."); return; }

    setSubmitting(true);
    setError(null);
    try {
      const payload: Record<string, unknown> = { name: name.trim(), description: description.trim() };
      if (mode === "gws") {
        payload.skill_ids = [];
        payload.data_source_url = sheetUrl.trim();
      } else {
        payload.skill_ids = Array.from(selectedSlugs);
      }
      const res = await apiFetch("/api/apps", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(_apiError(body, res.status));
      }
      const app = await res.json();
      void draft.discardDraft(); // app created — drop the saved draft
      onCreate(app.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create app.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleClose = () => {
    if (step === "form") trackEvent("lab.app_create_abandoned", { mode });
    onClose();
  };

  const filteredSkills = allSkills.filter(s =>
    !skillSearch || s.name.toLowerCase().includes(skillSearch.toLowerCase()) ||
    s.description?.toLowerCase().includes(skillSearch.toLowerCase())
  );

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <div className="bg-white rounded-xl border border-slate-200 shadow-2xl w-full max-w-lg">

        {/* ── Step 1: mode selection ── */}
        {step === "pick" && (
          <div className="p-6 space-y-5">
            <div className="flex items-center justify-between">
              <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
                <FlaskConical className="w-4 h-4 text-[#FF444F]" />
                New app
              </h2>
              <button onClick={handleClose} className="text-slate-400 hover:text-slate-700 text-lg leading-none">×</button>
            </div>
            <p className="text-xs text-slate-500">Choose how you want to build your app.</p>
            <div className="grid grid-cols-2 gap-3">
              <button
                onClick={() => { setMode("gws"); setStep("form"); trackEvent("lab.mode_selected", { mode: "gws" }); }}
                className="flex flex-col items-start gap-2 p-4 border-2 border-slate-200 rounded-xl hover:border-[#FF444F] hover:bg-[#FF444F]/5 text-left transition-all group"
              >
                <div className="w-8 h-8 rounded-lg bg-emerald-50 border border-emerald-200 flex items-center justify-center">
                  <Sheet className="w-4 h-4 text-emerald-600" />
                </div>
                <div>
                  <p className="text-sm font-bold text-slate-900 group-hover:text-[#FF444F]">Google Sheet</p>
                  <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">Build a live data dashboard from a spreadsheet</p>
                </div>
              </button>
              <button
                onClick={() => { setMode("skills"); setStep("form"); trackEvent("lab.mode_selected", { mode: "skills" }); }}
                className="flex flex-col items-start gap-2 p-4 border-2 border-slate-200 rounded-xl hover:border-[#FF444F] hover:bg-[#FF444F]/5 text-left transition-all group"
              >
                <div className="w-8 h-8 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center">
                  <Box className="w-4 h-4 text-blue-600" />
                </div>
                <div>
                  <p className="text-sm font-bold text-slate-900 group-hover:text-[#FF444F]">Skills</p>
                  <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">Pick skills and let AI build from your description</p>
                </div>
              </button>
            </div>
          </div>
        )}

        {/* ── Step 2: form ── */}
        {step === "form" && (
          <div className="flex flex-col max-h-[85vh]">
            {/* Header */}
            <div className="px-6 pt-5 pb-4 border-b border-slate-100 shrink-0">
              <div className="flex items-center gap-2">
                <button onClick={() => { setStep("pick"); setSheetValidation(null); setSheetError(null); setError(null); }}
                  className="text-slate-400 hover:text-slate-700 transition-colors">
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
                  {mode === "gws"
                    ? <><Sheet className="w-4 h-4 text-emerald-600" /> Build with Google Sheet</>
                    : <><Box className="w-4 h-4 text-blue-600" /> Build with Skills</>}
                </h2>
                <button onClick={handleClose} className="ml-auto text-slate-400 hover:text-slate-700 text-lg leading-none">×</button>
              </div>
            </div>

            {/* Body — scrollable */}
            <div className="overflow-y-auto flex-1 px-6 py-4 space-y-4">
              {/* Name */}
              <div>
                <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Name</label>
                <input
                  autoFocus
                  type="text"
                  value={name}
                  onChange={e => setName(e.target.value)}
                  placeholder={mode === "gws" ? "PSP Balance Dashboard" : "Invoices Dashboard"}
                  className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
                />
              </div>

              {/* Description */}
              <div>
                <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
                  Description{mode === "gws" && <span className="text-slate-400 font-normal ml-1">(optional)</span>}
                </label>
                <textarea
                  value={description}
                  onChange={e => {
                    setDescription(e.target.value);
                    if (mode === "skills") triggerClassify(name, e.target.value);
                  }}
                  rows={3}
                  placeholder={mode === "gws"
                    ? "What should this dashboard show? The AI will use your sheet data to build it."
                    : "What should this app do? Include features, users, integrations, and data needs."}
                  className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 resize-none"
                />
                {mode === "skills" && (
                  <p className="text-[10px] mt-1 text-slate-400">The more detail you provide, the better the AI can suggest skills</p>
                )}
              </div>

              {/* ── GWS path ── */}
              {mode === "gws" && (
                <div className="space-y-3">
                  {gwsConnected === false && (
                    <div className="bg-amber-50 border border-amber-200 text-amber-800 text-xs rounded-lg px-3 py-2.5 flex items-start gap-2">
                      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      <span>Google Workspace not connected. <a href="/integrations" className="font-semibold underline hover:text-amber-900">Connect on Integrations</a> first.</span>
                    </div>
                  )}
                  {gwsConnected === true && (
                    <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                      Google Workspace connected
                    </div>
                  )}
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Google Sheet URL</label>
                    <div className="flex gap-2">
                      <input
                        type="url"
                        value={sheetUrl}
                        onChange={e => { setSheetUrl(e.target.value); setSheetValidation(null); setSheetError(null); }}
                        placeholder="https://docs.google.com/spreadsheets/d/..."
                        disabled={!gwsConnected || validating}
                        className="flex-1 text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:border-slate-400 disabled:opacity-60"
                      />
                      <button
                        onClick={() => validateSheet(sheetUrl)}
                        disabled={!gwsConnected || !sheetUrl.trim() || validating}
                        className="shrink-0 px-3 py-2 text-sm font-semibold text-white bg-[#FF444F] hover:bg-[#e63d48] rounded-lg disabled:opacity-50 transition-colors"
                      >
                        {validating ? <Loader2 className="w-4 h-4 animate-spin" /> : "Validate"}
                      </button>
                    </div>
                  </div>
                  {sheetError && (
                    <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg px-3 py-2 flex items-start gap-2">
                      <AlertCircle className="w-3.5 h-3.5 shrink-0 mt-0.5" />
                      <span>{sheetError}</span>
                    </div>
                  )}
                  {sheetValidation && (
                    <div className="bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2.5 space-y-1">
                      <div className="flex items-center gap-1.5 text-xs font-semibold text-emerald-700">
                        <CheckCircle2 className="w-3.5 h-3.5" />
                        {sheetValidation.name}
                        {sheetValidation.row_estimate != null && (
                          <span className="font-normal text-emerald-600">· ~{sheetValidation.row_estimate} rows</span>
                        )}
                      </div>
                      {(sheetValidation.columns?.length ?? 0) > 0 && (
                        <p className="text-[11px] text-emerald-600 truncate">
                          Columns: {sheetValidation.columns!.slice(0, 8).join(", ")}{sheetValidation.columns!.length > 8 ? "…" : ""}
                        </p>
                      )}
                    </div>
                  )}
                </div>
              )}

              {/* ── Skills path ── */}
              {mode === "skills" && (
                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
                      Skills
                      {selectedSlugs.size > 0 && (
                        <span className="ml-1.5 text-[#FF444F]">{selectedSlugs.size} selected</span>
                      )}
                    </label>
                    {classifying && (
                      <span className="text-[10px] text-slate-400 flex items-center gap-1">
                        <Loader2 className="w-3 h-3 animate-spin" /> Analysing…
                      </span>
                    )}
                  </div>
                  <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-1.5">
                    <Search className="w-3 h-3 text-slate-400 shrink-0" />
                    <input
                      type="text"
                      value={skillSearch}
                      onChange={e => setSkillSearch(e.target.value)}
                      placeholder="Filter skills…"
                      className="flex-1 bg-transparent text-xs text-slate-700 placeholder:text-slate-400 focus:outline-none"
                    />
                  </div>
                  <div className="grid grid-cols-2 gap-1.5 max-h-52 overflow-y-auto pr-1">
                    {filteredSkills.map(skill => {
                      const selected = selectedSlugs.has(skill.slug);
                      const suggested = suggestedSlugs.has(skill.slug);
                      return (
                        <button
                          key={skill.slug}
                          onClick={() => toggleSkill(skill.slug)}
                          className={`text-left p-2.5 rounded-lg border transition-all text-xs ${
                            selected
                              ? "border-[#FF444F] bg-[#FF444F]/5 text-slate-900"
                              : "border-slate-200 bg-white text-slate-600 hover:border-slate-400"
                          }`}
                        >
                          <div className="flex items-start justify-between gap-1 mb-0.5">
                            <span className="font-semibold truncate">{skill.name}</span>
                            {selected && <CheckCircle2 className="w-3 h-3 text-[#FF444F] shrink-0 mt-0.5" />}
                          </div>
                          {suggested && !selected && (
                            <span className="text-[9px] font-bold text-[#FF444F] uppercase tracking-wide">Suggested</span>
                          )}
                          <p className="text-[10px] text-slate-400 line-clamp-2 leading-relaxed mt-0.5">{skill.description}</p>
                        </button>
                      );
                    })}
                    {filteredSkills.length === 0 && (
                      <p className="col-span-2 text-xs text-slate-400 py-4 text-center">No skills match.</p>
                    )}
                  </div>
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-6 py-4 border-t border-slate-100 shrink-0 space-y-3">
              {error && (
                <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg px-3 py-2 flex items-center gap-2">
                  <AlertCircle className="w-3.5 h-3.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              <div className="flex gap-2 items-center">
                <button
                  onClick={onClose}
                  disabled={submitting}
                  className="px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
                >
                  Cancel
                </button>
                {draftHydrated && (
                  <span className="text-[11px] text-slate-400 flex items-center gap-1.5 flex-1 min-w-0">
                    {draft.saving ? (
                      <><Loader2 className="w-3 h-3 animate-spin shrink-0" /> Saving…</>
                    ) : draft.savedAt ? (
                      <><Check className="w-3 h-3 text-emerald-500 shrink-0" /> Draft saved</>
                    ) : (
                      "Draft auto-saves"
                    )}
                    {draft.savedAt && (
                      <button
                        type="button"
                        onClick={() => {
                          void draft.discardDraft();
                          setStep("pick");
                          setMode("skills");
                          setName("");
                          setDescription("");
                          setSheetUrl("");
                          setSheetValidation(null);
                          setSelectedSlugs(new Set());
                        }}
                        className="text-slate-400 hover:text-red-600 underline underline-offset-2 shrink-0"
                      >
                        discard
                      </button>
                    )}
                  </span>
                )}
                <button
                  onClick={submit}
                  disabled={
                    submitting || !name.trim() ||
                    (mode === "skills" && !description.trim()) ||
                    (mode === "gws" && (!sheetValidation || gwsConnected === false))
                  }
                  className="px-4 py-2 text-sm font-bold bg-[#FF444F] hover:bg-[#e63d48] text-white rounded-lg disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
                >
                  {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                  Create &amp; Open
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
