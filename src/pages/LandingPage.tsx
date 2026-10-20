import { useMemo, useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowRight,
  Bell,
  Bot,
  Clock,
  Database,
  FileText,
  Grid3x3,
  Key,
  Layers,
  Link2,
  Loader2,
  Lock,
  Rocket,
  Send,
  Sparkles,
  X,
} from "lucide-react";
import { useSkillAccess, useSkillList } from "@/lib/SkillAccessContext";
import { useBranding, withAppName } from "@/lib/branding";
import { apiFetch } from "@/lib/api";
import AutomationLingo from "@/src/components/AutomationLingo";
import { useTour } from "@/src/lib/tour/TourProvider";
import type { WorkspaceDraftState } from "@/src/lib/workspaceDraft";
import {
  buildAskSuggestionsFromSkills,
  buildForYouTemplates,
  computeSkillStats,
  firstName,
  getCategoryTemplates,
  greetingTime,
  HOME_TABS,
  isTechnicalDepartment,
  resolveTemplateAccess,
  type HomeIcon,
  type HomeTabId,
  type HomeTemplate,
  type Persona,
} from "@/src/data/homeTemplates";

interface HomeSummary {
  skills_available: number;
  skills_to_request: number;
  slack_agents_live: number;
  automations_active: number;
  agent_scheduled_tasks_active: number;
}

const ICONS: Record<HomeIcon, typeof Database> = {
  database: Database,
  bell: Bell,
  file: FileText,
  bot: Bot,
  clock: Clock,
  link: Link2,
  rocket: Rocket,
  grid: Grid3x3,
  layers: Layers,
  key: Key,
  sparkles: Sparkles,
};

function TemplateIcon({ icon }: { icon: HomeIcon }) {
  const Icon = ICONS[icon] ?? Sparkles;
  return (
    <div className="w-10 h-10 rounded-lg bg-[#FF444F]/10 border border-[#FF444F]/15 flex items-center justify-center shrink-0 text-[#FF444F]">
      <Icon className="w-5 h-5" />
    </div>
  );
}

function AccessBadge({ access }: { access: "available" | "locked" | "neutral" }) {
  if (access === "neutral") return null;
  if (access === "available") {
    return (
      <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[10px] font-bold bg-emerald-50 border border-emerald-200 text-emerald-700">
        Tier 1 · available now
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-bold bg-slate-50 border border-slate-200 text-slate-600">
      <Lock className="w-3 h-3" />
      Tier 2 · request access
    </span>
  );
}

function TemplateCard({
  template,
  access,
  onPreview,
}: {
  template: HomeTemplate;
  access: "available" | "locked" | "neutral";
  onPreview: (t: HomeTemplate) => void;
}) {
  return (
    <div className="flex flex-col h-full p-5 border border-slate-200 rounded-xl bg-white hover:border-slate-300 hover:shadow-sm transition-all">
      <div className="flex items-start gap-3 mb-3">
        <TemplateIcon icon={template.icon} />
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-bold text-slate-900 leading-snug">{template.title}</h3>
        </div>
      </div>
      <p className="text-xs text-slate-500 leading-relaxed flex-1 mb-4 line-clamp-2">{template.description}</p>
      <div className="mb-4">
        <AccessBadge access={access} />
      </div>
      <button
        type="button"
        onClick={() => onPreview(template)}
        className="mt-auto inline-flex items-center justify-center gap-1.5 px-3 py-2 rounded-lg bg-[#FF444F] hover:bg-[#e63d48] text-white text-xs font-bold transition-colors"
      >
        Preview
      </button>
    </div>
  );
}

function PreviewModal({
  template,
  access,
  onClose,
  onStart,
}: {
  template: HomeTemplate;
  access: "available" | "locked" | "neutral";
  onClose: () => void;
  onStart: (t: HomeTemplate) => void;
}) {
  const primaryLabel =
    access === "locked"
      ? "Request access"
      : template.workspacePrompt
        ? "Open in Workspace"
        : "Get started";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="home-preview-title"
        className="w-full max-w-lg bg-white rounded-2xl shadow-xl border border-slate-200 overflow-hidden"
      >
        <div className="flex items-start justify-between gap-4 p-6 border-b border-slate-100">
          <div className="flex items-start gap-3 min-w-0">
            <TemplateIcon icon={template.icon} />
            <div className="min-w-0">
              <p className="text-[10px] font-bold uppercase tracking-widest text-[#FF444F] mb-1">
                Starting point
              </p>
              <h2 id="home-preview-title" className="text-lg font-bold text-slate-900 leading-snug">
                {template.title}
              </h2>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-100 transition-colors shrink-0"
            aria-label="Close"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="p-6 space-y-5">
          <p className="text-sm text-slate-600 leading-relaxed">{template.long}</p>
          <div>
            <p className="text-xs font-bold text-slate-900 uppercase tracking-wide mb-3">Steps</p>
            <ol className="space-y-2.5">
              {template.steps.map((step, i) => (
                <li key={step} className="flex items-start gap-3 text-sm text-slate-600">
                  <span className="w-5 h-5 rounded-full bg-slate-100 text-slate-700 text-[10px] font-bold flex items-center justify-center shrink-0 mt-0.5">
                    {i + 1}
                  </span>
                  {step}
                </li>
              ))}
            </ol>
          </div>
          <AccessBadge access={access} />
        </div>

        <div className="flex gap-3 p-6 pt-0">
          <button
            type="button"
            onClick={onClose}
            className="flex-1 px-4 py-2.5 rounded-xl border border-slate-200 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors"
          >
            Not now
          </button>
          <button
            type="button"
            onClick={() => onStart(template)}
            className="flex-1 inline-flex items-center justify-center gap-2 px-4 py-2.5 rounded-xl bg-[#FF444F] hover:bg-[#e63d48] text-white text-sm font-bold transition-colors"
          >
            {primaryLabel}
            <ArrowRight className="w-4 h-4" />
          </button>
        </div>
      </div>
    </div>
  );
}

