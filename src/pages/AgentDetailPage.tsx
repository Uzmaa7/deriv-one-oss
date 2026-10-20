import { useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { NavLink, useNavigate, useParams, useSearchParams } from "react-router-dom";
import AgentAccessRequestModal from "@/components/AgentAccessRequestModal";
import EmployeePicker, { type EmployeeResult } from "@/components/EmployeePicker";
import {
  AlertCircle,
  ArrowLeft,
  Bot,
  Brain,
  CalendarClock,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Clock,
  ExternalLink,
  GitMerge,
  Hash,
  Loader2,
  Lock,
  MessageSquare,
  Pause,
  Pencil,
  Play,
  Plus,
  PowerOff,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Tag,
  Trash2,
  UserPlus,
  Zap,
  Users,
  X,
} from "lucide-react";
import { validateAgentDescription, validateUsecaseDescription } from "@/lib/agentDescription";
import { apiFetch } from "@/lib/api";
import { useBranding } from "@/lib/branding";
import { InteractionDetailModal } from "@/components/InteractionDetailModal";
import AgentFormFields, { AgentFormState, agentModelLabel } from "../components/AgentFormFields";
import AgentIdentityEditor from "../components/AgentIdentityEditor";
import AgentKnowledgeBaseCard from "@/components/AgentKnowledgeBaseCard";
import {
  AgentInteractionListResponse,
  AgentInteractionRecord,
  AgentMemoryListResponse,
  AgentMemoryRecord,
  AgentRecord,
  statusChipClasses,
  statusDotColor,
  statusLabel,
} from "../data/agents";
import { useSkillAccess, useSkillList } from "@/lib/SkillAccessContext";

const XOXB_RE = /^xoxb-[A-Za-z0-9-]{10,512}$/;
const SIGNING_SECRET_RE = /^[a-f0-9]{8,}$/i;

interface ScheduledTask {
  id: string;
  agent_id: string;
  prompt: string;
  schedule: string;
  timezone: string;
  output_channel: string;
  is_active: boolean;
  report_enabled: boolean;
  report_prompt: string | null;
  default_report_prompt: string;
  last_run_at: string | null;
  last_run_status: string | null;
  run_count: number;
  created_at: string;
}

function fromAgent(a: AgentRecord): AgentFormState {
  return {
    name: a.name,
    description: a.description ?? "",
    usecase_description: a.usecase_description ?? "",
    avatar_url: a.avatar_url,
    slack_app_id: a.slack_app_id,
    skill_ids: [...a.skill_ids],
    channel_ids: [...a.channel_ids],
    trigger_mode: a.trigger_mode,
    soul_text: a.soul_text ?? "",
    llm_model: a.llm_model ?? null,
    schedule_enabled: false,
    schedule_cron: "0 9 * * *",
    schedule_prompt: "",
    schedule_output_channel: "",
  };
}

type AgentDetailTab = "overview" | "interactions" | "memory" | "slack-apps" | "guardrails" | "collaborators" | "access" | "skill-access" | "roles" | "automations";

export default function AgentDetailPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const initialInteractionId = searchParams.get("interaction");

  const [agent, setAgent] = useState<AgentRecord | null>(null);
  const [tasks, setTasks] = useState<ScheduledTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [accessStatus, setAccessStatus] = useState<"owner" | "collaborator" | "approved" | "pending" | "none" | null>(null);
  const { isAdmin } = useSkillAccess();
  const [showRequestModal, setShowRequestModal] = useState(false);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<AgentFormState | null>(null);
  const [busy, setBusy] = useState(false);
  const [showRotate, setShowRotate] = useState(false);
  const [showConnections, setShowConnections] = useState(false);
  const [activeTab, setActiveTab] = useState<AgentDetailTab>("overview");
  const [interactionsTotal, setInteractionsTotal] = useState<number | null>(null);
  const [interactionFailuresTotal, setInteractionFailuresTotal] = useState<number | null>(null);
  const [memoriesTotal, setMemoriesTotal] = useState<number | null>(null);

  useEffect(() => {
    const tab = searchParams.get("tab");
    if (tab === "interactions") {
      setActiveTab("interactions");
    }
  }, [searchParams]);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [aRes, tRes] = await Promise.all([
        apiFetch(`/api/agents/${id}`),
        apiFetch(`/api/agents/${id}/scheduled-tasks`),
      ]);
      if (!aRes.ok) {
        if (aRes.status === 404 || aRes.status === 403) {
          // Not the owner — try public endpoint to show access state
          const pubRes = await apiFetch(`/api/agents/${id}/public`);
          if (pubRes.ok) {
            const a: AgentRecord = await pubRes.json();
            setAgent(a);
            setAccessStatus(a.my_access ?? "none");
            setForm(null); // no editing
            return;
          }
          setError("Agent not found.");
          setAgent(null);
          return;
        }
        throw new Error(`HTTP ${aRes.status}`);
      }
      const a: AgentRecord = await aRes.json();
      setAgent(a);
      // my_access "approved" means the user has a grant but is not the owner —
      // show the full page read-only; "owner" gets full edit access.
      const myAccess = (a as AgentRecord & { my_access?: string }).my_access;
      if (myAccess === "approved") {
        setAccessStatus("approved");
        setForm(null);
      } else if (myAccess === "collaborator") {
        setAccessStatus("collaborator");
        setForm(fromAgent(a));
      } else {
        setAccessStatus("owner");
        setForm(fromAgent(a));
      }
      const t: ScheduledTask[] = tRes.ok ? await tRes.json() : [];
      setTasks(Array.isArray(t) ? t : []);
    } catch {
      setError("Could not load agent.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (id) load();
  }, [id]);

  const callLifecycle = async (action: "activate" | "pause" | "decommission") => {
    if (!agent) return;
    if (action === "decommission") {
      const confirmed = window.confirm(
        `Decommission "${agent.name}"? This deletes the bot tokens from the vault and cannot be undone.`
      );
      if (!confirmed) return;
    }
    setBusy(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}/${action}`, { method: "POST" });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || `Action failed (HTTP ${res.status}).`);
      }
      if (action === "decommission") {
        navigate("/agents");
      } else {
        await load();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Action failed.");
    } finally {
      setBusy(false);
    }
  };

  const saveEdits = async () => {
    if (!agent || !form) return;
    const descErr = validateAgentDescription(form.description);
    if (descErr) {
      setError(descErr);
      return;
    }
    const usecaseErr = validateUsecaseDescription(form.usecase_description);
    if (usecaseErr) {
      setError(usecaseErr);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const body = {
        name: form.name.trim(),
        description: form.description.trim() || null,
        usecase_description: form.usecase_description.trim() || null,
        avatar_url: form.avatar_url,
        skill_ids: form.skill_ids,
        soul_text: form.soul_text || null,
        channel_ids: form.channel_ids,
        trigger_mode: form.trigger_mode,
        llm_model: form.llm_model,
      };
      const res = await apiFetch(`/api/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || `Update failed (HTTP ${res.status}).`);
      }
      const updated: AgentRecord = await res.json();
      setAgent(updated);
      setForm(fromAgent(updated));
      setEditing(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Update failed.");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#F9FAFB]">
        <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
      </div>
    );
  }

  if (!agent) {
    return (
      <div className="flex flex-col items-center justify-center h-full bg-[#F9FAFB] text-center px-6">
        <Sparkles className="w-10 h-10 text-slate-300 mb-3" />
        <h2 className="text-base font-bold text-slate-900 mb-1">{error ?? "Agent not found"}</h2>
        <NavLink
          to="/agents"
          className="mt-4 inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"
        >
          <ArrowLeft className="w-3 h-3" /> Back to agents
        </NavLink>
      </div>
    );
  }

  // Non-owner view: polished public profile with access request prompt
  if (accessStatus !== "owner" && accessStatus !== "collaborator" && accessStatus !== "approved") {
    return (
      <div className="h-full overflow-y-auto bg-[#F9FAFB]">
        <div className="max-w-2xl mx-auto px-6 py-10 flex flex-col gap-6">
          {/* Back link */}
          <NavLink
            to="/agents"
            className="inline-flex items-center gap-1.5 text-xs text-slate-400 hover:text-slate-700 transition-colors w-fit"
          >
            <ArrowLeft className="w-3 h-3" /> Back to agents
          </NavLink>

          {/* Hero card */}
          <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
            {/* Coloured top band */}
            <div className="h-24 bg-gradient-to-r from-slate-800 to-slate-600 relative">
              {/* Avatar pinned to bottom-left */}
              <div className="absolute -bottom-6 left-6 w-14 h-14 rounded-2xl bg-white border-2 border-white shadow-md flex items-center justify-center overflow-hidden">
                {agent.avatar_url
                  ? <img src={agent.avatar_url} alt={agent.name} className="w-14 h-14 object-cover" />
                  : <Bot className="w-7 h-7 text-slate-500" />
                }
              </div>
            </div>

            <div className="pt-10 pb-6 px-6 flex flex-col gap-5">
              {/* Name + status */}
              <div className="flex items-start justify-between gap-4 flex-wrap">
                <div>
                  <h1 className="text-xl font-bold text-slate-900">{agent.name}</h1>
                  <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                    <span className={`inline-flex items-center gap-1 text-xs font-medium border rounded-full px-2 py-0.5 ${statusChipClasses(agent.status)}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${statusDotColor(agent.status)}`} />
                      {statusLabel(agent.status)}
                    </span>
                    {agent.skill_ids.length > 0 && (
                      <span className="text-xs text-slate-400">{agent.skill_ids.length} skill{agent.skill_ids.length !== 1 ? "s" : ""}</span>
                    )}
                  </div>
                </div>
                {accessStatus === "pending" ? (
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold bg-amber-50 border border-amber-200 text-amber-700 rounded-full px-3 py-1">
                    <Clock className="w-3 h-3" /> Pending approval
                  </span>
                ) : (
                  <span className="inline-flex items-center gap-1.5 text-xs font-semibold bg-slate-50 border border-slate-200 text-slate-500 rounded-full px-3 py-1">
                    <Lock className="w-3 h-3" /> No access
                  </span>
                )}
              </div>

              {/* Description */}
              {agent.description && (
                <p className="text-sm text-slate-600 leading-relaxed">{agent.description}</p>
              )}

              {/* Divider */}
              <div className="border-t border-slate-100" />

              {/* Access CTA */}
              {accessStatus === "pending" ? (
                <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-4">
                  <div className="w-8 h-8 rounded-lg bg-amber-100 flex items-center justify-center shrink-0 mt-0.5">
                    <Clock className="w-4 h-4 text-amber-600" />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-amber-900">Access request submitted</p>
                    <p className="text-xs text-amber-700 mt-0.5">Your request is awaiting admin approval. You'll be able to interact with this agent once approved.</p>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between gap-4 flex-wrap">
                  <div>
                    <p className="text-sm font-semibold text-slate-900">Request access to use this agent</p>
                    <p className="text-xs text-slate-500 mt-0.5">Submit a request and an admin will review it shortly.</p>
                  </div>
                  <button
                    onClick={() => setShowRequestModal(true)}
                    className="shrink-0 px-5 py-2.5 bg-slate-900 text-white text-sm font-semibold rounded-xl hover:bg-slate-700 transition-colors"
                  >
                    Request Access
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {showRequestModal && (
          <AgentAccessRequestModal
            agentId={agent.id}
            agentName={agent.name}
            agentDescription={agent.description}
            onClose={() => setShowRequestModal(false)}
            onSuccess={() => {
              setShowRequestModal(false);
              setAccessStatus("pending");
            }}
          />
        )}
      </div>
    );
  }

  const decommissioned = agent.status === "decommissioned";
  const pendingOAuth = agent.status === "pending_oauth";

  return (
    <div className="h-full overflow-y-auto bg-[#F9FAFB]">
      <div className="mx-auto w-full max-w-6xl px-6 py-6 space-y-5">
        <NavLink
          to="/agents"
          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 transition-colors"
        >
          <ArrowLeft className="w-3 h-3" /> Back to agents
        </NavLink>

        {/* Pending OAuth banner */}
        {pendingOAuth && (
          <PendingOAuthBanner agentId={agent.id} />
        )}

        {/* Self-created agent missing the service-account collaborator */}
        {!decommissioned &&
          !pendingOAuth &&
          (accessStatus === "owner" || accessStatus === "collaborator") &&
          agent.creation_method !== "auto_oauth" &&
          !agent.manifest_access_verified &&
          agent.slack_app_id && (
            <ServiceAccountBanner
              agent={agent}
              onVerified={() => setAgent({ ...agent, manifest_access_verified: true })}
            />
          )}

        {/* Header */}
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <div className="flex items-start justify-between gap-3">
            {(accessStatus === "owner" || accessStatus === "collaborator") && !decommissioned ? (
              <AgentIdentityEditor
                agent={agent}
                disabled={busy || editing}
                onUpdated={updated => {
                  setAgent(updated);
                  setForm(fromAgent(updated));
                }}
                onError={msg => setError(msg || null)}
              >
                <div className="flex items-center gap-2 mt-2 flex-wrap">
                  <span className={`inline-flex items-center gap-1 text-xs font-medium border rounded-full px-2 py-0.5 ${statusChipClasses(agent.status)}`}>
                    <span className={`w-1.5 h-1.5 rounded-full ${statusDotColor(agent.status)}`} />
                    {statusLabel(agent.status)}
                  </span>
                  <span className="text-xs text-slate-500 font-mono">{agent.slack_app_id}</span>
                  <span className="text-xs text-slate-500">{agent.creator_email}</span>
                </div>
                {agent.description && (
                  <p className="text-sm text-slate-600 mt-3 leading-relaxed max-w-3xl">{agent.description}</p>
                )}
              </AgentIdentityEditor>
            ) : (
              <div className="flex items-start gap-3 min-w-0 flex-1">
                <div className="w-12 h-12 rounded-xl bg-gradient-to-br from-slate-100 to-slate-200 border border-slate-200 flex items-center justify-center shrink-0 overflow-hidden">
                  {agent.avatar_url
                    ? <img src={agent.avatar_url} alt={agent.name} className="w-12 h-12 object-cover" />
                    : <Bot className="w-6 h-6 text-slate-400" />
                  }
                </div>
                <div className="min-w-0">
                  <h1 className="text-xl font-bold text-slate-900 truncate">{agent.name}</h1>
                  <div className="flex items-center gap-2 mt-2 flex-wrap">
                    <span className={`inline-flex items-center gap-1 text-xs font-medium border rounded-full px-2 py-0.5 ${statusChipClasses(agent.status)}`}>
                      <span className={`w-1.5 h-1.5 rounded-full ${statusDotColor(agent.status)}`} />
                      {statusLabel(agent.status)}
                    </span>
                    <span className="text-xs text-slate-500 font-mono">{agent.slack_app_id}</span>
                    <span className="text-xs text-slate-500">{agent.creator_email}</span>
                  </div>
                  {agent.description && (
                    <p className="text-sm text-slate-600 mt-3 leading-relaxed max-w-3xl">{agent.description}</p>
                  )}
                </div>
              </div>
            )}
            <button
              onClick={load}
              className="text-slate-400 hover:text-slate-700 p-1.5"
              title="Refresh"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
          </div>

          <div className="flex flex-wrap gap-1.5 pt-2 border-t border-slate-100">
            {accessStatus === "approved" && (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-full px-3 py-1">
                <CheckCircle2 className="w-3 h-3" /> Access granted
              </span>
            )}
            {accessStatus === "collaborator" && (
              <span className="inline-flex items-center gap-1.5 text-xs font-semibold bg-indigo-50 border border-indigo-200 text-indigo-700 rounded-full px-3 py-1">
                <Users className="w-3 h-3" /> Collaborator
              </span>
            )}
            {(accessStatus === "owner" || accessStatus === "collaborator") && (<>
            {(accessStatus === "owner" || accessStatus === "collaborator") && (agent.status === "registered" || agent.status === "paused") ? (
              <ActionButton onClick={() => callLifecycle("activate")} disabled={busy} icon={<Play className="w-3 h-3" />} primary>
                Activate
              </ActionButton>
            ) : null}
            {(accessStatus === "owner" || accessStatus === "collaborator") && agent.status === "active" ? (
              <ActionButton onClick={() => callLifecycle("pause")} disabled={busy} icon={<Pause className="w-3 h-3" />}>
                Pause
              </ActionButton>
            ) : null}
            {!decommissioned && !editing ? (
              <ActionButton onClick={() => setEditing(true)} disabled={busy}>
                Edit
              </ActionButton>
            ) : null}
            {(accessStatus === "owner" || accessStatus === "collaborator") && !decommissioned ? (
              <ActionButton onClick={() => setShowRotate(true)} disabled={busy} icon={<ShieldCheck className="w-3 h-3" />}>
                Rotate tokens
              </ActionButton>
            ) : null}
            {!decommissioned ? (
              <ActionButton onClick={() => setShowConnections(true)} disabled={busy} icon={<GitMerge className="w-3 h-3" />}>
                Connections
              </ActionButton>
            ) : null}
            {(accessStatus === "owner" || accessStatus === "collaborator") && !decommissioned ? (
              <ActionButton
                onClick={() => callLifecycle("decommission")}
                disabled={busy}
                icon={<PowerOff className="w-3 h-3" />}
                destructive
              >
                Decommission
              </ActionButton>
            ) : null}
            </>)}
          </div>
        </div>


        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3 flex items-start gap-2">
            <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {/* Edit / view */}
        {editing && form ? (
          <>
            <AgentFormFields
              form={form}
              onChange={setForm}
              disableSlackAppId
              showSchedule={false}
              agentId={agent.id}
            />
            <div className="flex gap-2 sticky bottom-0 bg-[#F9FAFB] py-3 -mx-6 px-6 border-t border-slate-200">
              <button
                onClick={() => {
                  setEditing(false);
                  setForm(fromAgent(agent));
                }}
                disabled={busy}
                className="px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={saveEdits}
                disabled={busy}
                className="flex-1 sm:flex-none px-6 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
              >
                {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Save changes
              </button>
            </div>
          </>
        ) : (
          <>
            {!editing && (
              <div className="flex gap-1 bg-white border border-slate-200 rounded-xl p-1 overflow-x-auto scrollbar-none">
                <button
                  type="button"
                  onClick={() => setActiveTab("overview")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "overview"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <Sparkles className="w-3.5 h-3.5" />
                  Overview
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("interactions")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "interactions"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <MessageSquare className="w-3.5 h-3.5" />
                  Interaction log
                  {interactionsTotal != null && interactionsTotal > 0 && (
                    <span
                      className={`rounded-full px-1.5 text-[10px] font-bold ${
                        activeTab === "interactions"
                          ? "bg-white/20 text-white"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {interactionsTotal}
                    </span>
                  )}
                  {interactionFailuresTotal != null && interactionFailuresTotal > 0 && (
                    <span
                      className={`rounded-full px-1.5 text-[10px] font-bold ${
                        activeTab === "interactions"
                          ? "bg-red-500/30 text-white"
                          : "bg-red-50 text-red-600"
                      }`}
                    >
                      {interactionFailuresTotal} failed
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("memory")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "memory"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <Brain className="w-3.5 h-3.5" />
                  Memory
                  {memoriesTotal != null && memoriesTotal > 0 && (
                    <span
                      className={`rounded-full px-1.5 text-[10px] font-bold ${
                        activeTab === "memory"
                          ? "bg-white/20 text-white"
                          : "bg-slate-100 text-slate-600"
                      }`}
                    >
                      {memoriesTotal}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("slack-apps")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "slack-apps"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <Bot className="w-3.5 h-3.5" />
                  Slack Apps
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("guardrails")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "guardrails"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <ShieldCheck className="w-3.5 h-3.5" />
                  Guardrails
                </button>
                <button
                  type="button"
                  onClick={() => setActiveTab("collaborators")}
                  className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                    activeTab === "collaborators"
                      ? "bg-slate-900 text-white"
                      : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                  }`}
                >
                  <Users className="w-3.5 h-3.5" />
                  Collaborators
                </button>
                {(accessStatus === "owner" || accessStatus === "collaborator") && (
                  <button
                    type="button"
                    onClick={() => setActiveTab("access")}
                    className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                      activeTab === "access"
                        ? "bg-slate-900 text-white"
                        : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                    }`}
                  >
                    <ShieldCheck className="w-3.5 h-3.5" />
                    User Access
                  </button>
                )}
                {(accessStatus === "owner" || accessStatus === "collaborator") && (
                  <button
                    type="button"
                    onClick={() => setActiveTab("skill-access")}
                    className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                      activeTab === "skill-access"
                        ? "bg-slate-900 text-white"
                        : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                    }`}
                  >
                    <Zap className="w-3.5 h-3.5" />
                    Skill Access
                  </button>
                )}
                {(accessStatus === "owner" || accessStatus === "collaborator") && (
                  <button
                    type="button"
                    onClick={() => setActiveTab("roles")}
                    className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                      activeTab === "roles"
                        ? "bg-slate-900 text-white"
                        : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                    }`}
                  >
                    <Tag className="w-3.5 h-3.5" />
                    Roles
                  </button>
                )}
                {(accessStatus === "owner" || accessStatus === "collaborator") && (
                  <button
                    type="button"
                    onClick={() => setActiveTab("automations")}
                    className={`shrink-0 flex items-center gap-1.5 px-3 h-9 text-xs font-semibold rounded-lg transition-colors whitespace-nowrap ${
                      activeTab === "automations"
                        ? "bg-slate-900 text-white"
                        : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
                    }`}
                  >
                    <CalendarClock className="w-3.5 h-3.5" />
                    Automations
                    {tasks.length > 0 && (
                      <span
                        className={`rounded-full px-1.5 text-[10px] font-bold ${
                          activeTab === "automations"
                            ? "bg-white/20 text-white"
                            : "bg-slate-100 text-slate-600"
                        }`}
                      >
                        {tasks.length}
                      </span>
                    )}
                  </button>
                )}
              </div>
            )}

            <div className="w-full">
              {activeTab === "overview" ? (
                <ViewMode
                  agent={agent}
                  tasks={tasks}
                  onRefresh={load}
                  isOwner={accessStatus === "owner" || accessStatus === "collaborator"}
                  canEdit={accessStatus === "owner" || accessStatus === "collaborator"}
                />
              ) : activeTab === "interactions" ? (
                <InteractionLogTab
                  agentId={agent.id}
                  initialInteractionId={initialInteractionId}
                  onTotalChange={setInteractionsTotal}
                  onFailureTotalChange={setInteractionFailuresTotal}
                />
              ) : activeTab === "slack-apps" ? (
                <SlackAppsGrantTab agentId={agent.id} />
              ) : activeTab === "guardrails" ? (
                <GuardrailsTab agentId={agent.id} readonly={accessStatus !== "owner" && accessStatus !== "collaborator"} />
              ) : activeTab === "collaborators" ? (
                <CollaboratorsTab agentId={agent.id} isOwner={accessStatus === "owner" || isAdmin} />
              ) : activeTab === "access" ? (
                <UserAccessTab agentId={agent.id} />
              ) : activeTab === "skill-access" ? (
                <SkillAccessTab agentId={agent.id} />
              ) : activeTab === "roles" ? (
                <RolesTab agentId={agent.id} />
              ) : activeTab === "automations" ? (
                <AutomationsTab
                  agentId={agent.id}
                  tasks={tasks}
                  onTasksChange={setTasks}
                  readonly={accessStatus !== "owner" && accessStatus !== "collaborator"}
                />
              ) : (
                <MemoryTab
                  agentId={agent.id}
                  onTotalChange={setMemoriesTotal}
                />
              )}
            </div>
          </>
        )}

        {showRotate && (
          <RotateTokensModal
            agentId={agent.id}
            onClose={() => setShowRotate(false)}
            onRotated={load}
          />
        )}

        {showConnections && (
          <ConnectionsModal
            agent={agent}
            onClose={() => setShowConnections(false)}
            onSaved={load}
          />
        )}
      </div>
    </div>
  );
}

// ── Automations tab ───────────────────────────────────────────────────────────

const CRON_PRESETS = [
  { label: "Every day at 9 AM", value: "0 9 * * *" },
  { label: "Every day at 6 PM", value: "0 18 * * *" },
  { label: "Every Monday at 9 AM", value: "0 9 * * 1" },
  { label: "Every weekday at 9 AM", value: "0 9 * * 1-5" },
  { label: "Every hour", value: "0 * * * *" },
  { label: "Custom…", value: "" },
];

const TIMEZONES = [
  "Asia/Dubai",
  "UTC",
  "Europe/London",
  "Europe/Berlin",
  "Asia/Singapore",
  "Asia/Kolkata",
  "America/New_York",
  "America/Los_Angeles",
];

function describeCron(cron: string): string {
  const presetMatch = CRON_PRESETS.find((p) => p.value === cron);
  if (presetMatch && presetMatch.value) return presetMatch.label;
  return cron;
}

interface AutomationsTabProps {
  agentId: string;
  tasks: ScheduledTask[];
  onTasksChange: (tasks: ScheduledTask[]) => void;
  readonly?: boolean;
}

function AutomationsTab({ agentId, tasks, onTasksChange, readonly = false }: AutomationsTabProps) {
  const [showForm, setShowForm] = useState(false);
  const [editingTask, setEditingTask] = useState<ScheduledTask | null>(null);
  const [busy, setBusy] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const [runningTaskId, setRunningTaskId] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const defaultReportPrompt =
    tasks[0]?.default_report_prompt ??
    "You are a data analyst. Write a concise, well-formatted markdown report based on the query results.";

  const emptyForm = {
    prompt: "",
    schedule: "0 9 * * *",
    customCron: "",
    useCustom: false,
    timezone: "Asia/Dubai",
    output_channel: "",
    is_active: true,
    report_enabled: false,
    report_prompt: defaultReportPrompt,
  };
  const [form, setForm] = useState(emptyForm);

  function openNew() {
    setEditingTask(null);
    setForm({ ...emptyForm, report_prompt: defaultReportPrompt });
    setFormError(null);
    setShowForm(true);
  }

  function openEdit(t: ScheduledTask) {
    const presetMatch = CRON_PRESETS.find((p) => p.value === t.schedule && p.value !== "");
    setEditingTask(t);
    setForm({
      prompt: t.prompt,
      schedule: presetMatch ? t.schedule : "custom",
      customCron: presetMatch ? "" : t.schedule,
      useCustom: !presetMatch,
      timezone: t.timezone,
      output_channel: t.output_channel,
      is_active: t.is_active,
      report_enabled: t.report_enabled,
      report_prompt: t.report_prompt ?? t.default_report_prompt,
    });
    setFormError(null);
    setShowForm(true);
  }

  function cancelForm() {
    setShowForm(false);
    setEditingTask(null);
    setFormError(null);
  }

  async function saveTask() {
    const resolvedCron = form.useCustom ? form.customCron.trim() : form.schedule;
    if (!form.prompt.trim()) { setFormError("Prompt is required."); return; }
    if (!resolvedCron) { setFormError("Schedule is required."); return; }
    if (!form.output_channel.trim()) { setFormError("Output channel is required."); return; }

    setBusy(true);
    setFormError(null);
    try {
      const body = {
        prompt: form.prompt.trim(),
        schedule: resolvedCron,
        timezone: form.timezone,
        output_channel: form.output_channel.trim().replace(/^#/, ""),
        is_active: form.is_active,
        report_enabled: form.report_enabled,
        report_prompt: form.report_enabled ? form.report_prompt.trim() || null : null,
      };
      const url = editingTask
        ? `/api/agents/${agentId}/scheduled-tasks/${editingTask.id}`
        : `/api/agents/${agentId}/scheduled-tasks`;
      const res = await apiFetch(url, {
        method: editingTask ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to save automation.");
      }
      const saved: ScheduledTask = await res.json();
      if (editingTask) {
        onTasksChange(tasks.map((t) => (t.id === saved.id ? saved : t)));
      } else {
        onTasksChange([...tasks, saved]);
      }
      cancelForm();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Failed to save automation.");
    } finally {
      setBusy(false);
    }
  }

  async function toggleActive(task: ScheduledTask) {
    try {
      const res = await apiFetch(`/api/agents/${agentId}/scheduled-tasks/${task.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ is_active: !task.is_active }),
      });
      if (res.ok) {
        const updated: ScheduledTask = await res.json();
        onTasksChange(tasks.map((t) => (t.id === updated.id ? updated : t)));
      }
    } catch {
      // silent
    }
  }

  async function deleteTask(task: ScheduledTask) {
    if (!window.confirm(`Delete automation "${task.prompt.slice(0, 60)}…"?`)) return;
    try {
      const res = await apiFetch(`/api/agents/${agentId}/scheduled-tasks/${task.id}`, { method: "DELETE" });
      if (res.ok || res.status === 204) {
        onTasksChange(tasks.filter((t) => t.id !== task.id));
      }
    } catch {
      // silent
    }
  }

  async function runTaskNow(task: ScheduledTask) {
    if (runningTaskId) return;
    setRunningTaskId(task.id);
    const startedAt = task.last_run_at;

    try {
      const res = await apiFetch(`/api/agents/${agentId}/scheduled-tasks/${task.id}/run`, {
        method: "POST",
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || "Failed to trigger run.");
      }
    } catch (e) {
      setRunningTaskId(null);
      window.alert(e instanceof Error ? e.message : "Failed to trigger run.");
      return;
    }

    let polls = 0;
    pollRef.current = setInterval(async () => {
      polls += 1;
      try {
        const res = await apiFetch(`/api/agents/${agentId}/scheduled-tasks`);
        if (!res.ok) return;
        const updatedTasks: ScheduledTask[] = await res.json();
        onTasksChange(updatedTasks);
        const updated = updatedTasks.find((t) => t.id === task.id);
        const runFinished =
          updated &&
          updated.last_run_at &&
          updated.last_run_at !== startedAt &&
          updated.last_run_status;
        if (runFinished || polls >= 90) {
          if (pollRef.current) clearInterval(pollRef.current);
          pollRef.current = null;
          setRunningTaskId(null);
        }
      } catch {
        if (pollRef.current) clearInterval(pollRef.current);
        pollRef.current = null;
        setRunningTaskId(null);
      }
    }, 2000);
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Scheduled Automations</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Run prompts on a schedule — the agent posts results to a Slack channel using its connected bot token.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={openNew}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors"
          >
            <Plus className="w-3.5 h-3.5" />
            New automation
          </button>
        )}
      </div>

      {/* Create / edit form */}
      {showForm && (
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-4">
          <h4 className="text-sm font-semibold text-slate-900">
            {editingTask ? "Edit automation" : "New automation"}
          </h4>

          {/* Prompt */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-700">Prompt</label>
            <textarea
              rows={3}
              value={form.prompt}
              onChange={(e) => setForm({ ...form, prompt: e.target.value })}
              placeholder="e.g. Summarise open P2P disputes from the last 24 hours and post a bullet-point report."
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 resize-none focus:outline-none focus:ring-2 focus:ring-slate-900/20"
            />
          </div>

          {/* Schedule */}
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <label className="text-xs font-medium text-slate-700">Schedule</label>
              <select
                value={form.useCustom ? "custom" : form.schedule}
                onChange={(e) => {
                  const v = e.target.value;
                  if (v === "custom") {
                    setForm({ ...form, useCustom: true, schedule: "custom" });
                  } else {
                    setForm({ ...form, useCustom: false, schedule: v, customCron: "" });
                  }
                }}
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-slate-900/20 bg-white"
              >
                {CRON_PRESETS.map((p) => (
                  <option key={p.label} value={p.value || "custom"}>{p.label}</option>
                ))}
              </select>
              {form.useCustom && (
                <input
                  type="text"
                  value={form.customCron}
                  onChange={(e) => setForm({ ...form, customCron: e.target.value })}
                  placeholder="0 9 * * 1"
                  className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 font-mono focus:outline-none focus:ring-2 focus:ring-slate-900/20"
                />
              )}
            </div>

            <div className="space-y-1.5">
              <label className="text-xs font-medium text-slate-700">Timezone</label>
              <select
                value={form.timezone}
                onChange={(e) => setForm({ ...form, timezone: e.target.value })}
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-slate-900/20 bg-white"
              >
                {TIMEZONES.map((tz) => (
                  <option key={tz} value={tz}>{tz}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Output channel */}
          <div className="space-y-1.5">
            <label className="text-xs font-medium text-slate-700">Output Slack channel</label>
            <div className="relative">
              <Hash className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
              <input
                type="text"
                value={form.output_channel}
                onChange={(e) => setForm({ ...form, output_channel: e.target.value })}
                placeholder="general"
                className="w-full text-sm border border-slate-200 rounded-lg pl-8 pr-3 py-2 focus:outline-none focus:ring-2 focus:ring-slate-900/20"
              />
            </div>
            <p className="text-[11px] text-slate-400">The agent must be a member of this channel.</p>
          </div>

          {/* Report formatting (per automation) */}
          <div className="border border-slate-100 rounded-lg p-4 space-y-3 bg-slate-50/50">
            <div>
              <p className="text-xs font-medium text-slate-700">Report formatting</p>
              <p className="text-[11px] text-slate-400 mt-0.5">
                When enabled, posts a formatted summary to the output channel after the run.
                When disabled, only in-run tool actions run (e.g. thread replies) — no summary post.
              </p>
            </div>
            <label className={`flex items-center gap-2 select-none ${readonly ? "cursor-default" : "cursor-pointer"}`}>
              <input
                type="checkbox"
                checked={form.report_enabled}
                disabled={readonly}
                onChange={(e) => setForm({ ...form, report_enabled: e.target.checked })}
                className="w-4 h-4 rounded accent-slate-900"
              />
              <span className="text-xs text-slate-700">Enable report formatting step</span>
            </label>
            {form.report_enabled && (
              <div className="space-y-1.5">
                <div className="flex items-center justify-between gap-2">
                  <label className="text-xs font-medium text-slate-700">Report system prompt</label>
                  {!readonly && (
                    <button
                      type="button"
                      onClick={() => setForm({ ...form, report_prompt: defaultReportPrompt })}
                      className="text-[11px] text-slate-500 hover:text-slate-800 underline"
                    >
                      Reset to default
                    </button>
                  )}
                </div>
                <textarea
                  rows={4}
                  value={form.report_prompt}
                  disabled={readonly}
                  onChange={(e) => setForm({ ...form, report_prompt: e.target.value })}
                  className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 resize-y focus:outline-none focus:ring-2 focus:ring-slate-900/20 disabled:bg-slate-50 disabled:text-slate-500 font-mono text-[12px] leading-relaxed bg-white"
                />
              </div>
            )}
          </div>

          {/* Active toggle */}
          <label className="flex items-center gap-2 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={form.is_active}
              onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
              className="w-4 h-4 rounded accent-slate-900"
            />
            <span className="text-xs text-slate-700">Active — run on schedule</span>
          </label>

          {formError && (
            <p className="text-xs text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3.5 h-3.5" />
              {formError}
            </p>
          )}

          <div className="flex gap-2 pt-1">
            <button
              onClick={cancelForm}
              disabled={busy}
              className="px-4 py-2 text-xs text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
            <button
              onClick={saveTask}
              disabled={busy}
              className="flex items-center gap-1.5 px-5 py-2 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50"
            >
              {busy && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
              {editingTask ? "Save changes" : "Create automation"}
            </button>
          </div>
        </div>
      )}

      {/* Task list */}
      {tasks.length === 0 && !showForm ? (
        <div className="bg-white border border-slate-200 rounded-xl py-12 flex flex-col items-center gap-3 text-slate-400">
          <CalendarClock className="w-8 h-8 opacity-40" />
          <p className="text-sm font-medium">No automations yet</p>
          <p className="text-xs text-center max-w-xs">
            Create a scheduled automation to have this agent run a prompt on a recurring schedule and post results to Slack.
          </p>
        </div>
      ) : (
        tasks.map((task) => (
          <div
            key={task.id}
            className={`bg-white border rounded-xl p-4 space-y-3 ${task.is_active ? "border-slate-200" : "border-slate-100 opacity-60"}`}
          >
            <div className="flex items-start justify-between gap-3">
              <div className="flex-1 min-w-0">
                <p className="text-sm text-slate-800 leading-snug line-clamp-2">{task.prompt}</p>
              </div>
              <div className="flex items-center gap-1 shrink-0">
                <button
                  onClick={() => runTaskNow(task)}
                  disabled={runningTaskId === task.id}
                  title="Run now"
                  className="p-1.5 rounded-lg text-slate-400 hover:text-emerald-600 hover:bg-emerald-50 transition-colors disabled:opacity-50"
                >
                  {runningTaskId === task.id ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Zap className="w-3.5 h-3.5" />
                  )}
                </button>
                <button
                  onClick={() => toggleActive(task)}
                  title={task.is_active ? "Pause schedule" : "Resume schedule"}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  {task.is_active ? <Pause className="w-3.5 h-3.5" /> : <Play className="w-3.5 h-3.5" />}
                </button>
                <button
                  onClick={() => openEdit(task)}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-slate-700 hover:bg-slate-50 transition-colors"
                >
                  <Pencil className="w-3.5 h-3.5" />
                </button>
                <button
                  onClick={() => deleteTask(task)}
                  className="p-1.5 rounded-lg text-slate-400 hover:text-red-600 hover:bg-red-50 transition-colors"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <span className="inline-flex items-center gap-1 text-[11px] bg-slate-100 text-slate-600 rounded-md px-2 py-0.5 font-mono">
                <Clock className="w-3 h-3" />
                {describeCron(task.schedule)}
              </span>
              <span className="inline-flex items-center gap-1 text-[11px] bg-slate-100 text-slate-600 rounded-md px-2 py-0.5">
                {task.timezone}
              </span>
              <span className="inline-flex items-center gap-1 text-[11px] bg-slate-100 text-slate-600 rounded-md px-2 py-0.5">
                <Hash className="w-3 h-3" />
                {task.output_channel}
              </span>
              {task.report_enabled && (
                <span className="inline-flex items-center gap-1 text-[11px] bg-indigo-50 text-indigo-700 rounded-md px-2 py-0.5">
                  Report step
                </span>
              )}
              {!task.is_active && (
                <span className="inline-flex items-center gap-1 text-[11px] bg-amber-50 text-amber-600 rounded-md px-2 py-0.5">
                  Schedule paused
                </span>
              )}
              {runningTaskId === task.id && (
                <span className="inline-flex items-center gap-1 text-[11px] bg-emerald-50 text-emerald-600 rounded-md px-2 py-0.5">
                  Running…
                </span>
              )}
            </div>

            {(task.last_run_at || task.run_count > 0) && (
              <div className="flex items-center gap-3 text-[11px] text-slate-400 border-t border-slate-100 pt-2">
                {task.run_count > 0 && (
                  <span>{task.run_count} run{task.run_count !== 1 ? "s" : ""}</span>
                )}
                {task.last_run_at && (
                  <span>Last run: {new Date(task.last_run_at).toLocaleString()}</span>
                )}
                {task.last_run_status && (
                  <span
                    className={`font-medium ${
                      task.last_run_status === "success" ? "text-green-600" : "text-red-500"
                    }`}
                  >
                    {task.last_run_status}
                  </span>
                )}
              </div>
            )}
          </div>
        ))
      )}
    </div>
  );
}

// ── Collaborators tab ─────────────────────────────────────────────────────────

interface Collaborator {
  id: string;
  agent_id: string;
  user_email: string;
  user_name: string | null;
  added_by_email: string;
  created_at: string | null;
}

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{1,63}$/;

function CollaboratorsTab({
  agentId,
  isOwner,
}: {
  agentId: string;
  isOwner: boolean;
}) {
  const [collaborators, setCollaborators] = useState<Collaborator[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [newEmail, setNewEmail] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [removingEmail, setRemovingEmail] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/collaborators`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setCollaborators(data.collaborators ?? []);
    } catch {
      setError("Could not load collaborators.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [agentId]);

  const addCollaborator = async () => {
    const parsed = newEmail
      .split(",")
      .map(e => e.trim().toLowerCase())
      .filter(e => e.length > 0);
    const emails = Array.from(new Set<string>(parsed));
    if (emails.length === 0) {
      setAddError("Enter at least one email address.");
      return;
    }
    const invalid = emails.filter(e => !EMAIL_RE.test(e));
    if (invalid.length > 0) {
      setAddError(`Invalid email: ${invalid[0]}`);
      return;
    }
    setSubmitting(true);
    setAddError(null);
    try {
      const failures: string[] = [];
      for (const email of emails) {
        const res = await apiFetch(`/api/agents/${agentId}/collaborators`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ email }),
        });
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          failures.push(`${email}: ${(body as { detail?: string }).detail || `HTTP ${res.status}`}`);
        }
      }
      if (failures.length === emails.length) {
        throw new Error(failures.join("; "));
      }
      setNewEmail("");
      if (failures.length === 0) setAdding(false);
      else setAddError(`Added ${emails.length - failures.length}; failed: ${failures.join("; ")}`);
      await load();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Failed to add collaborator.");
    } finally {
      setSubmitting(false);
    }
  };

  const removeCollaborator = async (email: string) => {
    if (!window.confirm(`Remove ${email} as a collaborator?`)) return;
    setRemovingEmail(email);
    setError(null);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/collaborators/${encodeURIComponent(email)}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setCollaborators(prev => prev.filter(c => c.user_email !== email));
    } catch {
      setError("Failed to remove collaborator.");
    } finally {
      setRemovingEmail(null);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Collaborators</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Co-owners who have the same access as you — they can view and edit this agent, access logs and memory.
          </p>
        </div>
        {isOwner && !adding && (
          <button
            type="button"
            onClick={() => { setAdding(true); setAddError(null); setNewEmail(""); }}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold shrink-0"
          >
            <UserPlus className="w-3.5 h-3.5" /> Add
          </button>
        )}
      </div>

      {/* Add form */}
      {adding && (
        <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
            <UserPlus className="w-3.5 h-3.5 text-indigo-500" />
            Add collaborator
          </p>
          <div className="flex flex-col gap-2">
            <textarea
              autoFocus
              value={newEmail}
              onChange={e => setNewEmail(e.target.value)}
              placeholder="colleague@example.com, other@example.com"
              rows={2}
              maxLength={4000}
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 placeholder:text-slate-400 resize-none"
            />
            <p className="text-[11px] text-slate-400">Comma-separate multiple emails to add several at once.</p>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => void addCollaborator()}
                disabled={submitting || !newEmail.trim()}
                className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
              >
                {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
                Add
              </button>
              <button
                type="button"
                onClick={() => { setAdding(false); setAddError(null); }}
                className="px-3 py-2 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
              >
                Cancel
              </button>
            </div>
          </div>
          {addError && (
            <p className="text-[11px] text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" /> {addError}
            </p>
          )}
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {error}
        </div>
      )}

      {/* List */}
      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        </div>
      ) : collaborators.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center border border-dashed border-slate-200 rounded-xl">
          <Users className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">No collaborators yet</p>
          <p className="text-xs text-slate-400 mt-1 max-w-xs">
            {isOwner
              ? 'Click "Add" to invite a colleague as a co-owner of this agent.'
              : "The agent owner hasn't added any collaborators yet."}
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
          {collaborators.map(c => (
            <div key={c.user_email} className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors group">
              {/* Avatar placeholder */}
              <div className="w-8 h-8 rounded-full bg-indigo-100 border border-indigo-200 flex items-center justify-center shrink-0 text-indigo-700 text-xs font-bold">
                {(c.user_name ?? c.user_email).slice(0, 1).toUpperCase()}
              </div>

              <div className="flex-1 min-w-0">
                {c.user_name && (
                  <p className="text-xs font-semibold text-slate-900 truncate">{c.user_name}</p>
                )}
                <p className="text-xs text-slate-500 truncate">{c.user_email}</p>
                <p className="text-[11px] text-slate-400 mt-0.5">
                  Added by {c.added_by_email}
                  {c.created_at && (
                    <span className="ml-1">
                      · {new Date(c.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                    </span>
                  )}
                </p>
              </div>

              <div className="flex items-center gap-2 shrink-0">
                <span className="inline-flex items-center gap-1 text-[11px] font-semibold bg-indigo-50 border border-indigo-200 text-indigo-700 rounded-full px-2 py-0.5">
                  <Users className="w-3 h-3" /> Co-owner
                </span>
                {isOwner && (
                  <button
                    type="button"
                    onClick={() => removeCollaborator(c.user_email)}
                    disabled={removingEmail === c.user_email}
                    className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 transition-all disabled:opacity-50"
                    title="Remove collaborator"
                  >
                    {removingEmail === c.user_email ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <X className="w-3.5 h-3.5" />
                    )}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── User Access tab (owner-managed consumer grants) ───────────────────────────

interface UserAccessGrant {
  id: string;
  agent_id: string;
  user_email: string;
  user_name: string | null;
  status: string;
  justification: string | null;
  granted_by_email: string | null;
  decided_at: string | null;
  expires_at: string | null;
  created_at: string | null;
}

function UserAccessTab({ agentId }: { agentId: string }) {
  const [grants, setGrants] = useState<UserAccessGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [selectedEmployees, setSelectedEmployees] = useState<EmployeeResult[]>([]);
  const [expiresDays, setExpiresDays] = useState("");
  const [addError, setAddError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionGrantId, setActionGrantId] = useState<string | null>(null);
  const [showPendingOnly, setShowPendingOnly] = useState(false);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/grants`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setGrants(data.grants ?? []);
    } catch {
      setError("Could not load user access grants.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const grantAccess = async () => {
    const unique = Array.from(new Set(selectedEmployees.map(e => e.email)));
    if (unique.length === 0) {
      setAddError("Add at least one employee.");
      return;
    }
    setSubmitting(true);
    setAddError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_emails: unique,
          expires_in_days: expiresDays ? parseInt(expiresDays, 10) : null,
        }),
      });
      const body = await res.json().catch(() => ({})) as {
        detail?: string | { message?: string; errors?: Array<{ email: string; error: string }> };
        granted?: number;
        failed?: number;
        errors?: Array<{ email: string; error: string }>;
      };
      if (!res.ok) {
        const detail = body.detail;
        const msg = typeof detail === "string"
          ? detail
          : detail?.message ?? `HTTP ${res.status}`;
        throw new Error(msg);
      }
      if (body.failed && body.failed > 0 && body.errors?.length) {
        const summary = body.errors.map(e => `${e.email}: ${e.error}`).join("; ");
        setAddError(`Granted ${body.granted ?? 0}, failed ${body.failed}: ${summary}`);
        await load();
        return;
      }
      setSelectedEmployees([]);
      setExpiresDays("");
      setAdding(false);
      await load();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Failed to grant access.");
    } finally {
      setSubmitting(false);
    }
  };

  const approveGrant = async (grant: UserAccessGrant) => {
    setActionGrantId(grant.id);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/${grant.id}/approve`, { method: "POST" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Failed to approve request.");
    } finally {
      setActionGrantId(null);
    }
  };

  const denyGrant = async (grant: UserAccessGrant) => {
    const reason = window.prompt("Deny reason (optional):");
    if (reason === null) return;
    setActionGrantId(grant.id);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/${grant.id}/deny`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deny_reason: reason }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Failed to deny request.");
    } finally {
      setActionGrantId(null);
    }
  };

  const revokeGrant = async (grant: UserAccessGrant) => {
    if (!window.confirm(`Revoke access for ${grant.user_email}?`)) return;
    setActionGrantId(grant.id);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/${grant.id}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Failed to revoke access.");
    } finally {
      setActionGrantId(null);
    }
  };

  const statusBadge = (status: string) => {
    const styles: Record<string, string> = {
      approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
      pending: "bg-amber-50 text-amber-700 border-amber-200",
      denied: "bg-red-50 text-red-700 border-red-200",
      revoked: "bg-slate-50 text-slate-500 border-slate-200",
    };
    return (
      <span className={`inline-flex text-[11px] font-medium border rounded-full px-2 py-0.5 ${styles[status] ?? "bg-slate-50 text-slate-500 border-slate-200"}`}>
        {status}
      </span>
    );
  };

  const displayed = showPendingOnly ? grants.filter(g => g.status === "pending") : grants;
  const pendingCount = grants.filter(g => g.status === "pending").length;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">User Access</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Users who can interact with this agent in Slack. This is separate from collaborators (co-owners).
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {pendingCount > 0 && (
            <button
              type="button"
              onClick={() => setShowPendingOnly(v => !v)}
              className={`text-xs px-2.5 py-1.5 rounded-lg border transition-colors ${
                showPendingOnly
                  ? "bg-amber-50 border-amber-200 text-amber-700 font-semibold"
                  : "border-slate-200 text-slate-500 hover:bg-slate-50"
              }`}
            >
              Pending ({pendingCount})
            </button>
          )}
          {!adding && (
            <button
              type="button"
              onClick={() => { setAdding(true); setAddError(null); setSelectedEmployees([]); setExpiresDays(""); }}
              className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold"
            >
              <UserPlus className="w-3.5 h-3.5" /> Grant Access
            </button>
          )}
        </div>
      </div>

      {adding && (
        <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
            <ShieldCheck className="w-3.5 h-3.5 text-emerald-500" />
            Grant user access
          </p>
          <EmployeePicker selected={selectedEmployees} onChange={setSelectedEmployees} disabled={submitting} />
          <div className="flex items-center gap-2">
            <label className="text-xs text-slate-600 shrink-0">Expires in (days)</label>
            <input
              type="number"
              min={1}
              max={3650}
              value={expiresDays}
              onChange={e => setExpiresDays(e.target.value)}
              placeholder="Permanent"
              className="w-28 text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-200"
            />
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void grantAccess()}
              disabled={submitting || selectedEmployees.length === 0}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              Grant
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setAddError(null); }}
              className="px-3 py-2 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
          </div>
          {addError && (
            <p className="text-[11px] text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" /> {addError}
            </p>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        </div>
      ) : displayed.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center border border-dashed border-slate-200 rounded-xl">
          <ShieldCheck className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">
            {showPendingOnly ? "No pending requests" : "No user access grants yet"}
          </p>
          <p className="text-xs text-slate-400 mt-1 max-w-xs">
            {showPendingOnly
              ? "All access requests have been handled."
              : 'Click "Grant Access" to add users who can use this agent in Slack.'}
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
          {displayed.map(g => (
            <div key={g.id} className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors group">
              <div className="w-8 h-8 rounded-full bg-emerald-100 border border-emerald-200 flex items-center justify-center shrink-0 text-emerald-700 text-xs font-bold">
                {(g.user_name ?? g.user_email).slice(0, 1).toUpperCase()}
              </div>
              <div className="flex-1 min-w-0">
                {g.user_name && (
                  <p className="text-xs font-semibold text-slate-900 truncate">{g.user_name}</p>
                )}
                <p className="text-xs text-slate-500 truncate">{g.user_email}</p>
                {g.justification && (
                  <p className="text-[11px] text-slate-400 mt-0.5 truncate" title={g.justification}>
                    {g.justification}
                  </p>
                )}
                {g.expires_at && g.status === "approved" && (
                  <p className="text-[11px] text-slate-400 mt-0.5">
                    Expires {new Date(g.expires_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0">
                {statusBadge(g.status)}
                {g.status === "pending" && (
                  <>
                    <button
                      type="button"
                      onClick={() => void approveGrant(g)}
                      disabled={actionGrantId === g.id}
                      className="text-[11px] font-semibold px-2 py-1 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50"
                    >
                      {actionGrantId === g.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "Approve"}
                    </button>
                    <button
                      type="button"
                      onClick={() => void denyGrant(g)}
                      disabled={actionGrantId === g.id}
                      className="text-[11px] font-semibold px-2 py-1 border border-slate-200 text-slate-600 rounded-lg hover:bg-slate-50 disabled:opacity-50"
                    >
                      Deny
                    </button>
                  </>
                )}
                {g.status === "approved" && (
                  <button
                    type="button"
                    onClick={() => void revokeGrant(g)}
                    disabled={actionGrantId === g.id}
                    className="opacity-0 group-hover:opacity-100 text-[11px] font-semibold px-2 py-1 text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50 transition-all"
                  >
                    {actionGrantId === g.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "Revoke"}
                  </button>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Skill Access tab (owner-managed skill grants for agent skills) ────────────

interface GrantableSkill {
  slug: string;
  name: string;
  risk_level?: string;
  grantable: boolean;
  reason: string | null;
}

interface SkillAccessGrant {
  id: string;
  user_email: string;
  user_name: string | null;
  skill_slug: string;
  skill_name: string;
  status: string;
  grant_type: string;
  expires_at: string | null;
  decided_by_email: string | null;
  decided_at: string | null;
}

function SkillAccessTab({ agentId }: { agentId: string }) {
  const [grantableSkills, setGrantableSkills] = useState<GrantableSkill[]>([]);
  const [grants, setGrants] = useState<SkillAccessGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [adding, setAdding] = useState(false);
  const [selectedEmployees, setSelectedEmployees] = useState<EmployeeResult[]>([]);
  const [justification, setJustification] = useState("");
  const [grantType, setGrantType] = useState<"time_limited" | "permanent" | "restricted">("time_limited");
  const [expiresDays, setExpiresDays] = useState("90");
  const [selectedSlugs, setSelectedSlugs] = useState<Set<string>>(new Set());
  const [addError, setAddError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [actionGrantId, setActionGrantId] = useState<string | null>(null);

  const grantableOnly = grantableSkills.filter(s => s.grantable);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [skillsRes, grantsRes] = await Promise.all([
        apiFetch(`/api/agents/${agentId}/skills/grantable`),
        apiFetch(`/api/agents/${agentId}/skills/grants`),
      ]);
      if (!skillsRes.ok) throw new Error(`Skills fetch failed (${skillsRes.status})`);
      if (!grantsRes.ok) throw new Error(`Grants fetch failed (${grantsRes.status})`);
      const skillsData = await skillsRes.json();
      const grantsData = await grantsRes.json();
      const skills: GrantableSkill[] = skillsData.skills ?? [];
      setGrantableSkills(skills);
      setGrants(grantsData.grants ?? []);
      setSelectedSlugs(new Set(skills.filter(s => s.grantable).map(s => s.slug)));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load skill access data.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const toggleSkill = (slug: string) => {
    setSelectedSlugs(prev => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug);
      else next.add(slug);
      return next;
    });
  };

  const selectAllSkills = () => {
    setSelectedSlugs(new Set(grantableOnly.map(s => s.slug)));
  };

  const grantSkills = async () => {
    const unique = Array.from(new Set(selectedEmployees.map(e => e.email)));
    if (unique.length === 0) {
      setAddError("Add at least one employee.");
      return;
    }
    if (justification.trim().length < 10) {
      setAddError("Justification must be at least 10 characters.");
      return;
    }
    const slugs = Array.from(selectedSlugs);
    if (slugs.length === 0) {
      setAddError("Select at least one skill to grant.");
      return;
    }

    setSubmitting(true);
    setAddError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/skills/grant`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_emails: unique,
          skill_slugs: slugs.length === grantableOnly.length ? undefined : slugs,
          grant_type: grantType,
          expires_in_days: grantType === "permanent" ? null : parseInt(expiresDays, 10) || 90,
          justification: justification.trim(),
        }),
      });
      const body = await res.json().catch(() => ({})) as {
        detail?: string | { message?: string; errors?: Array<{ email: string; skill_slug?: string; error: string }> };
        granted?: number;
        failed?: number;
        errors?: Array<{ email: string; skill_slug?: string; error: string }>;
      };
      if (!res.ok) {
        const detail = body.detail;
        const msg = typeof detail === "string"
          ? detail
          : detail?.message ?? `HTTP ${res.status}`;
        throw new Error(msg);
      }
      if (body.failed && body.failed > 0 && body.errors?.length) {
        const summary = body.errors
          .map(e => `${e.email}${e.skill_slug ? ` (${e.skill_slug})` : ""}: ${e.error}`)
          .join("; ");
        setAddError(`Granted ${body.granted ?? 0}, failed ${body.failed}: ${summary}`);
        await load();
        return;
      }
      setSelectedEmployees([]);
      setJustification("");
      setAdding(false);
      await load();
    } catch (e) {
      setAddError(e instanceof Error ? e.message : "Failed to grant skill access.");
    } finally {
      setSubmitting(false);
    }
  };

  const revokeGrant = async (grant: SkillAccessGrant) => {
    if (!window.confirm(`Revoke ${grant.skill_slug} access for ${grant.user_email}?`)) return;
    setActionGrantId(grant.id);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/skills/grants/${grant.id}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "Revoked by agent owner" }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Failed to revoke skill grant.");
    } finally {
      setActionGrantId(null);
    }
  };

  const statusBadge = (status: string) => {
    const styles: Record<string, string> = {
      approved: "bg-emerald-50 text-emerald-700 border-emerald-200",
      pending: "bg-amber-50 text-amber-700 border-amber-200",
    };
    return (
      <span className={`inline-flex text-[11px] font-medium border rounded-full px-2 py-0.5 ${styles[status] ?? "bg-slate-50 text-slate-500 border-slate-200"}`}>
        {status}
      </span>
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Skill Access</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Grant users access to the skills this agent uses so they can interact with it fully in Slack.
          </p>
        </div>
        {!adding && grantableOnly.length > 0 && (
          <button
            type="button"
            onClick={() => { setAdding(true); setAddError(null); setSelectedEmployees([]); }}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold shrink-0"
          >
            <UserPlus className="w-3.5 h-3.5" /> Grant Skills
          </button>
        )}
      </div>

      {adding && (
        <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
            <Zap className="w-3.5 h-3.5 text-amber-500" />
            Grant skill access
          </p>
          <EmployeePicker selected={selectedEmployees} onChange={setSelectedEmployees} disabled={submitting} />

          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-slate-700">Skills to grant</p>
              <button type="button" onClick={selectAllSkills} className="text-[11px] text-indigo-600 hover:underline">
                Select all
              </button>
            </div>
            <div className="flex flex-wrap gap-2">
              {grantableOnly.map(skill => (
                <label
                  key={skill.slug}
                  className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border cursor-pointer transition-colors ${
                    selectedSlugs.has(skill.slug)
                      ? "bg-amber-50 border-amber-300 text-amber-800"
                      : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={selectedSlugs.has(skill.slug)}
                    onChange={() => toggleSkill(skill.slug)}
                    className="rounded border-slate-300 text-amber-600 focus:ring-amber-200"
                  />
                  {skill.name || skill.slug}
                </label>
              ))}
            </div>
            {grantableSkills.some(s => !s.grantable) && (
              <p className="text-[11px] text-slate-400 mt-2">
                Some agent skills cannot be granted by you (blocked, critical-risk, or no access).
              </p>
            )}
          </div>

          <textarea
            value={justification}
            onChange={e => setJustification(e.target.value)}
            placeholder="Why do these users need access? (min 10 characters)"
            rows={2}
            maxLength={2000}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-200 focus:border-amber-400 placeholder:text-slate-400 resize-none"
          />

          <div className="flex flex-wrap items-center gap-3">
            <select
              value={grantType}
              onChange={e => setGrantType(e.target.value as typeof grantType)}
              className="text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-200"
            >
              <option value="time_limited">Time-limited</option>
              <option value="permanent">Permanent</option>
              <option value="restricted">Restricted</option>
            </select>
            {grantType !== "permanent" && (
              <>
                <label className="text-xs text-slate-600">Expires in (days)</label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresDays}
                  onChange={e => setExpiresDays(e.target.value)}
                  className="w-20 text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-200"
                />
              </>
            )}
          </div>

          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void grantSkills()}
              disabled={submitting || selectedEmployees.length === 0 || justification.trim().length < 10}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold bg-amber-600 text-white rounded-lg hover:bg-amber-700 disabled:opacity-50 transition-colors"
            >
              {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              Grant
            </button>
            <button
              type="button"
              onClick={() => { setAdding(false); setAddError(null); }}
              className="px-3 py-2 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
          </div>
          {addError && (
            <p className="text-[11px] text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" /> {addError}
            </p>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        </div>
      ) : grantableOnly.length === 0 && grants.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center border border-dashed border-slate-200 rounded-xl">
          <Zap className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">No grantable skills</p>
          <p className="text-xs text-slate-400 mt-1 max-w-xs">
            Add skills to this agent first, or contact an admin for critical-risk skills.
          </p>
        </div>
      ) : (
        <>
          {grants.length > 0 && (
            <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
              {grants.map(g => (
                <div key={g.id} className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors group">
                  <div className="w-8 h-8 rounded-full bg-amber-100 border border-amber-200 flex items-center justify-center shrink-0 text-amber-700 text-xs font-bold">
                    {(g.user_name ?? g.user_email).slice(0, 1).toUpperCase()}
                  </div>
                  <div className="flex-1 min-w-0">
                    <p className="text-xs font-semibold text-slate-900 truncate">
                      {g.user_name ?? g.user_email}
                      <span className="text-slate-400 font-normal ml-1">· {g.skill_name || g.skill_slug}</span>
                    </p>
                    <p className="text-xs text-slate-500 truncate">{g.user_email}</p>
                    {g.expires_at && g.status === "approved" && (
                      <p className="text-[11px] text-slate-400 mt-0.5">
                        Expires {new Date(g.expires_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2 shrink-0">
                    {statusBadge(g.status)}
                    {g.status === "approved" && (
                      <button
                        type="button"
                        onClick={() => void revokeGrant(g)}
                        disabled={actionGrantId === g.id}
                        className="opacity-0 group-hover:opacity-100 text-[11px] font-semibold px-2 py-1 text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50 transition-all"
                      >
                        {actionGrantId === g.id ? <Loader2 className="w-3 h-3 animate-spin" /> : "Revoke"}
                      </button>
                    )}
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Roles tab ──────────────────────────────────────────────────────────────────

interface AgentRoleRecord {
  id: string;
  agent_id: string;
  name: string;
  description: string | null;
  skill_slugs: string[];
  created_by_email: string | null;
  archived_at: string | null;
  full_count: number;
  partial_count: number;
}

interface RoleMemberGrant {
  skill_slug: string;
  grant_id: string;
  expires_at: string | null;
}

interface RoleMember {
  email: string;
  name: string | null;
  grants: RoleMemberGrant[];
  status: "full" | "partial";
  missing_skill_slugs: string[];
  has_agent_access: boolean;
}

function RolesTab({ agentId }: { agentId: string }) {
  const [roles, setRoles] = useState<AgentRoleRecord[]>([]);
  const [grantableSkills, setGrantableSkills] = useState<{ slug: string; name: string; grantable: boolean }[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [formMode, setFormMode] = useState<"create" | "edit" | null>(null);
  const [editingRoleId, setEditingRoleId] = useState<string | null>(null);
  const [formName, setFormName] = useState("");
  const [formDescription, setFormDescription] = useState("");
  const [formSlugs, setFormSlugs] = useState<Set<string>>(new Set());
  const [formError, setFormError] = useState<string | null>(null);
  const [formSubmitting, setFormSubmitting] = useState(false);

  const [expandedRoleId, setExpandedRoleId] = useState<string | null>(null);
  const [members, setMembers] = useState<Record<string, RoleMember[]>>({});
  const [membersLoading, setMembersLoading] = useState<string | null>(null);

  const [assigningRoleId, setAssigningRoleId] = useState<string | null>(null);
  const [assignEmployees, setAssignEmployees] = useState<EmployeeResult[]>([]);
  const [assignJustification, setAssignJustification] = useState("");
  const [assignGrantType, setAssignGrantType] = useState<"time_limited" | "permanent" | "restricted">("time_limited");
  const [assignExpiresDays, setAssignExpiresDays] = useState("90");
  const [assignError, setAssignError] = useState<string | null>(null);
  const [assignSubmitting, setAssignSubmitting] = useState(false);

  const [actionBusyId, setActionBusyId] = useState<string | null>(null);

  const grantableOnly = grantableSkills.filter(s => s.grantable);
  const skillName = (slug: string) => grantableSkills.find(s => s.slug === slug)?.name || slug;

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [rolesRes, skillsRes] = await Promise.all([
        apiFetch(`/api/agents/${agentId}/roles`),
        apiFetch(`/api/agents/${agentId}/skills/grantable`),
      ]);
      if (!rolesRes.ok) throw new Error(`HTTP ${rolesRes.status}`);
      if (!skillsRes.ok) throw new Error(`HTTP ${skillsRes.status}`);
      const rolesData = await rolesRes.json();
      const skillsData = await skillsRes.json();
      setRoles(rolesData.roles ?? []);
      setGrantableSkills(skillsData.skills ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not load roles.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void load(); }, [agentId]);

  const loadMembers = async (roleId: string) => {
    setMembersLoading(roleId);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/roles/${roleId}/members`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setMembers(prev => ({ ...prev, [roleId]: data.members ?? [] }));
    } catch {
      setError("Could not load role members.");
    } finally {
      setMembersLoading(null);
    }
  };

  const toggleExpand = (roleId: string) => {
    if (expandedRoleId === roleId) {
      setExpandedRoleId(null);
      return;
    }
    setExpandedRoleId(roleId);
    if (!members[roleId]) void loadMembers(roleId);
  };

  const openCreateForm = () => {
    setFormMode("create");
    setEditingRoleId(null);
    setFormName("");
    setFormDescription("");
    setFormSlugs(new Set());
    setFormError(null);
  };

  const openEditForm = (role: AgentRoleRecord) => {
    setFormMode("edit");
    setEditingRoleId(role.id);
    setFormName(role.name);
    setFormDescription(role.description ?? "");
    setFormSlugs(new Set(role.skill_slugs));
    setFormError(null);
  };

  const closeForm = () => {
    setFormMode(null);
    setEditingRoleId(null);
    setFormError(null);
  };

  const toggleFormSlug = (slug: string) => {
    setFormSlugs(prev => {
      const next = new Set(prev);
      if (next.has(slug)) next.delete(slug); else next.add(slug);
      return next;
    });
  };

  const submitForm = async () => {
    const name = formName.trim();
    if (!name) { setFormError("Name is required."); return; }
    if (formSlugs.size === 0) { setFormError("Select at least one skill for this role."); return; }

    setFormSubmitting(true);
    setFormError(null);
    try {
      const body = {
        name,
        description: formDescription.trim() || null,
        skill_slugs: Array.from(formSlugs),
      };
      const url =
        formMode === "edit" && editingRoleId
          ? `/api/agents/${agentId}/roles/${editingRoleId}`
          : `/api/agents/${agentId}/roles`;
      const res = await apiFetch(url, {
        method: formMode === "edit" ? "PUT" : "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error((detail as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      closeForm();
      await load();
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "Failed to save role.");
    } finally {
      setFormSubmitting(false);
    }
  };

  const deleteRole = async (role: AgentRoleRecord) => {
    if (!window.confirm(
      `Delete role "${role.name}"? This revokes the skill access and agent access it granted for everyone who has it. Access they hold directly, or via another role, is not affected.`
    )) return;
    setActionBusyId(role.id);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/roles/${role.id}`, { method: "DELETE" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load();
    } catch {
      setError("Failed to delete role.");
    } finally {
      setActionBusyId(null);
    }
  };

  const openAssignForm = (roleId: string) => {
    setAssigningRoleId(roleId);
    setAssignEmployees([]);
    setAssignJustification("");
    setAssignGrantType("time_limited");
    setAssignExpiresDays("90");
    setAssignError(null);
  };

  const submitAssign = async (roleId: string) => {
    const unique = Array.from(new Set(assignEmployees.map(e => e.email)));
    if (unique.length === 0) { setAssignError("Add at least one employee."); return; }
    if (assignJustification.trim().length < 10) { setAssignError("Justification must be at least 10 characters."); return; }

    setAssignSubmitting(true);
    setAssignError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/roles/${roleId}/assign`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          user_emails: unique,
          grant_type: assignGrantType,
          expires_in_days: assignGrantType === "permanent" ? null : parseInt(assignExpiresDays, 10) || 90,
          justification: assignJustification.trim(),
        }),
      });
      const body = await res.json().catch(() => ({})) as {
        detail?: string | { message?: string; errors?: Array<{ email: string; error: string }> };
        granted?: number; failed?: number; errors?: Array<{ email: string; error: string }>;
      };
      if (!res.ok) {
        const detail = body.detail;
        const msg = typeof detail === "string" ? detail : detail?.message ?? `HTTP ${res.status}`;
        throw new Error(msg);
      }
      if (body.failed && body.failed > 0 && body.errors?.length) {
        const summary = body.errors.map(e => `${e.email}: ${e.error}`).join("; ");
        setAssignError(`Granted ${body.granted ?? 0}, failed ${body.failed}: ${summary}`);
        await load();
        if (members[roleId]) await loadMembers(roleId);
        return;
      }
      setAssigningRoleId(null);
      await load();
      if (members[roleId]) await loadMembers(roleId);
    } catch (e) {
      setAssignError(e instanceof Error ? e.message : "Failed to assign role.");
    } finally {
      setAssignSubmitting(false);
    }
  };

  const revokeMember = async (roleId: string, member: RoleMember) => {
    if (!window.confirm(`Revoke this role from ${member.email}?`)) return;
    setActionBusyId(`${roleId}:${member.email}`);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/roles/${roleId}/members/${encodeURIComponent(member.email)}/revoke`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({}) }
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({})) as { detail?: string };
        throw new Error(body.detail || `HTTP ${res.status}`);
      }
      await load();
      await loadMembers(roleId);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to revoke role from user.");
    } finally {
      setActionBusyId(null);
    }
  };

  const memberStatusBadge = (status: "full" | "partial") => (
    <span
      className={`inline-flex text-[11px] font-medium border rounded-full px-2 py-0.5 ${
        status === "full"
          ? "bg-emerald-50 text-emerald-700 border-emerald-200"
          : "bg-amber-50 text-amber-700 border-amber-200"
      }`}
    >
      {status === "full" ? "Full" : "Partial"}
    </span>
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Roles</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Named bundles of skills (e.g. "CRI User") you can assign to people in one action, for cleaner auditing.
          </p>
        </div>
        {formMode === null && (
          <button
            type="button"
            onClick={openCreateForm}
            className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold shrink-0"
          >
            <Plus className="w-3.5 h-3.5" /> New Role
          </button>
        )}
      </div>

      {formMode !== null && (
        <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
          <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
            <Tag className="w-3.5 h-3.5 text-indigo-500" /> {formMode === "edit" ? "Edit role" : "New role"}
          </p>
          <input
            autoFocus
            value={formName}
            onChange={e => setFormName(e.target.value)}
            placeholder="e.g. CRI User"
            maxLength={120}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 placeholder:text-slate-400"
          />
          <textarea
            value={formDescription}
            onChange={e => setFormDescription(e.target.value)}
            placeholder="What is this role for? (optional)"
            rows={2}
            maxLength={2000}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 focus:border-indigo-400 placeholder:text-slate-400 resize-none"
          />
          <div>
            <p className="text-xs font-semibold text-slate-700 mb-2">Skills in this role</p>
            <div className="flex flex-wrap gap-2">
              {grantableOnly.map(skill => (
                <label
                  key={skill.slug}
                  className={`inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border cursor-pointer transition-colors ${
                    formSlugs.has(skill.slug)
                      ? "bg-indigo-50 border-indigo-300 text-indigo-800"
                      : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"
                  }`}
                >
                  <input
                    type="checkbox"
                    checked={formSlugs.has(skill.slug)}
                    onChange={() => toggleFormSlug(skill.slug)}
                    className="rounded border-slate-300 text-indigo-600 focus:ring-indigo-200"
                  />
                  {skill.name || skill.slug}
                </label>
              ))}
            </div>
            {grantableOnly.length === 0 && (
              <p className="text-[11px] text-slate-400 mt-2">This agent has no grantable skills to bundle into a role.</p>
            )}
          </div>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => void submitForm()}
              disabled={formSubmitting || !formName.trim()}
              className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
            >
              {formSubmitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />}
              {formMode === "edit" ? "Save" : "Create"}
            </button>
            <button
              type="button"
              onClick={closeForm}
              className="px-3 py-2 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
            >
              Cancel
            </button>
          </div>
          {formError && (
            <p className="text-[11px] text-red-600 flex items-center gap-1">
              <AlertCircle className="w-3 h-3 shrink-0" /> {formError}
            </p>
          )}
        </div>
      )}

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2 flex items-center gap-2">
          <AlertCircle className="w-3.5 h-3.5 shrink-0" /> {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10"><Loader2 className="w-4 h-4 animate-spin text-slate-400" /></div>
      ) : roles.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-12 text-center border border-dashed border-slate-200 rounded-xl">
          <Tag className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">No roles yet</p>
          <p className="text-xs text-slate-400 mt-1 max-w-xs">
            Click "New Role" to bundle a set of skills under a reusable name.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
          {roles.map(role => (
            <div key={role.id} className="bg-white">
              <div className="flex items-center gap-3 px-4 py-3 hover:bg-slate-50 transition-colors group">
                <button
                  type="button"
                  onClick={() => toggleExpand(role.id)}
                  className="flex items-center gap-2 flex-1 min-w-0 text-left"
                >
                  <ChevronDown
                    className={`w-3.5 h-3.5 text-slate-400 shrink-0 transition-transform ${expandedRoleId === role.id ? "" : "-rotate-90"}`}
                  />
                  <div className="min-w-0">
                    <p className="text-xs font-semibold text-slate-900 truncate">{role.name}</p>
                    {role.description && (
                      <p className="text-xs text-slate-500 truncate">{role.description}</p>
                    )}
                    <div className="flex flex-wrap gap-1 mt-1">
                      {role.skill_slugs.map(slug => (
                        <span key={slug} className="text-[10px] px-1.5 py-0.5 bg-slate-100 text-slate-500 rounded">
                          {skillName(slug)}
                        </span>
                      ))}
                    </div>
                  </div>
                </button>
                <div className="flex items-center gap-2 shrink-0">
                  {role.full_count > 0 && (
                    <span className="inline-flex text-[11px] font-medium border rounded-full px-2 py-0.5 bg-emerald-50 text-emerald-700 border-emerald-200">
                      {role.full_count} full
                    </span>
                  )}
                  {role.partial_count > 0 && (
                    <span className="inline-flex text-[11px] font-medium border rounded-full px-2 py-0.5 bg-amber-50 text-amber-700 border-amber-200">
                      {role.partial_count} partial
                    </span>
                  )}
                  <button
                    type="button"
                    onClick={() => openAssignForm(role.id)}
                    className="text-[11px] font-semibold px-2 py-1 bg-slate-900 text-white rounded-lg hover:bg-slate-700"
                  >
                    Assign
                  </button>
                  <>
                    <button
                      type="button"
                      onClick={() => openEditForm(role)}
                      className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-slate-700 transition-all"
                      title="Edit role"
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void deleteRole(role)}
                      disabled={actionBusyId === role.id}
                      className="opacity-0 group-hover:opacity-100 text-slate-400 hover:text-red-500 transition-all disabled:opacity-50"
                      title="Delete role"
                    >
                      {actionBusyId === role.id ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Trash2 className="w-3.5 h-3.5" />}
                    </button>
                  </>
                </div>
              </div>

              {assigningRoleId === role.id && (
                <div className="mx-4 mb-3 border border-slate-200 rounded-xl bg-slate-50 p-4 space-y-3">
                  <p className="text-xs font-semibold text-slate-700 flex items-center gap-1.5">
                    <UserPlus className="w-3.5 h-3.5 text-emerald-500" /> Assign "{role.name}"
                  </p>
                  <EmployeePicker selected={assignEmployees} onChange={setAssignEmployees} disabled={assignSubmitting} />
                  <textarea
                    value={assignJustification}
                    onChange={e => setAssignJustification(e.target.value)}
                    placeholder="Why do these users need this role? (min 10 characters)"
                    rows={2}
                    maxLength={2000}
                    className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-200 focus:border-emerald-400 placeholder:text-slate-400 resize-none bg-white"
                  />
                  <div className="flex flex-wrap items-center gap-3">
                    <select
                      value={assignGrantType}
                      onChange={e => setAssignGrantType(e.target.value as typeof assignGrantType)}
                      className="text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-200 bg-white"
                    >
                      <option value="time_limited">Time-limited</option>
                      <option value="permanent">Permanent</option>
                      <option value="restricted">Restricted</option>
                    </select>
                    {assignGrantType !== "permanent" && (
                      <>
                        <label className="text-xs text-slate-600">Expires in (days)</label>
                        <input
                          type="number"
                          min={1}
                          max={365}
                          value={assignExpiresDays}
                          onChange={e => setAssignExpiresDays(e.target.value)}
                          className="w-20 text-sm px-3 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-emerald-200 bg-white"
                        />
                      </>
                    )}
                  </div>
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => void submitAssign(role.id)}
                      disabled={assignSubmitting || assignEmployees.length === 0 || assignJustification.trim().length < 10}
                      className="flex items-center gap-1.5 px-4 py-2 text-xs font-semibold bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-50 transition-colors"
                    >
                      {assignSubmitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plus className="w-3.5 h-3.5" />} Assign
                    </button>
                    <button
                      type="button"
                      onClick={() => setAssigningRoleId(null)}
                      className="px-3 py-2 text-xs text-slate-500 border border-slate-200 rounded-lg hover:bg-slate-100 transition-colors"
                    >
                      Cancel
                    </button>
                  </div>
                  {assignError && (
                    <p className="text-[11px] text-red-600 flex items-center gap-1">
                      <AlertCircle className="w-3 h-3 shrink-0" /> {assignError}
                    </p>
                  )}
                </div>
              )}

              {expandedRoleId === role.id && (
                <div className="px-4 pb-3">
                  {membersLoading === role.id ? (
                    <div className="flex items-center justify-center py-6">
                      <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                    </div>
                  ) : (members[role.id]?.length ?? 0) === 0 ? (
                    <p className="text-xs text-slate-400 py-2">No one holds this role yet.</p>
                  ) : (
                    <div className="divide-y divide-slate-100 border border-slate-200 rounded-lg overflow-hidden">
                      {members[role.id].map(member => (
                        <div key={member.email} className="flex items-center gap-3 px-3 py-2 bg-white hover:bg-slate-50 transition-colors group/member">
                          <div className="flex-1 min-w-0">
                            <p className="text-xs font-semibold text-slate-900 truncate">{member.name ?? member.email}</p>
                            <p className="text-[11px] text-slate-500 truncate">{member.email}</p>
                            {member.status === "partial" && (
                              <p className="text-[11px] text-amber-600 mt-0.5">
                                Missing:{" "}
                                {[
                                  ...member.missing_skill_slugs.map(skillName),
                                  ...(member.has_agent_access ? [] : ["agent access"]),
                                ].join(", ")}
                              </p>
                            )}
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            {memberStatusBadge(member.status)}
                            <button
                              type="button"
                              onClick={() => void revokeMember(role.id, member)}
                              disabled={actionBusyId === `${role.id}:${member.email}`}
                              className="opacity-0 group-hover/member:opacity-100 text-[11px] font-semibold px-2 py-1 text-red-600 border border-red-200 rounded-lg hover:bg-red-50 disabled:opacity-50 transition-all"
                            >
                              {actionBusyId === `${role.id}:${member.email}` ? <Loader2 className="w-3 h-3 animate-spin" /> : "Revoke"}
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Slack App Grants tab ──────────────────────────────────────────────────────

interface SlackAppGrant {
  id: string;
  agent_id: string;
  slack_user_id: string;
  app_name: string | null;
  granted_by_email: string | null;
  created_at: string | null;
  image_192: string | null;
  is_app_user: boolean | null;
  app_id: string | null;
}

interface SlackAppRegistryEntry {
  user_id: string;
  name: string;
  display_name: string | null;
  real_name: string | null;
  image_192: string | null;
  is_app_user: boolean;
  registered: boolean;
}

// ── Guardrails ────────────────────────────────────────────────────────────────

interface GuardrailPreset {
  id: string;
  label: string;
  description: string;
  type: "preset";
  enabled: boolean;
}

interface GuardrailCustom {
  id: string;
  type: "custom";
  text: string;
  enabled: boolean;
}

type GuardrailSavePayload =
  | { id: string; type: "preset"; enabled: boolean }
  | { id: string; type: "custom"; text: string; enabled: boolean };

function GuardrailsTab({
  agentId,
  readonly,
}: {
  agentId: string;
  readonly: boolean;
}) {
  const [presets, setPresets] = useState<GuardrailPreset[]>([]);
  const [custom, setCustom] = useState<GuardrailCustom[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [newRule, setNewRule] = useState("");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/guardrails`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setPresets(data.presets ?? []);
      setCustom(data.custom ?? []);
    } catch {
      setError("Could not load guardrails.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, [agentId]);

  const togglePreset = (id: string) => {
    if (readonly) return;
    setPresets(prev =>
      prev.map(p => (p.id === id ? { ...p, enabled: !p.enabled } : p))
    );
  };

  const addCustomRule = () => {
    const text = newRule.trim();
    if (!text || text.length > 500) return;
    setCustom(prev => [
      ...prev,
      { id: `custom_${Date.now()}`, type: "custom", text, enabled: true },
    ]);
    setNewRule("");
  };

  const removeCustomRule = (id: string) => {
    setCustom(prev => prev.filter(c => c.id !== id));
  };

  const save = async () => {
    setSaving(true);
    setError(null);
    setSuccess(false);
    try {
      const payload: GuardrailSavePayload[] = [
        ...presets.map(p => ({ id: p.id, type: "preset" as const, enabled: p.enabled })),
        ...custom,
      ];
      const res = await apiFetch(`/api/agents/${agentId}/guardrails`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ guardrails: payload }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      setSuccess(true);
      setTimeout(() => setSuccess(false), 3000);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to save guardrails.");
    } finally {
      setSaving(false);
    }
  };

  const activeCount = [...presets.filter(p => p.enabled), ...custom.filter(c => c.enabled)].length;

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-slate-400">
        <Loader2 className="w-5 h-5 animate-spin mr-2" />
        Loading guardrails…
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-start justify-between gap-4">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Guardrails</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Rules injected into this agent's system prompt on every invocation.
            {activeCount > 0 && (
              <span className="ml-1 inline-flex items-center gap-1 bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5 text-[10px] font-semibold">
                <ShieldCheck className="w-3 h-3" />
                {activeCount} active
              </span>
            )}
          </p>
        </div>
        {!readonly && (
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700 disabled:opacity-50 transition-colors"
          >
            {saving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            {saving ? "Saving…" : "Save"}
          </button>
        )}
      </div>

      {error && (
        <div className="flex items-center gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle className="w-4 h-4 flex-shrink-0" />
          {error}
        </div>
      )}
      {success && (
        <div className="flex items-center gap-2 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
          <CheckCircle2 className="w-4 h-4 flex-shrink-0" />
          Guardrails saved successfully.
        </div>
      )}

      {/* Preset rules */}
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Preset rules</p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {presets.map(preset => (
            <button
              key={preset.id}
              type="button"
              onClick={() => togglePreset(preset.id)}
              disabled={readonly}
              className={`text-left p-3.5 rounded-xl border transition-all ${
                preset.enabled
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 bg-white text-slate-700 hover:border-slate-300"
              } ${readonly ? "cursor-default" : "cursor-pointer"}`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex-1 min-w-0">
                  <p className={`text-xs font-semibold ${preset.enabled ? "text-white" : "text-slate-900"}`}>
                    {preset.label}
                  </p>
                  <p className={`text-[11px] mt-0.5 leading-relaxed ${preset.enabled ? "text-slate-300" : "text-slate-500"}`}>
                    {preset.description}
                  </p>
                </div>
                <div
                  className={`flex-shrink-0 w-8 rounded-full border transition-colors mt-0.5 ${
                    preset.enabled
                      ? "bg-emerald-400 border-emerald-400"
                      : "bg-slate-200 border-slate-300"
                  }`}
                  style={{ height: "18px" }}
                >
                  <div
                    className={`w-3.5 h-3.5 rounded-full bg-white shadow-sm transform transition-transform mt-[1px] ${
                      preset.enabled ? "translate-x-[18px]" : "translate-x-[1px]"
                    }`}
                  />
                </div>
              </div>
            </button>
          ))}
        </div>
      </div>

      {/* Custom rules */}
      <div>
        <p className="text-xs font-semibold text-slate-500 uppercase tracking-wider mb-3">Custom rules</p>
        {custom.length === 0 && readonly && (
          <p className="text-xs text-slate-400 italic">No custom rules configured.</p>
        )}
        <div className="space-y-2">
          {custom.map(rule => (
            <div
              key={rule.id}
              className="flex items-start gap-2 p-3 bg-slate-50 border border-slate-200 rounded-lg"
            >
              <ShieldCheck className="w-3.5 h-3.5 text-slate-400 flex-shrink-0 mt-0.5" />
              <p className="flex-1 text-xs text-slate-700">{rule.text}</p>
              {!readonly && (
                <button
                  type="button"
                  onClick={() => removeCustomRule(rule.id)}
                  className="flex-shrink-0 text-slate-400 hover:text-red-500 transition-colors"
                  aria-label="Remove rule"
                >
                  <X className="w-3.5 h-3.5" />
                </button>
              )}
            </div>
          ))}
        </div>
        {!readonly && (
          <div className="flex gap-2 mt-2">
            <input
              type="text"
              value={newRule}
              onChange={e => setNewRule(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); addCustomRule(); }}}
              placeholder="Add a custom rule… (max 500 chars)"
              maxLength={500}
              className="flex-1 text-xs border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-slate-300 placeholder:text-slate-400"
            />
            <button
              type="button"
              onClick={addCustomRule}
              disabled={!newRule.trim()}
              className="flex items-center gap-1 px-3 py-2 text-xs font-semibold bg-slate-100 text-slate-700 rounded-lg hover:bg-slate-200 disabled:opacity-40 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              Add
            </button>
          </div>
        )}
      </div>
    </div>
  );
}

// ─────────────────────────────────────────────────────────────────────────────

function SlackAppsGrantTab({ agentId }: { agentId: string }) {
  const [grants, setGrants] = useState<SlackAppGrant[]>([]);
  const [registry, setRegistry] = useState<SlackAppRegistryEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [removingId, setRemovingId] = useState<string | null>(null);

  const loadGrants = async () => {
    setLoading(true);
    setError(null);
    try {
      const [gRes, rRes] = await Promise.all([
        apiFetch(`/api/agents/${agentId}/slack-app-grants`),
        apiFetch("/api/slack-apps"),
      ]);
      if (!gRes.ok) throw new Error(`HTTP ${gRes.status}`);
      setGrants(await gRes.json());
      if (rRes.ok) setRegistry(await rRes.json());
    } catch {
      setError("Could not load authorized Slack apps.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadGrants(); }, [agentId]);

  const grantedIds = new Set(grants.map(g => g.slack_user_id));
  const available = registry.filter(r => !grantedIds.has(r.user_id));

  const filteredAvailable = available.filter(r => {
    const q = search.toLowerCase();
    return (
      r.name.toLowerCase().includes(q) ||
      (r.real_name ?? "").toLowerCase().includes(q) ||
      (r.display_name ?? "").toLowerCase().includes(q)
    );
  });

  const addGrant = async (entry: SlackAppRegistryEntry) => {
    setAdding(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/slack-app-grants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slack_user_id: entry.user_id,
          app_name: entry.real_name || entry.display_name || entry.name,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      setPickerOpen(false);
      setSearch("");
      await loadGrants();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add app.");
    } finally {
      setAdding(false);
    }
  };

  const removeGrant = async (slackUserId: string) => {
    setRemovingId(slackUserId);
    setError(null);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/slack-app-grants/${slackUserId}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadGrants();
    } catch {
      setError("Failed to remove app.");
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className="space-y-8">
    <div className="space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">Authorized Slack Apps</h3>
          <p className="text-xs text-slate-500 mt-0.5">
            Slack bots listed here can invoke this agent with full skill access.
          </p>
        </div>
        <button
          onClick={() => { setPickerOpen(v => !v); setSearch(""); }}
          className="flex items-center gap-1.5 text-xs px-3 py-1.5 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold"
        >
          <Plus className="w-3.5 h-3.5" /> Add App
        </button>
      </div>

      {/* App picker dropdown */}
      {pickerOpen && (
        <div className="border border-slate-200 rounded-xl bg-white shadow-sm overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-2 border-b border-slate-100">
            <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
            <input
              autoFocus
              type="text"
              value={search}
              onChange={e => setSearch(e.target.value)}
              placeholder="Search Slack apps…"
              className="flex-1 bg-transparent text-sm text-slate-700 placeholder:text-slate-400 focus:outline-none"
            />
          </div>
          <div className="max-h-56 overflow-y-auto divide-y divide-slate-50">
            {filteredAvailable.length === 0 ? (
              <p className="px-4 py-3 text-xs text-slate-400 text-center">
                {registry.length === 0
                  ? "Sync the Slack App Registry first to see available apps."
                  : "No unregistered apps found."}
              </p>
            ) : (
              filteredAvailable.map(entry => {
                const label = entry.real_name || entry.display_name || entry.name;
                return (
                  <button
                    key={entry.user_id}
                    onClick={() => addGrant(entry)}
                    disabled={adding}
                    className="w-full flex items-center gap-3 px-4 py-2.5 hover:bg-slate-50 transition-colors text-left disabled:opacity-50"
                  >
                    <div className="w-7 h-7 rounded-lg border border-slate-200 bg-slate-100 overflow-hidden shrink-0 flex items-center justify-center">
                      {entry.image_192 ? (
                        <img src={entry.image_192} alt={label} className="w-full h-full object-cover" />
                      ) : (
                        <Bot className="w-3.5 h-3.5 text-slate-400" />
                      )}
                    </div>
                    <div className="min-w-0">
                      <p className="text-xs font-semibold text-slate-900 truncate">{label}</p>
                      {label !== entry.name && (
                        <p className="text-[11px] text-slate-400 truncate">@{entry.name}</p>
                      )}
                    </div>
                    {entry.registered && (
                      <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 ml-auto shrink-0" />
                    )}
                  </button>
                );
              })
            )}
          </div>
        </div>
      )}

      {/* Error */}
      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2">
          {error}
        </div>
      )}

      {/* Grants list */}
      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        </div>
      ) : grants.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center border border-dashed border-slate-200 rounded-xl">
          <Bot className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">No Slack apps authorized</p>
          <p className="text-xs text-slate-400 mt-1">
            Click "Add App" to allow a Slack bot to invoke this agent.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
          {grants.map(g => {
            const label = g.app_name || g.slack_user_id;
            return (
              <div key={g.slack_user_id} className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors">
                <div className="w-8 h-8 rounded-xl border border-slate-200 bg-slate-100 overflow-hidden shrink-0 flex items-center justify-center">
                  {g.image_192 ? (
                    <img src={g.image_192} alt={label} className="w-full h-full object-cover" />
                  ) : (
                    <Bot className="w-4 h-4 text-slate-400" />
                  )}
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-slate-900 truncate">{label}</p>
                  <p className="text-[11px] font-mono text-slate-400 truncate">{g.slack_user_id}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[11px] text-slate-400 hidden sm:block">
                    {g.granted_by_email ? `by ${g.granted_by_email}` : ""}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-full px-2 py-0.5">
                    <CheckCircle2 className="w-3 h-3" /> Full access
                  </span>
                  <button
                    onClick={() => removeGrant(g.slack_user_id)}
                    disabled={removingId === g.slack_user_id}
                    className="text-slate-400 hover:text-red-500 transition-colors disabled:opacity-50"
                    title="Remove"
                  >
                    {removingId === g.slack_user_id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <X className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
    <SlackWorkflowsGrantSection agentId={agentId} />
    </div>
  );
}

interface SlackWorkflowGrant {
  id: string;
  agent_id: string;
  workflow_id: string;
  label: string | null;
  granted_by_email: string | null;
  created_at: string | null;
}

function SlackWorkflowsGrantSection({ agentId }: { agentId: string }) {
  const [grants, setGrants] = useState<SlackWorkflowGrant[]>([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [workflowId, setWorkflowId] = useState("");
  const [label, setLabel] = useState("");
  const [removingId, setRemovingId] = useState<string | null>(null);

  const loadGrants = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/slack-workflow-grants`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setGrants(await res.json());
    } catch {
      setError("Could not load authorized workflows.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { loadGrants(); }, [agentId]);

  const addGrant = async () => {
    const id = workflowId.trim();
    if (!id) return;
    setAdding(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/slack-workflow-grants`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          workflow_id: id,
          label: label.trim() || null,
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error((body as { detail?: string }).detail || `HTTP ${res.status}`);
      }
      setWorkflowId("");
      setLabel("");
      await loadGrants();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to add workflow.");
    } finally {
      setAdding(false);
    }
  };

  const removeGrant = async (id: string) => {
    setRemovingId(id);
    setError(null);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/slack-workflow-grants/${encodeURIComponent(id)}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await loadGrants();
    } catch {
      setError("Failed to remove workflow.");
    } finally {
      setRemovingId(null);
    }
  };

  return (
    <div className="space-y-4 pt-2 border-t border-slate-200">
      <div>
        <h3 className="text-sm font-semibold text-slate-900">Authorized Slack Workflows</h3>
        <p className="text-xs text-slate-500 mt-0.5">
          This allows Slack Workflow Builder workflows to interact with this bot.
          Add a workflow ID so those workflows can @mention the agent and receive a response.
          You can paste the ID from the workflow&apos;s Workflow Builder URL.
        </p>
      </div>

      <div className="border border-slate-200 rounded-xl bg-white p-4 space-y-3">
        <div className="grid gap-3 sm:grid-cols-[1fr_1fr_auto]">
          <div>
            <label className="block text-[11px] font-semibold text-slate-500 mb-1">
              Workflow ID
            </label>
            <input
              type="text"
              value={workflowId}
              onChange={e => setWorkflowId(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") addGrant(); }}
              placeholder="Wf0123ABCD"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
            />
          </div>
          <div>
            <label className="block text-[11px] font-semibold text-slate-500 mb-1">
              Label <span className="font-normal">(optional)</span>
            </label>
            <input
              type="text"
              value={label}
              onChange={e => setLabel(e.target.value)}
              onKeyDown={e => { if (e.key === "Enter") addGrant(); }}
              placeholder="e.g. Access provisioning"
              className="w-full rounded-lg border border-slate-200 px-3 py-2 text-sm text-slate-800 placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900/10"
            />
          </div>
          <div className="flex items-end">
            <button
              type="button"
              onClick={addGrant}
              disabled={adding || !workflowId.trim()}
              className="w-full sm:w-auto flex items-center justify-center gap-1.5 text-xs px-3 py-2 bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors font-semibold disabled:opacity-50"
            >
              {adding ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Plus className="w-3.5 h-3.5" />
              )}
              Add
            </button>
          </div>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-xl px-4 py-2">
          {error}
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-10">
          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
        </div>
      ) : grants.length === 0 ? (
        <div className="flex flex-col items-center justify-center py-10 text-center border border-dashed border-slate-200 rounded-xl">
          <GitMerge className="w-8 h-8 text-slate-300 mb-2" />
          <p className="text-sm font-semibold text-slate-700">No workflows authorized</p>
          <p className="text-xs text-slate-400 mt-1 max-w-sm">
            Add a workflow ID so Slack workflows can interact with this bot.
          </p>
        </div>
      ) : (
        <div className="divide-y divide-slate-100 border border-slate-200 rounded-xl overflow-hidden">
          {grants.map(g => {
            const title = g.label || g.workflow_id;
            return (
              <div
                key={g.workflow_id}
                className="flex items-center gap-3 px-4 py-3 bg-white hover:bg-slate-50 transition-colors"
              >
                <div className="w-8 h-8 rounded-xl border border-slate-200 bg-slate-100 shrink-0 flex items-center justify-center">
                  <GitMerge className="w-4 h-4 text-slate-400" />
                </div>
                <div className="flex-1 min-w-0">
                  <p className="text-xs font-semibold text-slate-900 truncate">{title}</p>
                  <p className="text-[11px] font-mono text-slate-400 truncate">{g.workflow_id}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="text-[11px] text-slate-400 hidden sm:block">
                    {g.granted_by_email ? `by ${g.granted_by_email}` : ""}
                  </span>
                  <span className="inline-flex items-center gap-1 text-[11px] font-semibold bg-emerald-50 border border-emerald-200 text-emerald-700 rounded-full px-2 py-0.5">
                    <CheckCircle2 className="w-3 h-3" /> Full access
                  </span>
                  <button
                    type="button"
                    onClick={() => removeGrant(g.workflow_id)}
                    disabled={removingId === g.workflow_id}
                    className="text-slate-400 hover:text-red-500 transition-colors disabled:opacity-50"
                    title="Remove"
                  >
                    {removingId === g.workflow_id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <X className="w-3.5 h-3.5" />
                    )}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ── Interaction log tab ───────────────────────────────────────────────────

const INTERACTIONS_PAGE_SIZE = 25;

function InteractionLogTab({
  agentId,
  initialInteractionId,
  onTotalChange,
  onFailureTotalChange,
}: {
  agentId: string;
  initialInteractionId: string | null;
  onTotalChange: (total: number) => void;
  onFailureTotalChange: (total: number) => void;
}) {
  const [items, setItems] = useState<AgentInteractionRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");
  const [triggerFilter, setTriggerFilter] = useState("");
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [selectedItem, setSelectedItem] = useState<AgentInteractionRecord | null>(null);
  const [highlightId, setHighlightId] = useState<string | null>(initialInteractionId);
  const deepLinkRowRef = useRef<HTMLTableRowElement | null>(null);

  useEffect(() => {
    if (!initialInteractionId) return;
    setHighlightId(initialInteractionId);
  }, [initialInteractionId]);

  useEffect(() => {
    if (!initialInteractionId || loading || items.length === 0) return;
    const match = items.find(row => row.id === initialInteractionId);
    if (match) {
      setSelectedItem(match);
    }
  }, [initialInteractionId, loading, items]);

  useEffect(() => {
    if (!highlightId || !deepLinkRowRef.current) return;
    const timer = window.setTimeout(() => {
      deepLinkRowRef.current?.scrollIntoView({ behavior: "smooth", block: "center" });
    }, 300);
    return () => window.clearTimeout(timer);
  }, [highlightId, loading, items]);

  useEffect(() => {
    const timer = window.setTimeout(() => setQuery(searchInput.trim()), 300);
    return () => window.clearTimeout(timer);
  }, [searchInput]);

  useEffect(() => {
    setPage(1);
  }, [query, triggerFilter, failuresOnly]);

  useEffect(() => {
    let cancelled = false;
    const loadFailureTotal = async () => {
      try {
        const res = await apiFetch(
          `/api/agents/${agentId}/interactions?failures_only=true&page=1&limit=1`
        );
        if (!res.ok) return;
        const payload: AgentInteractionListResponse = await res.json();
        if (!cancelled) {
          onFailureTotalChange(typeof payload.total === "number" ? payload.total : 0);
        }
      } catch {
        if (!cancelled) onFailureTotalChange(0);
      }
    };
    void loadFailureTotal();
    return () => {
      cancelled = true;
    };
  }, [agentId, onFailureTotalChange]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({
          page: String(page),
          limit: String(INTERACTIONS_PAGE_SIZE),
        });
        if (query) params.set("q", query);
        if (triggerFilter) params.set("trigger_type", triggerFilter);
        if (failuresOnly) params.set("failures_only", "true");

        const res = await apiFetch(
          `/api/agents/${agentId}/interactions?${params.toString()}`
        );
        if (!res.ok) {
          throw new Error(`HTTP ${res.status}`);
        }
        const payload: AgentInteractionListResponse = await res.json();
        if (cancelled) return;
        const nextItems = Array.isArray(payload.items) ? payload.items : [];
        const nextTotal = typeof payload.total === "number" ? payload.total : 0;
        setItems(nextItems);
        setTotal(nextTotal);
        onTotalChange(nextTotal);
      } catch {
        if (!cancelled) {
          setError("Could not load interaction log.");
          setItems([]);
          setTotal(0);
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [agentId, page, query, triggerFilter, failuresOnly, onTotalChange, initialInteractionId]);

  const totalPages = Math.max(1, Math.ceil(total / INTERACTIONS_PAGE_SIZE));
  const rangeStart = total === 0 ? 0 : (page - 1) * INTERACTIONS_PAGE_SIZE + 1;
  const rangeEnd = Math.min(page * INTERACTIONS_PAGE_SIZE, total);

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
        <div>
          <h2 className="text-sm font-bold text-slate-900">Interaction log</h2>
          <p className="text-xs text-slate-500 mt-1">
            Queries and responses from Slack and agent-to-agent calls. Click a row to
            open the full run details, tool timeline, and failure diagnostics.
          </p>
        </div>

        <div className="flex flex-col sm:flex-row gap-2 sm:items-center">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              type="search"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              placeholder="Search user, query, or response…"
              className="w-full text-sm pl-9 pr-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
            />
          </div>
          <select
            value={triggerFilter}
            onChange={e => setTriggerFilter(e.target.value)}
            className="text-sm px-3 py-2 border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-slate-400"
          >
            <option value="">All types</option>
            <option value="mention">Mention</option>
            <option value="dm">DM</option>
            <option value="a2a">Agent call</option>
          </select>
          <label className="inline-flex items-center gap-2 text-sm text-slate-700 whitespace-nowrap px-1">
            <input
              type="checkbox"
              checked={failuresOnly}
              onChange={e => setFailuresOnly(e.target.checked)}
              className="rounded border-slate-300 text-slate-900 focus:ring-slate-400"
            />
            Failures only
          </label>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 bg-white border border-slate-200 rounded-xl">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 bg-white border border-slate-200 rounded-xl text-slate-500 text-sm">
          {query || triggerFilter || failuresOnly
            ? "No interactions match your filters."
            : "No interactions recorded yet."}
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[720px]">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <InteractionTh>Time</InteractionTh>
                  <InteractionTh>User</InteractionTh>
                  <InteractionTh>Type</InteractionTh>
                  <InteractionTh>Duration</InteractionTh>
                  <InteractionTh>Query</InteractionTh>
                  <InteractionTh className="w-10" />
                </tr>
              </thead>
              <tbody>
                {items.map(item => {
                  const isHighlighted = highlightId === item.id;
                  return (
                    <InteractionRow
                      key={item.id}
                      item={item}
                      highlighted={isHighlighted}
                      rowRef={isHighlighted ? deepLinkRowRef : undefined}
                      onOpen={() => setSelectedItem(item)}
                    />
                  );
                })}
              </tbody>
            </table>
          </div>

          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 py-3 border-t border-slate-200 bg-slate-50">
            <p className="text-xs text-slate-500">
              Showing {rangeStart}–{rangeEnd} of {total.toLocaleString()}
            </p>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => setPage(p => Math.max(1, p - 1))}
                disabled={page <= 1}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium border border-slate-200 rounded-lg bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <ChevronLeft className="w-3.5 h-3.5" />
                Previous
              </button>
              <span className="text-xs text-slate-500 tabular-nums">
                Page {page} of {totalPages}
              </span>
              <button
                type="button"
                onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                disabled={page >= totalPages}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium border border-slate-200 rounded-lg bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40 disabled:cursor-not-allowed"
              >
                Next
                <ChevronRight className="w-3.5 h-3.5" />
              </button>
            </div>
          </div>
        </div>
      )}

      {selectedItem && (
        <InteractionDetailModal
          agentId={agentId}
          item={selectedItem}
          onClose={() => setSelectedItem(null)}
        />
      )}
    </div>
  );
}

function InteractionTh({
  children,
  className = "",
}: {
  children?: ReactNode;
  className?: string;
}) {
  return (
    <th
      className={`text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-2.5 ${className}`}
    >
      {children}
    </th>
  );
}

function InteractionRow({
  item,
  onOpen,
  highlighted,
  rowRef,
}: {
  item: AgentInteractionRecord;
  onOpen: () => void;
  highlighted?: boolean;
  rowRef?: RefObject<HTMLTableRowElement | null>;
}) {
  const preview =
    item.user_message.length > 120
      ? `${item.user_message.slice(0, 120)}…`
      : item.user_message;
  const toolSummary = item.tool_summary;
  const hasFailure = Boolean(item.failure_kind);

  return (
    <tr
      ref={rowRef}
      className={`border-b border-slate-100 hover:bg-slate-50 cursor-pointer ${
        hasFailure ? "bg-red-50/40" : ""
      } ${highlighted ? "ring-2 ring-inset ring-slate-300 bg-slate-50" : ""}`}
      onClick={onOpen}
    >
      <td className="px-4 py-3 text-xs text-slate-600 whitespace-nowrap">
        {formatInteractionWhen(item.created_at)}
      </td>
      <td className="px-4 py-3 text-xs text-slate-700 max-w-[180px] truncate" title={item.invoker_email}>
        {item.invoker_email}
      </td>
      <td className="px-4 py-3">
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="inline-flex text-[10px] font-semibold uppercase tracking-wide rounded-full bg-slate-100 text-slate-600 px-2 py-0.5">
            {interactionTriggerLabel(item.trigger_type)}
          </span>
          {hasFailure && (
            <span className="inline-flex text-[10px] font-semibold uppercase tracking-wide rounded-full bg-red-100 text-red-700 px-2 py-0.5">
              Failed
            </span>
          )}
          {toolSummary && toolSummary.total > 0 && (
            <span className="inline-flex text-[10px] font-medium rounded-full bg-slate-50 text-slate-500 border border-slate-200 px-2 py-0.5 tabular-nums">
              {toolSummary.total} tools
              {toolSummary.failed > 0 ? ` · ${toolSummary.failed} err` : ""}
            </span>
          )}
        </div>
      </td>
      <td className="px-4 py-3 text-xs text-slate-500 tabular-nums whitespace-nowrap">
        {item.duration_ms != null ? `${(item.duration_ms / 1000).toFixed(1)}s` : "—"}
      </td>
      <td className="px-4 py-3 text-sm text-slate-700 max-w-md">
        <span className="line-clamp-2 whitespace-pre-wrap">{preview}</span>
      </td>
      <td className="px-4 py-3 text-slate-400">
        <ChevronRight className="w-4 h-4" />
      </td>
    </tr>
  );
}

// ── Memory tab ───────────────────────────────────────────────────────────────

const MEMORY_PAGE_SIZE = 50;

function MemoryTab({
  agentId,
  onTotalChange,
}: {
  agentId: string;
  onTotalChange: (n: number) => void;
}) {
  const [items, setItems] = useState<AgentMemoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [scopeFilter, setScopeFilter] = useState<"" | "agent" | "user">("");
  const [searchInput, setSearchInput] = useState("");
  const [query, setQuery] = useState("");

  // Inline editing state
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editKey, setEditKey] = useState("");
  const [editValue, setEditValue] = useState("");
  const [saving, setSaving] = useState(false);

  // Add-new row state
  const [adding, setAdding] = useState(false);
  const [newScope, setNewScope] = useState<"agent" | "user">("agent");
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [addError, setAddError] = useState<string | null>(null);

  useEffect(() => {
    const t = window.setTimeout(() => setQuery(searchInput.trim()), 300);
    return () => window.clearTimeout(t);
  }, [searchInput]);

  useEffect(() => { setPage(1); }, [query, scopeFilter]);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setError(null);
      try {
        const params = new URLSearchParams({ page: String(page), limit: String(MEMORY_PAGE_SIZE) });
        if (scopeFilter) params.set("scope", scopeFilter);
        if (query) params.set("search", query);
        const res = await apiFetch(`/api/agents/${agentId}/memories?${params}`);
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data: AgentMemoryListResponse = await res.json();
        if (cancelled) return;
        const nextItems = Array.isArray(data.items) ? data.items : [];
        const nextTotal = typeof data.total === "number" ? data.total : 0;
        setItems(nextItems);
        setTotal(nextTotal);
        onTotalChange(nextTotal);
      } catch {
        if (!cancelled) setError("Could not load memory.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => { cancelled = true; };
  }, [agentId, page, query, scopeFilter, onTotalChange]);

  const reload = () => {
    setPage(1);
    setQuery(searchInput.trim());
  };

  const startEdit = (item: AgentMemoryRecord) => {
    setEditingId(item.id);
    setEditKey(item.key);
    setEditValue(item.value);
  };

  const cancelEdit = () => {
    setEditingId(null);
    setEditKey("");
    setEditValue("");
  };

  const saveEdit = async (item: AgentMemoryRecord) => {
    setSaving(true);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/memories/${item.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ key: editKey.trim(), value: editValue.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated: AgentMemoryRecord = await res.json();
      setItems(prev => prev.map(m => (m.id === updated.id ? updated : m)));
      setEditingId(null);
    } catch {
      // leave editing open so user can retry
    } finally {
      setSaving(false);
    }
  };

  const deleteOne = async (id: string) => {
    if (!window.confirm("Delete this memory?")) return;
    try {
      await apiFetch(`/api/agents/${agentId}/memories/${id}`, { method: "DELETE" });
      setItems(prev => {
        const next = prev.filter(m => m.id !== id);
        const nextTotal = total - 1;
        setTotal(nextTotal);
        onTotalChange(nextTotal);
        return next;
      });
    } catch {
      setError("Delete failed.");
    }
  };

  const clearAll = async () => {
    const msg = scopeFilter
      ? `Clear all ${scopeFilter}-scope memories for this agent?`
      : "Clear ALL memories for this agent? This cannot be undone.";
    if (!window.confirm(msg)) return;
    try {
      const params = new URLSearchParams();
      if (scopeFilter) params.set("scope", scopeFilter);
      await apiFetch(`/api/agents/${agentId}/memories?${params}`, { method: "DELETE" });
      setItems([]);
      setTotal(0);
      onTotalChange(0);
    } catch {
      setError("Clear failed.");
    }
  };

  const submitAdd = async () => {
    setAddError(null);
    if (!newKey.trim() || !newValue.trim()) {
      setAddError("Key and value are required.");
      return;
    }
    if (newScope === "user" && !newEmail.trim()) {
      setAddError("User email is required for user-scope memories.");
      return;
    }
    setSaving(true);
    try {
      const body: Record<string, string> = {
        scope: newScope,
        key: newKey.trim(),
        value: newValue.trim(),
      };
      if (newScope === "user") body.user_email = newEmail.trim().toLowerCase();
      const res = await apiFetch(`/api/agents/${agentId}/memories`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail ?? `HTTP ${res.status}`);
      }
      setAdding(false);
      setNewKey("");
      setNewValue("");
      setNewEmail("");
      setNewScope("agent");
      reload();
    } catch (err) {
      setAddError(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSaving(false);
    }
  };

  const totalPages = Math.max(1, Math.ceil(total / MEMORY_PAGE_SIZE));

  return (
    <div className="space-y-4">
      <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-sm font-bold text-slate-900">Agent memory</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Facts the agent remembers across conversations. Auto-extracted after each interaction or added manually.
            </p>
          </div>
          <div className="flex gap-2 shrink-0">
            <button
              type="button"
              onClick={() => setAdding(a => !a)}
              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold border border-slate-200 rounded-lg bg-white text-slate-700 hover:bg-slate-50 transition-colors"
            >
              <Plus className="w-3.5 h-3.5" />
              Add
            </button>
            {total > 0 && (
              <button
                type="button"
                onClick={clearAll}
                className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold border border-red-200 rounded-lg bg-white text-red-600 hover:bg-red-50 transition-colors"
              >
                <Trash2 className="w-3.5 h-3.5" />
                Clear{scopeFilter ? ` ${scopeFilter}` : " all"}
              </button>
            )}
          </div>
        </div>

        {adding && (
          <div className="border border-slate-200 rounded-lg p-3 bg-slate-50 space-y-2">
            <p className="text-xs font-semibold text-slate-700">New memory</p>
            <div className="flex flex-col sm:flex-row gap-2">
              <select
                value={newScope}
                onChange={e => setNewScope(e.target.value as "agent" | "user")}
                className="text-xs px-2 py-1.5 border border-slate-200 rounded-lg bg-white"
              >
                <option value="agent">Agent scope</option>
                <option value="user">User scope</option>
              </select>
              <input
                type="text"
                placeholder="Key (e.g. prefers_bullets)"
                value={newKey}
                onChange={e => setNewKey(e.target.value)}
                className="flex-1 text-xs px-2 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
              />
              <input
                type="text"
                placeholder="Value"
                value={newValue}
                onChange={e => setNewValue(e.target.value)}
                className="flex-1 text-xs px-2 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
              />
              {newScope === "user" && (
                <input
                  type="email"
                  placeholder="user@example.com"
                  value={newEmail}
                  onChange={e => setNewEmail(e.target.value)}
                  className="flex-1 text-xs px-2 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
                />
              )}
            </div>
            {addError && <p className="text-[11px] text-red-600">{addError}</p>}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={submitAdd}
                disabled={saving}
                className="px-3 py-1.5 text-xs font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700 disabled:opacity-50"
              >
                {saving ? <Loader2 className="w-3 h-3 animate-spin inline" /> : "Save"}
              </button>
              <button
                type="button"
                onClick={() => { setAdding(false); setAddError(null); }}
                className="px-3 py-1.5 text-xs text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50"
              >
                Cancel
              </button>
            </div>
          </div>
        )}

        <div className="flex flex-col sm:flex-row gap-2">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
            <input
              type="search"
              value={searchInput}
              onChange={e => setSearchInput(e.target.value)}
              placeholder="Search key or value…"
              className="w-full text-sm pl-9 pr-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
            />
          </div>
          <select
            value={scopeFilter}
            onChange={e => setScopeFilter(e.target.value as "" | "agent" | "user")}
            className="text-sm px-3 py-2 border border-slate-200 rounded-lg bg-white focus:outline-none focus:border-slate-400"
          >
            <option value="">All scopes</option>
            <option value="agent">Agent</option>
            <option value="user">User</option>
          </select>
        </div>
      </div>

      {error && (
        <div className="bg-red-50 border border-red-200 text-red-700 text-sm rounded-xl px-4 py-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-16 bg-white border border-slate-200 rounded-xl">
          <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
        </div>
      ) : items.length === 0 ? (
        <div className="text-center py-16 bg-white border border-slate-200 rounded-xl">
          <Brain className="w-8 h-8 text-slate-300 mx-auto mb-3" />
          <p className="text-sm text-slate-500">
            {query || scopeFilter ? "No memories match your filters." : "No memories stored yet. They'll be auto-extracted as the agent converses."}
          </p>
        </div>
      ) : (
        <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[640px]">
              <thead className="bg-slate-50 border-b border-slate-200">
                <tr>
                  <MemTh>Scope</MemTh>
                  <MemTh>Key</MemTh>
                  <MemTh>Value</MemTh>
                  <MemTh>User</MemTh>
                  <MemTh>Source</MemTh>
                  <MemTh>Updated</MemTh>
                  <MemTh className="w-16" />
                </tr>
              </thead>
              <tbody>
                {items.map(item => {
                  const isEditing = editingId === item.id;
                  return (
                    <tr key={item.id} className="border-b border-slate-100 last:border-b-0 hover:bg-slate-50 group">
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                            item.scope === "agent"
                              ? "bg-indigo-50 text-indigo-700"
                              : "bg-emerald-50 text-emerald-700"
                          }`}
                        >
                          {item.scope}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {isEditing ? (
                          <input
                            autoFocus
                            type="text"
                            value={editKey}
                            onChange={e => setEditKey(e.target.value)}
                            className="w-full text-xs px-2 py-1 border border-slate-300 rounded focus:outline-none focus:border-slate-500"
                          />
                        ) : (
                          <span className="text-xs font-mono text-slate-700">{item.key}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 max-w-xs">
                        {isEditing ? (
                          <input
                            type="text"
                            value={editValue}
                            onChange={e => setEditValue(e.target.value)}
                            onKeyDown={e => {
                              if (e.key === "Enter") void saveEdit(item);
                              if (e.key === "Escape") cancelEdit();
                            }}
                            className="w-full text-xs px-2 py-1 border border-slate-300 rounded focus:outline-none focus:border-slate-500"
                          />
                        ) : (
                          <span className="text-xs text-slate-800 line-clamp-2 whitespace-pre-wrap">{item.value}</span>
                        )}
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-500 max-w-[160px] truncate" title={item.user_email ?? ""}>
                        {item.user_email ?? <span className="text-slate-300">—</span>}
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={`inline-flex text-[10px] font-semibold uppercase tracking-wide rounded-full px-2 py-0.5 ${
                            item.source === "manual"
                              ? "bg-amber-50 text-amber-700"
                              : "bg-slate-100 text-slate-500"
                          }`}
                        >
                          {item.source}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-slate-400 whitespace-nowrap">
                        {new Date(item.updated_at).toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" })}
                      </td>
                      <td className="px-4 py-3">
                        {isEditing ? (
                          <div className="flex items-center gap-1">
                            <button
                              type="button"
                              onClick={() => void saveEdit(item)}
                              disabled={saving}
                              className="px-2 py-0.5 text-[10px] font-bold bg-slate-900 text-white rounded hover:bg-slate-700 disabled:opacity-50"
                            >
                              {saving ? "…" : "Save"}
                            </button>
                            <button
                              type="button"
                              onClick={cancelEdit}
                              className="px-2 py-0.5 text-[10px] text-slate-500 border border-slate-200 rounded hover:bg-slate-50"
                            >
                              Cancel
                            </button>
                          </div>
                        ) : (
                          <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
                            <button
                              type="button"
                              onClick={() => startEdit(item)}
                              className="p-1 text-slate-400 hover:text-slate-700"
                              title="Edit"
                            >
                              <Pencil className="w-3.5 h-3.5" />
                            </button>
                            <button
                              type="button"
                              onClick={() => void deleteOne(item.id)}
                              className="p-1 text-slate-400 hover:text-red-600"
                              title="Delete"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>

          {totalPages > 1 && (
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3 px-4 py-3 border-t border-slate-200 bg-slate-50">
              <p className="text-xs text-slate-500">
                Showing {total === 0 ? 0 : (page - 1) * MEMORY_PAGE_SIZE + 1}–{Math.min(page * MEMORY_PAGE_SIZE, total)} of {total.toLocaleString()}
              </p>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => setPage(p => Math.max(1, p - 1))}
                  disabled={page <= 1}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium border border-slate-200 rounded-lg bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  <ChevronLeft className="w-3.5 h-3.5" /> Previous
                </button>
                <span className="text-xs text-slate-500 tabular-nums">Page {page} of {totalPages}</span>
                <button
                  type="button"
                  onClick={() => setPage(p => Math.min(totalPages, p + 1))}
                  disabled={page >= totalPages}
                  className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-medium border border-slate-200 rounded-lg bg-white text-slate-700 hover:bg-slate-50 disabled:opacity-40"
                >
                  Next <ChevronRight className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function MemTh({ children, className = "" }: { children?: ReactNode; className?: string }) {
  return (
    <th className={`text-left text-[11px] font-semibold text-slate-500 uppercase tracking-wider px-4 py-2.5 ${className}`}>
      {children}
    </th>
  );
}

// ── View mode ──────────────────────────────────────────────────────────────

function ViewMode({
  agent,
  tasks,
  onRefresh,
  isOwner,
  canEdit,
}: {
  agent: AgentRecord;
  tasks: ScheduledTask[];
  onRefresh: () => void;
  isOwner: boolean;
  canEdit: boolean;
}) {
  const skillList = useSkillList();
  const [showGatewayKey, setShowGatewayKey] = useState(false);
  const [clearingKey, setClearingKey] = useState(false);
  const [savingArtifacts, setSavingArtifacts] = useState(false);
  const [savingRouting, setSavingRouting] = useState(false);
  const [generatingIndex, setGeneratingIndex] = useState(false);
  const [savingIndex, setSavingIndex] = useState(false);
  const [indexDraft, setIndexDraft] = useState(agent.skill_index_text ?? "");

  useEffect(() => {
    setIndexDraft(agent.skill_index_text ?? "");
  }, [agent.skill_index_text, agent.id]);

  const toggleArtifacts = async () => {
    if (!canEdit || savingArtifacts) return;
    setSavingArtifacts(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ artifacts_enabled: !agent.artifacts_enabled }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `Update failed (HTTP ${res.status}).`);
      }
      onRefresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not update skill artifacts setting.");
    } finally {
      setSavingArtifacts(false);
    }
  };

  const toggleSkillRouting = async () => {
    if (!canEdit || savingRouting) return;
    setSavingRouting(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_routing_enabled: !agent.skill_routing_enabled }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `Update failed (HTTP ${res.status}).`);
      }
      onRefresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not update skill routing setting.");
    } finally {
      setSavingRouting(false);
    }
  };

  const generateSkillIndex = async () => {
    if (!canEdit || generatingIndex) return;
    if (
      agent.skill_index_text &&
      !window.confirm("Regenerate the skill index? This will replace the current index (you can edit before saving).")
    ) {
      return;
    }
    setGeneratingIndex(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}/generate-skill-index`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `Generate failed (HTTP ${res.status}).`);
      }
      const body = await res.json();
      setIndexDraft(body.agent?.skill_index_text ?? "");
      onRefresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not generate skill index.");
    } finally {
      setGeneratingIndex(false);
    }
  };

  const saveSkillIndex = async () => {
    if (!canEdit || savingIndex) return;
    setSavingIndex(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ skill_index_text: indexDraft }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `Save failed (HTTP ${res.status}).`);
      }
      onRefresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not save skill index.");
    } finally {
      setSavingIndex(false);
    }
  };

  const clearGatewayKey = async () => {
    if (clearingKey) return;
    if (!window.confirm("Remove this agent's gateway key? It will fall back to the platform key.")) {
      return;
    }
    setClearingKey(true);
    try {
      const res = await apiFetch(`/api/agents/${agent.id}/gateway-key`, { method: "DELETE" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(typeof body?.detail === "string" ? body.detail : `Clear failed (HTTP ${res.status}).`);
      }
      onRefresh();
    } catch (err) {
      window.alert(err instanceof Error ? err.message : "Could not clear gateway key.");
    } finally {
      setClearingKey(false);
    }
  };

  return (
    <div className="space-y-5">
      <Card title="Skills">
        {agent.skill_ids.length === 0 ? (
          <p className="text-xs text-slate-400">No skills configured.</p>
        ) : (
          <ul className="space-y-1.5">
            {agent.skill_ids.map(slug => {
              const meta = skillList.find(m => m.slug === slug);
              return (
                <li key={slug} className="text-sm text-slate-700 flex items-center gap-2">
                  <Sparkles className="w-3 h-3 text-amber-500 shrink-0" />
                  <span className="font-medium">{meta?.name ?? slug}</span>
                  <span className="text-xs text-slate-400 font-mono">{slug}</span>
                </li>
              );
            })}
          </ul>
        )}
      </Card>

      <Card title="Skill artifacts">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <p className="text-sm text-slate-700">
              {agent.artifacts_enabled
                ? "This agent can run approved Python artifacts attached to its skills."
                : "Artifacts are off — the agent uses run_python and other tools only."}
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Turn on when you want call_artifact instead of ad-hoc sandbox code for supported skills.
            </p>
          </div>
          {canEdit ? (
            <button
              type="button"
              role="switch"
              aria-checked={!!agent.artifacts_enabled}
              disabled={savingArtifacts}
              onClick={toggleArtifacts}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50 ${
                agent.artifacts_enabled ? "bg-amber-500" : "bg-slate-200"
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition ${
                  agent.artifacts_enabled ? "translate-x-5" : "translate-x-0"
                }`}
              />
            </button>
          ) : (
            <span className="text-xs font-medium text-slate-500 shrink-0">
              {agent.artifacts_enabled ? "Enabled" : "Disabled"}
            </span>
          )}
        </div>
      </Card>

      <Card title="Auto-select skills">
        <div className="flex items-start justify-between gap-4 mb-4">
          <div className="min-w-0">
            <p className="text-sm text-slate-700">
              {agent.skill_routing_enabled
                ? "The router picks 1–2 relevant skills per Slack message using the index below."
                : "All attached skills load on every Slack turn (current default)."}
            </p>
            <p className="text-xs text-slate-400 mt-1">
              Generate a compact Skill Index, then enable auto-select. Use shadow mode globally before enforce.
            </p>
            {agent.skill_index_stale ? (
              <p className="text-xs text-amber-700 mt-2 font-medium">
                Skills changed since the index was generated — regenerate the index.
              </p>
            ) : null}
          </div>
          {canEdit ? (
            <button
              type="button"
              role="switch"
              aria-checked={!!agent.skill_routing_enabled}
              disabled={savingRouting}
              onClick={toggleSkillRouting}
              className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50 ${
                agent.skill_routing_enabled ? "bg-amber-500" : "bg-slate-200"
              }`}
            >
              <span
                className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition ${
                  agent.skill_routing_enabled ? "translate-x-5" : "translate-x-0"
                }`}
              />
            </button>
          ) : (
            <span className="text-xs font-medium text-slate-500 shrink-0">
              {agent.skill_routing_enabled ? "Enabled" : "Disabled"}
            </span>
          )}
        </div>

        {canEdit ? (
          <div className="space-y-3">
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={generateSkillIndex}
                disabled={generatingIndex || agent.skill_ids.length === 0}
                className="text-xs font-medium px-3 py-1.5 rounded-md bg-slate-900 text-white hover:bg-slate-800 disabled:opacity-50"
              >
                {generatingIndex ? "Generating…" : agent.skill_index_text ? "Regenerate index" : "Generate index"}
              </button>
              {(indexDraft !== (agent.skill_index_text ?? "") || indexDraft) && (
                <button
                  type="button"
                  onClick={saveSkillIndex}
                  disabled={savingIndex}
                  className="text-xs font-medium px-3 py-1.5 rounded-md border border-slate-300 text-slate-700 hover:bg-slate-50 disabled:opacity-50"
                >
                  {savingIndex ? "Saving…" : "Save index"}
                </button>
              )}
            </div>
            <textarea
              value={indexDraft}
              onChange={e => setIndexDraft(e.target.value)}
              readOnly={!canEdit}
              rows={10}
              placeholder="Generate a skill index to describe when each attached skill should be used…"
              className="w-full text-xs font-mono border border-slate-200 rounded-lg p-3 text-slate-700 focus:outline-none focus:ring-2 focus:ring-amber-500"
            />
            <p className="text-[11px] text-slate-400">
              {(indexDraft || "").length.toLocaleString()} / 8,000 characters
              {agent.skill_index_generated_at
                ? ` · Last generated ${new Date(agent.skill_index_generated_at).toLocaleString()}`
                : ""}
            </p>
          </div>
        ) : agent.skill_index_text ? (
          <pre className="text-xs text-slate-600 whitespace-pre-wrap font-mono bg-slate-50 border border-slate-100 rounded-lg p-3">
            {agent.skill_index_text}
          </pre>
        ) : (
          <p className="text-xs text-slate-400">No skill index configured.</p>
        )}
      </Card>

      {/* Per-agent document retrieval. Its own card rather than a row in the
          Skills card above: it needs a status indicator, an upload widget and a
          document list, not just a toggle. */}
      <AgentKnowledgeBaseCard agentId={agent.id} canEdit={canEdit} />

      <Card title="Channels & trigger">
        <div className="grid sm:grid-cols-2 gap-4">
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Channels</p>
            {agent.channel_ids.length === 0 ? (
              <p className="text-sm text-slate-400">None configured.</p>
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {agent.channel_ids.map(ch => (
                  <span
                    key={ch}
                    className="inline-flex items-center gap-1 text-xs bg-slate-100 text-slate-700 border border-slate-200 rounded-full px-2 py-0.5"
                  >
                    <Hash className="w-3 h-3 text-slate-400" />
                    {ch.replace(/^#/, "")}
                  </span>
                ))}
              </div>
            )}
          </div>
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Trigger</p>
            <p className="text-sm text-slate-700">
              {agent.trigger_mode === "mention"
                ? "@mention"
                : agent.trigger_mode === "dm"
                  ? "Direct message"
                  : "Both @mention and DM"}
            </p>
          </div>
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">Model</p>
            <p className="text-sm text-slate-700">{agentModelLabel(agent.llm_model)}</p>
            {agent.llm_model && (
              <p className="text-[11px] text-slate-400 font-mono mt-0.5">{agent.llm_model}</p>
            )}
          </div>
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">gateway key</p>
            <p className="text-sm text-slate-700">
              {agent.has_gateway_api_key ? "Bring your own key" : "Platform default"}
            </p>
          </div>
        </div>
      </Card>

      <Card title="Slack scopes" hint="Slack permission scopes granted to this agent's bot token.">
        {(() => {
          const BASE_SCOPES = [
            "app_mentions:read", "chat:write", "channels:read", "channels:history",
            "groups:read", "groups:history", "users:read", "users:read.email",
          ];
          const baseSet = new Set(BASE_SCOPES);
          const scopes = agent.bot_scopes ?? BASE_SCOPES;
          return (
            <>
              <div className="flex flex-wrap gap-2">
                {scopes.map(scope => {
                  const isBase = baseSet.has(scope);
                  return (
                    <span
                      key={scope}
                      className={`inline-flex items-center gap-1 text-xs font-mono px-2.5 py-1 rounded-lg border ${
                        isBase
                          ? "bg-slate-50 border-slate-200 text-slate-600"
                          : "bg-violet-50 border-violet-200 text-violet-700"
                      }`}
                    >
                      {isBase && <Lock className="w-3 h-3 shrink-0 opacity-50" />}
                      {scope}
                    </span>
                  );
                })}
              </div>
              {!agent.bot_scopes && (
                <p className="text-[11px] text-slate-400 mt-2">Showing default base scopes.</p>
              )}
            </>
          );
        })()}
      </Card>

      <Card title="Personality / Soul">
        {agent.soul_text ? (
          <pre className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded-lg p-3 whitespace-pre-wrap leading-relaxed font-mono">
            {agent.soul_text}
          </pre>
        ) : (
          <p className="text-xs text-slate-400">No personality configured (default Hub system prompt is used).</p>
        )}
      </Card>

      <Card
        title="Token storage"
        hint="Bot/app tokens and optional gateway keys live in the vault. Only resource refs are stored here."
      >
        <div className="space-y-1.5 font-mono text-xs">
          <p className="text-slate-600">
            <span className="text-slate-400">xoxb:</span>{" "}
            {agent.xoxb_secret_ref ?? <span className="text-slate-400 italic">not stored</span>}
          </p>
          <p className="text-slate-600">
            <span className="text-slate-400">xapp:</span>{" "}
            {agent.xapp_secret_ref ?? <span className="text-slate-400 italic">not stored</span>}
          </p>
          <p className="text-slate-600">
            <span className="text-slate-400">gateway:</span>{" "}
            {agent.gateway_api_key_ref ?? <span className="text-slate-400 italic">platform key</span>}
          </p>
        </div>
        {isOwner && agent.status !== "decommissioned" && (
          <div className="mt-3 pt-3 border-t border-slate-100 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={() => setShowGatewayKey(true)}
              className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-700 hover:border-slate-400 transition-colors"
            >
              {agent.has_gateway_api_key ? "Rotate gateway key" : "Set gateway key"}
            </button>
            {agent.has_gateway_api_key && (
              <button
                type="button"
                onClick={() => void clearGatewayKey()}
                disabled={clearingKey}
                className="text-xs px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-500 hover:border-red-300 hover:text-red-600 transition-colors disabled:opacity-50"
              >
                {clearingKey ? "Clearing…" : "Use platform key"}
              </button>
            )}
          </div>
        )}
        {agent.creation_method === "auto_oauth" && agent.status === "registered" && (
          <div className="mt-3 pt-3 border-t border-slate-100">
            <ReinstallBotButton agentId={agent.id} />
          </div>
        )}
      </Card>

      {showGatewayKey && (
        <GatewayKeyModal
          agentId={agent.id}
          onClose={() => setShowGatewayKey(false)}
          onSaved={() => {
            setShowGatewayKey(false);
            onRefresh();
          }}
        />
      )}

      <Card
        title={`Scheduled tasks (${tasks.length})`}
        hint="Recurring tasks scheduled for this agent."
      >
        {tasks.length === 0 ? (
          <p className="text-xs text-slate-400">No scheduled tasks yet.</p>
        ) : (
          <ul className="divide-y divide-slate-100">
            {tasks.map(t => (
              <li key={t.id} className="py-2.5 first:pt-0 last:pb-0">
                <p className="text-sm text-slate-700 line-clamp-2 leading-relaxed">{t.prompt}</p>
                <p className="text-xs text-slate-500 mt-1 font-mono">
                  {t.schedule} · {t.timezone} → {t.output_channel}
                </p>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {(agent.can_invoke.length > 0 || agent.can_be_invoked_by.length > 0) && (
        <Card
          title="Agent connections"
          hint="Agents this bot can call (and agents that can call it) via the A2A API."
        >
          <div className="space-y-3">
            {agent.can_invoke.length > 0 && (
              <div>
                <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                  Can call
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.can_invoke.map(id => (
                    <span key={id} className="inline-flex items-center gap-1 text-xs bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full px-2 py-0.5 font-mono">
                      {id}
                    </span>
                  ))}
                </div>
              </div>
            )}
            {agent.can_be_invoked_by.length > 0 && (
              <div>
                <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1.5">
                  Can be called by
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {agent.can_be_invoked_by.map(id => (
                    <span key={id} className="inline-flex items-center gap-1 text-xs bg-emerald-50 text-emerald-700 border border-emerald-200 rounded-full px-2 py-0.5 font-mono">
                      {id === "*" ? "any agent" : id}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}

function Card({
  title,
  hint,
  children,
}: {
  title: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
      <header>
        <h3 className="text-sm font-bold text-slate-900">{title}</h3>
        {hint && <p className="text-xs text-slate-500 mt-1 leading-relaxed">{hint}</p>}
      </header>
      {children}
    </section>
  );
}

function ActionButton({
  onClick,
  disabled,
  children,
  icon,
  primary,
  destructive,
}: {
  onClick: () => void;
  disabled?: boolean;
  children: ReactNode;
  icon?: ReactNode;
  primary?: boolean;
  destructive?: boolean;
}) {
  const base = "flex items-center gap-1.5 text-xs font-medium px-3 py-1.5 rounded-lg transition-colors disabled:opacity-50 border";
  const variant = destructive
    ? "bg-white border-red-200 text-red-700 hover:bg-red-50"
    : primary
      ? "bg-slate-900 border-slate-900 text-white hover:bg-slate-700"
      : "bg-white border-slate-200 text-slate-700 hover:bg-slate-50";
  return (
    <button onClick={onClick} disabled={disabled} className={`${base} ${variant}`}>
      {icon}
      {children}
    </button>
  );
}

// ── Connections modal ──────────────────────────────────────────────────────

function ConnectionsModal({
  agent,
  onClose,
  onSaved,
}: {
  agent: AgentRecord;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [allAgents, setAllAgents] = useState<AgentRecord[]>([]);
  const [canInvoke, setCanInvoke] = useState<string[]>(agent.can_invoke);
  const [canBeInvokedBy, setCanBeInvokedBy] = useState<string[]>(agent.can_be_invoked_by);
  const [wildcardAny, setWildcardAny] = useState(agent.can_be_invoked_by.includes("*"));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/agents")
      .then(r => r.ok ? r.json() : [])
      .then((agents: AgentRecord[]) => setAllAgents(agents.filter(a => a.id !== agent.id)))
      .catch(() => {});
  }, [agent.id]);

  const toggleInvoke = (id: string) =>
    setCanInvoke(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);

  const toggleInvokedBy = (id: string) =>
    setCanBeInvokedBy(prev => prev.includes(id) ? prev.filter(x => x !== id) : [...prev, id]);

  const save = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const invokedByList = wildcardAny ? ["*"] : canBeInvokedBy;
      const res = await apiFetch(`/api/agents/${agent.id}/orchestration`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ can_invoke: canInvoke, can_be_invoked_by: invokedByList }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = typeof body?.detail === "string"
          ? body.detail
          : Array.isArray(body?.detail)
            ? body.detail.map((e: { msg?: string }) => e.msg ?? String(e)).join("; ")
            : `Save failed (HTTP ${res.status}).`;
        throw new Error(msg);
      }
      onSaved();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <div className="bg-white rounded-xl border border-slate-200 shadow-2xl w-full max-w-lg p-6 space-y-5 max-h-[90vh] overflow-y-auto">
        <div className="flex items-start justify-between">
          <div>
            <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <GitMerge className="w-4 h-4 text-indigo-600" />
              Agent connections
            </h2>
            <p className="text-xs text-slate-500 mt-1 leading-relaxed">
              Configure which agents <em>{agent.name}</em> can call, and which agents can call it.
              Skill code can then call peers via <code className="text-[11px] bg-slate-100 px-1 rounded">run_python</code> using the A2A API.
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-600 p-1 -mt-1">
            <X className="w-4 h-4" />
          </button>
        </div>

        <div className="space-y-4">
          <div>
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
              This agent can call
            </p>
            {allAgents.length === 0 ? (
              <p className="text-xs text-slate-400">No other agents registered.</p>
            ) : (
              <div className="flex flex-wrap gap-2">
                {allAgents.map(a => {
                  const selected = canInvoke.includes(a.id);
                  return (
                    <button
                      key={a.id}
                      type="button"
                      onClick={() => toggleInvoke(a.id)}
                      className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
                        selected
                          ? "bg-indigo-600 border-indigo-600 text-white"
                          : "bg-white border-slate-200 text-slate-700 hover:border-indigo-300 hover:text-indigo-700"
                      }`}
                    >
                      {a.name}
                    </button>
                  );
                })}
              </div>
            )}
          </div>

          <div className="border-t border-slate-100 pt-4">
            <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2">
              This agent can be called by
            </p>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => setWildcardAny(v => !v)}
                className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
                  wildcardAny
                    ? "bg-emerald-600 border-emerald-600 text-white"
                    : "bg-white border-slate-200 text-slate-700 hover:border-emerald-300 hover:text-emerald-700"
                }`}
              >
                Any agent
              </button>
              {!wildcardAny && allAgents.map(a => {
                const selected = canBeInvokedBy.includes(a.id);
                return (
                  <button
                    key={a.id}
                    type="button"
                    onClick={() => toggleInvokedBy(a.id)}
                    className={`px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors ${
                      selected
                        ? "bg-emerald-600 border-emerald-600 text-white"
                        : "bg-white border-slate-200 text-slate-700 hover:border-emerald-300 hover:text-emerald-700"
                    }`}
                  >
                    {a.name}
                  </button>
                );
              })}
            </div>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg px-3 py-2 flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex gap-2 pt-1">
          <button
            onClick={onClose}
            disabled={submitting}
            className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={save}
            disabled={submitting}
            className="flex-1 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Save connections
          </button>
        </div>
      </div>
    </div>
  );
}


// ── Rotate-tokens modal ────────────────────────────────────────────────────

function GatewayKeyModal({
  agentId,
  onClose,
  onSaved,
}: {
  agentId: string;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [apiKey, setApiKey] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    const trimmed = apiKey.trim();
    if (trimmed.length < 8 || /\s/.test(trimmed)) {
      setError("gateway API key looks malformed.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/gateway-key`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ api_key: trimmed }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = typeof body?.detail === "string"
          ? body.detail
          : Array.isArray(body?.detail)
            ? body.detail.map((e: { msg?: string }) => e.msg ?? String(e)).join("; ")
            : `Save failed (HTTP ${res.status}).`;
        throw new Error(msg);
      }
      setApiKey("");
      onSaved();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Save failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <div className="bg-white rounded-xl border border-slate-200 shadow-2xl w-full max-w-md p-6 space-y-4">
        <div>
          <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            gateway API key
          </h2>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            Bring your own gateway key for this agent. Stored in the vault — never written to the database.
            Leave the platform key alone for agents that should share quota.
          </p>
        </div>

        <div>
          <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">
            API key
          </label>
          <input
            type="password"
            autoComplete="new-password"
            spellCheck={false}
            placeholder="sk-…"
            value={apiKey}
            onChange={e => setApiKey(e.target.value.trim())}
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-slate-400"
          />
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg px-3 py-2 flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex gap-2 pt-2">
          <button
            onClick={onClose}
            disabled={submitting}
            className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={() => void submit()}
            disabled={submitting || apiKey.trim().length < 8}
            className="flex-1 px-4 py-2 text-sm font-semibold text-white bg-slate-900 rounded-lg hover:bg-slate-800 transition-colors disabled:opacity-50"
          >
            {submitting ? "Saving…" : "Save key"}
          </button>
        </div>
      </div>
    </div>
  );
}

function RotateTokensModal({
  agentId,
  onClose,
  onRotated,
}: {
  agentId: string;
  onClose: () => void;
  onRotated: () => void;
}) {
  const [xoxb, setXoxb] = useState("");
  const [signingSecret, setSigningSecret] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async () => {
    if (!XOXB_RE.test(xoxb)) {
      setError("Bot token (xoxb) looks malformed.");
      return;
    }
    if (!SIGNING_SECRET_RE.test(signingSecret)) {
      setError("Signing secret looks malformed — expected a hex string (e.g. xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx).");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/rotate-tokens`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ xoxb, signing_secret: signingSecret }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const msg = typeof body?.detail === "string"
          ? body.detail
          : Array.isArray(body?.detail)
            ? body.detail.map((e: { msg?: string }) => e.msg ?? String(e)).join("; ")
            : `Rotation failed (HTTP ${res.status}).`;
        throw new Error(msg);
      }
      setXoxb("");
      setSigningSecret("");
      onRotated();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Rotation failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40 px-4">
      <div className="bg-white rounded-xl border border-slate-200 shadow-2xl w-full max-w-md p-6 space-y-4">
        <div>
          <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
            <ShieldCheck className="w-4 h-4 text-emerald-600" />
            Rotate Slack tokens
          </h2>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            Paste the new bot token and signing secret. The previous secret
            containers in the vault are deleted after the new ones are saved.
          </p>
        </div>

        <div className="space-y-3">
          <div>
            <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Bot token (xoxb)</label>
            <input
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              placeholder="xoxb-..."
              value={xoxb}
              onChange={e => setXoxb(e.target.value)}
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-slate-400"
            />
          </div>
          <div>
            <label className="block text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-1">Signing secret</label>
            <input
              type="password"
              autoComplete="new-password"
              spellCheck={false}
              placeholder="xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
              value={signingSecret}
              onChange={e => setSigningSecret(e.target.value.trim())}
              className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-slate-400"
            />
            <p className="text-[11px] text-slate-400 mt-1">Basic Information → App Credentials → Signing Secret</p>
          </div>
        </div>

        {error && (
          <div className="bg-red-50 border border-red-200 text-red-700 text-xs rounded-lg px-3 py-2 flex items-start gap-2">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        <div className="flex gap-2 pt-2">
          <button
            onClick={onClose}
            disabled={submitting}
            className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={submit}
            disabled={submitting}
            className="flex-1 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Rotate
          </button>
        </div>

        <p className="text-[10px] text-slate-400 leading-relaxed pt-1 border-t border-slate-100">
          <Trash2 className="w-2.5 h-2.5 inline -mt-0.5 mr-1" />
          Old secret containers are deleted after the new ones land.
        </p>
      </div>
    </div>
  );
}

function formatInteractionWhen(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

function interactionTriggerLabel(trigger: AgentInteractionRecord["trigger_type"]): string {
  switch (trigger) {
    case "dm":
      return "DM";
    case "a2a":
      return "Agent call";
    case "mention":
    default:
      return "Mention";
  }
}

// ── Pending OAuth banner ──────────────────────────────────────────────────────

function ReinstallBotButton({ agentId }: { agentId: string }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleReinstall = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/slack-oauth/reinstall-agent/${agentId}`, { method: "POST" });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body?.detail ?? "Failed to start reinstall.");
        return;
      }
      const { session_id, redirect_url } = await res.json();
      navigate(`/agents/oauth-pending?session=${session_id}`, {
        state: { slack_install_url: redirect_url },
      });
    } catch {
      setError("An unexpected error occurred.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="space-y-1.5">
      <p className="text-xs text-slate-500 leading-relaxed">
        If the bot is missing Slack permissions (e.g. can't read user emails), reinstall it to refresh the OAuth scopes.
      </p>
      {error && <p className="text-xs text-red-600">{error}</p>}
      <button
        type="button"
        onClick={handleReinstall}
        disabled={loading}
        className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-slate-700 hover:bg-slate-800 text-white rounded-lg transition-colors disabled:opacity-50"
      >
        {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
        Reinstall bot
      </button>
    </div>
  );
}


function PendingOAuthBanner({ agentId }: { agentId: string }) {
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleResume = async () => {
    setLoading(true);
    setError(null);
    try {
      // resume-agent creates a fresh 30-min session using credentials stored on the
      // agent row — works even if the original session has already expired.
      const res = await apiFetch(`/api/slack-oauth/resume-agent/${agentId}`, {
        method: "POST",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail: string = body?.detail ?? "Could not resume Slack setup.";
        setError(detail);
        return;
      }
      const { session_id, redirect_url } = await res.json();
      // Navigate to the pending page; it will open the Slack install URL in a new tab.
      navigate(`/agents/oauth-pending?session=${session_id}`, {
        state: { slack_install_url: redirect_url },
      });
    } catch {
      setError("An unexpected error occurred. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="flex items-start gap-3 bg-violet-50 border border-violet-200 rounded-xl px-4 py-4">
      <Loader2 className="w-4 h-4 text-violet-600 mt-0.5 shrink-0 animate-spin" />
      <div className="flex-1 space-y-2">
        <p className="text-sm font-semibold text-violet-900">
          Slack install pending
        </p>
        <p className="text-xs text-violet-700 leading-relaxed">
          This agent is saved as a draft. Complete the Slack install to activate it.
          If your workspace required admin approval, click below once approved.
        </p>
        {error ? (
          <p className="text-xs text-red-600">{error}</p>
        ) : null}
        <button
          type="button"
          onClick={handleResume}
          disabled={loading}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-violet-700 hover:bg-violet-800 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {loading ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          Resume Slack setup
        </button>
      </div>
    </div>
  );
}

function ServiceAccountBanner({
  agent,
  onVerified,
}: {
  agent: AgentRecord;
  onVerified: () => void;
}) {
  const branding = useBranding();
  const collaborator = branding.slack_config_collaborator || "the platform collaborator";
  const [verifying, setVerifying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showHelp, setShowHelp] = useState(false);

  const handleVerify = async () => {
    if (!agent.slack_app_id) return;
    setVerifying(true);
    setError(null);
    try {
      const res = await apiFetch("/api/agents/verify-service-account", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slack_app_id: agent.slack_app_id, agent_id: agent.id }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok && data?.verified) {
        onVerified();
      } else {
        setError("This hasn't been added yet — add it in Slack, then click Done again.");
      }
    } catch {
      setError("Could not check right now — try again.");
    } finally {
      setVerifying(false);
    }
  };

  return (
    <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-4">
      <AlertCircle className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
      <div className="flex-1 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <p className="text-sm font-semibold text-amber-900">
            Service account not yet added
          </p>
          <button
            type="button"
            onClick={() => setShowHelp(v => !v)}
            className="shrink-0 text-[11px] font-semibold text-amber-800 hover:text-amber-950 underline underline-offset-2"
          >
            {showHelp ? "Hide help" : "How do I do this?"}
          </button>
        </div>
        <p className="text-xs text-amber-800 leading-relaxed">
          This agent's Slack app was self-created and doesn't yet have{" "}
          <strong>{collaborator}</strong> added as a collaborator, so {branding.app_name} can't
          update its name, description, or photo in Slack. Add it as a collaborator, then click
          Done.
        </p>

        {showHelp && (
          <ol className="space-y-2 text-xs text-amber-800 border-t border-amber-200 pt-2">
            <li>
              1. Open{" "}
              <a
                href="https://api.slack.com/apps"
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 text-amber-900 underline underline-offset-2 hover:text-amber-950"
              >
                api.slack.com/apps
                <ExternalLink className="w-3 h-3" />
              </a>
              {" "}and click into this agent's Slack app.
            </li>
            <li>2. Go to <strong>Basic Information</strong> → <strong>Collaborators</strong>.</li>
            <li>3. Click <strong>Add a collaborator</strong>, search for <strong>{collaborator}</strong>, and add it.</li>
            <li>4. Come back here and click <strong>Done</strong>.</li>
          </ol>
        )}

        {error && <p className="text-xs text-red-600">{error}</p>}

        <button
          type="button"
          onClick={handleVerify}
          disabled={verifying}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-amber-700 hover:bg-amber-800 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {verifying ? (
            <>
              <Loader2 className="w-3 h-3 animate-spin" /> Checking…
            </>
          ) : (
            <>
              <Check className="w-3 h-3" /> Done
            </>
          )}
        </button>
      </div>
    </div>
  );
}