export default function LandingPage() {
  const navigate = useNavigate();
  const branding = useBranding();
  const { loading, employee, bySlug } = useSkillAccess();
  const { maybeAutoStartTour, sessionReady } = useTour();
  const skillList = useSkillList();
  const [activeTab, setActiveTab] = useState<HomeTabId>("for-you");
  const [askText, setAskText] = useState("");
  const [preview, setPreview] = useState<HomeTemplate | null>(null);
  const [summary, setSummary] = useState<HomeSummary | null>(null);

  const department = employee?.department ?? null;
  const persona: Persona = isTechnicalDepartment(department) ? "technical" : "non-technical";
  const deptBucketLabel = department ?? "your team";

  const clientSkillStats = useMemo(() => computeSkillStats(bySlug), [bySlug]);

  useEffect(() => {
    if (loading) return;
    let cancelled = false;
    apiFetch("/api/home/summary")
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
      .then((data: HomeSummary) => {
        if (!cancelled) setSummary(data);
      })
      .catch(() => {
        if (!cancelled) setSummary(null);
      });
    return () => {
      cancelled = true;
    };
  }, [loading]);

  useEffect(() => {
    if (loading || !sessionReady) return;
    maybeAutoStartTour();
  }, [loading, sessionReady, maybeAutoStartTour]);

  const skillsAvailable = summary?.skills_available ?? clientSkillStats.available;
  const skillsToRequest = summary?.skills_to_request ?? clientSkillStats.toRequest;

  const skillNames = useMemo(
    () => new Map(skillList.map((s) => [s.slug, s.name])),
    [skillList],
  );

  const templates = useMemo(() => {
    const raw =
      activeTab === "for-you"
        ? buildForYouTemplates(bySlug, skillNames, department)
        : getCategoryTemplates(activeTab, persona);
    const name = branding.app_name;
    return raw.map((t) => ({
      ...t,
      title: withAppName(t.title, name),
      description: withAppName(t.description, name),
      long: withAppName(t.long, name),
    }));
  }, [activeTab, branding.app_name, bySlug, department, persona, skillNames]);

  const askSuggestions = useMemo(
    () => buildAskSuggestionsFromSkills(bySlug, skillNames, department),
    [bySlug, department, skillNames],
  );

  const openWorkspace = (draft: WorkspaceDraftState) => {
    // Always start a fresh chat so Home Ask / templates don't land mid-thread
    // in the user's most recently updated conversation.
    navigate("/workspace", { state: { newChat: true, workspaceDraft: draft } });
  };

  const handleAsk = () => {
    const prompt = askText.trim();
    if (!prompt) return;
    openWorkspace({ prompt });
  };

  const handleStartTemplate = (template: HomeTemplate) => {
    const access = resolveTemplateAccess(template, bySlug);
    setPreview(null);

    if (access === "locked" && template.skillSlug) {
      navigate(`/skills/${template.skillSlug}`);
      return;
    }

    if (template.workspacePrompt || template.workspaceSkillId) {
      openWorkspace({
        prompt: template.workspacePrompt,
        skillId: template.workspaceSkillId,
      });
      return;
    }

    if (template.href) {
      navigate(template.href);
    }
  };

  if (loading) {
    return (
      <div className="flex-1 flex items-center justify-center bg-[#F2F3F4]">
        <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
      </div>
    );
  }

  return (
    <div className="flex-1 bg-[#F2F3F4] overflow-y-auto">
      <div className="max-w-5xl mx-auto px-6 py-10 space-y-10">

        {/* Greeting */}
        <section className="space-y-3">
          <h1 className="text-3xl font-bold text-[#0E0E0E] tracking-tight">
            {greetingTime()}, {firstName(employee?.name ?? employee?.email ?? null)}
          </h1>
          <p className="text-sm font-medium text-slate-500">
            {deptBucketLabel} team · here&apos;s what you can build today
          </p>
          <div className="flex flex-wrap gap-2 pt-1">
            <span className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-white border border-slate-200 text-xs font-semibold text-slate-700">
              <Layers className="w-3.5 h-3.5 text-[#FF444F]" />
              {skillsAvailable} skill
              {skillsAvailable === 1 ? "" : "s"} available
            </span>
            {skillsToRequest > 0 && (
              <span className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-white border border-slate-200 text-xs font-semibold text-slate-700">
                <Lock className="w-3.5 h-3.5 text-slate-500" />
                {skillsToRequest} to request
              </span>
            )}
            {summary && summary.slack_agents_live > 0 && (
              <span className="inline-flex items-center gap-1.5 h-8 px-3 rounded-full bg-white border border-slate-200 text-xs font-semibold text-slate-700">
                <Bot className="w-3.5 h-3.5 text-[#FF444F]" />
                {summary.slack_agents_live} Slack agent{summary.slack_agents_live === 1 ? "" : "s"} live
              </span>
            )}
          </div>
        </section>

        <AutomationLingo />

        {/* Ask bar */}
        <section data-tour="prompt-bar" className="bg-white border border-slate-200 rounded-2xl p-5 shadow-sm space-y-3">
          <div className="flex gap-3 items-center">
            <div className="w-9 h-9 rounded-xl bg-[#FF444F]/10 flex items-center justify-center shrink-0 text-[#FF444F]">
              <Sparkles className="w-4 h-4" />
            </div>
            <input
              type="text"
              value={askText}
              onChange={(e) => setAskText(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleAsk();
              }}
              placeholder={`Ask ${branding.app_name} anything — data, a document, an agent, a skill...`}
              className="flex-1 text-sm text-slate-900 placeholder:text-slate-400 bg-transparent focus:outline-none"
            />
            <button
              type="button"
              onClick={handleAsk}
              disabled={!askText.trim()}
              className="inline-flex items-center gap-2 px-4 py-2.5 rounded-xl bg-[#FF444F] hover:bg-[#e63d48] disabled:opacity-40 disabled:cursor-not-allowed text-white text-sm font-bold transition-colors shrink-0"
            >
              Ask
              <Send className="w-4 h-4" />
            </button>
          </div>
          <div className="flex flex-wrap gap-2 pl-12">
            {askSuggestions.map((s, i) => (
              <button
                key={`${i}-${s}`}
                type="button"
                onClick={() => setAskText(s)}
                title={s}
                className="max-w-[220px] truncate px-3 py-1.5 rounded-full bg-slate-100 hover:bg-slate-200 text-xs font-medium text-slate-600 transition-colors"
              >
                {s}
              </button>
            ))}
          </div>
        </section>

        {/* Templates */}
        <section data-tour="starting-points" className="space-y-5">
          <div className="space-y-1">
            <p className="text-xs font-bold uppercase tracking-widest text-[#FF444F]">Start here</p>
            <h2 className="text-2xl font-bold text-[#0E0E0E]">Pick a starting point</h2>
            <p className="text-sm text-slate-500 max-w-xl">
              {activeTab === "for-you"
                ? "Cards are built from skills you can use or request — not generic templates."
                : `Every card below is something ${branding.app_name} can already build for you. Pick one to see what it needs, then start it.`}
            </p>
          </div>

          <div className="flex flex-wrap gap-1 border-b border-slate-200">
            {HOME_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`px-4 py-2.5 text-sm font-semibold border-b-2 -mb-px transition-colors ${
                  activeTab === tab.id
                    ? "border-[#FF444F] text-[#FF444F]"
                    : "border-transparent text-slate-500 hover:text-slate-800"
                }`}
              >
                {tab.label}
              </button>
            ))}
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
            {templates.map((template) => (
              <TemplateCard
                key={template.id}
                template={template}
                access={resolveTemplateAccess(template, bySlug)}
                onPreview={setPreview}
              />
            ))}
          </div>
        </section>
      </div>

      {preview && (
        <PreviewModal
          template={preview}
          access={resolveTemplateAccess(preview, bySlug)}
          onClose={() => setPreview(null)}
          onStart={handleStartTemplate}
        />
      )}
    </div>
  );
}
