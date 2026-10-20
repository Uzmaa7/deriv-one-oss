import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  Zap, Clock, FlaskConical, Loader2, CheckCircle2, XCircle, ExternalLink, Maximize2,
  Beaker, Wand2, Play, Send, ChevronDown, ChevronUp,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { renderMarkdown } from "@/src/lib/chatMarkdown";
import { type MentionKind } from "@/src/components/composer/mention/types";
import { type Run, fmtDuration, renderRunMarkdown, extractFinalOutput, humanizeRunError } from "@/src/lib/automationRuns";

// ── Types ─────────────────────────────────────────────────────────────────────

type ProposalStatus = "pending" | "approved" | "discarded";

interface SkillContent {
  skill_md: string;
  api_ref_url?: string | null;
}

interface AutomationContent {
  name: string;
  instructions: string;
  schedule: string;
  is_active: boolean;
  skill_id?: string | null;
  slack_channel?: string | null;
}

interface LabContent {
  name: string;
  description: string;
  skill_ids: string[];
}

interface Proposal {
  id: string;
  chat_id: string;
  kind: MentionKind;
  name: string;
  content: SkillContent | AutomationContent | LabContent | Record<string, unknown>;
  status: ProposalStatus;
  created_resource_id: string | null;
  created_at: string | null;
}

const KIND_META: Record<MentionKind, { icon: typeof Zap; iconClass: string; label: string }> = {
  skill: { icon: Zap, iconClass: "text-amber-600", label: "Skill" },
  automation: { icon: Clock, iconClass: "text-sky-600", label: "Automation" },
  lab: { icon: FlaskConical, iconClass: "text-violet-600", label: "Lab" },
};

// ── Shared body renderer (used by both the compact card and the full modal) ───

function ProposalBody({ proposal, compact }: { proposal: Proposal; compact: boolean }) {
  if (proposal.kind === "skill") {
    const c = proposal.content as SkillContent;
    return (
      <div
        className={
          compact
            ? "max-h-56 overflow-y-auto rounded-lg border border-slate-100 bg-slate-50 px-3 py-2 text-xs prose-sm"
            : "prose-sm"
        }
        dangerouslySetInnerHTML={{ __html: renderMarkdown(c.skill_md || "") }}
      />
    );
  }
  if (proposal.kind === "automation") {
    const c = proposal.content as AutomationContent;
    return (
      <div className="space-y-1 text-xs text-slate-600">
        <p className="whitespace-pre-wrap leading-relaxed">{c.instructions}</p>
        <div><span className="font-medium text-slate-500">Schedule:</span> <code className="text-[11px]">{c.schedule}</code></div>
        <div><span className="font-medium text-slate-500">Active:</span> {c.is_active ? "Yes" : "No"}</div>
        {c.slack_channel && (
          <div><span className="font-medium text-slate-500">Slack channel:</span> {c.slack_channel}</div>
        )}
      </div>
    );
  }
  const c = proposal.content as LabContent;
  return (
    <div className="space-y-1.5 text-xs text-slate-600">
      <p className="whitespace-pre-wrap leading-relaxed">{c.description}</p>
      {c.skill_ids && c.skill_ids.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {c.skill_ids.map(id => (
            <span key={id} className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-500 text-[10px]">{id}</span>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Inline test / AI-edit (skill) ──────────────────────────────────────────────

interface ChatTurn { role: "user" | "assistant"; content: string }

function SkillTestAndEdit({
  proposal,
  onContentApplied,
}: {
  proposal: Proposal;
  onContentApplied: (content: Proposal["content"]) => void;
}) {
  const [testOpen, setTestOpen] = useState(false);
  const [testMessages, setTestMessages] = useState<ChatTurn[]>([]);
  const [testInput, setTestInput] = useState("");
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);

  const [editOpen, setEditOpen] = useState(false);
  const [editInput, setEditInput] = useState("");
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<{ content: SkillContent; summary: string; reply: string } | null>(null);
  const [applying, setApplying] = useState(false);
  const [justUpdated, setJustUpdated] = useState(false);

  const sendTestMessage = async () => {
    const text = testInput.trim();
    if (!text || testLoading) return;
    const nextMessages: ChatTurn[] = [...testMessages, { role: "user", content: text }];
    setTestMessages(nextMessages);
    setTestInput("");
    setTestLoading(true);
    setTestError(null);
    setJustUpdated(false);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/test-chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: testMessages }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const data: { reply: string } = await res.json();
      setTestMessages([...nextMessages, { role: "assistant", content: data.reply }]);
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setTestLoading(false);
    }
  };

  const suggestEdit = async () => {
    const text = editInput.trim();
    if (!text || editLoading) return;
    setEditLoading(true);
    setEditError(null);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/ai-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: [] }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const data: { content: SkillContent; summary: string; reply: string } = await res.json();
      setSuggestion(data);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setEditLoading(false);
    }
  };

  const applySuggestion = async () => {
    if (!suggestion || applying) return;
    setApplying(true);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: suggestion.content }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      onContentApplied(suggestion.content);
      setSuggestion(null);
      setEditInput("");
      // The skill's instructions just changed — a test conversation started
      // before this edit reflects the OLD instructions, and models tend to
      // stay consistent with their own prior answers rather than reconsider
      // mid-thread. Reset it so the next test reflects the update cleanly.
      if (testMessages.length > 0) {
        setTestMessages([]);
        setJustUpdated(true);
        setTestOpen(true);
      }
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
      {/* Test this skill */}
      <div>
        <button
          type="button"
          onClick={() => setTestOpen(v => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800 transition-colors"
        >
          <Beaker className="w-3.5 h-3.5" />
          Test this skill
          {testOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        {testOpen && (
          <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 space-y-2">
            {justUpdated ? (
              <p className="text-[11px] text-emerald-700 bg-emerald-50 border border-emerald-100 rounded px-2 py-1">
                Skill updated — this test conversation was reset to reflect the change.
              </p>
            ) : (
              <p className="text-[11px] text-slate-400">
                Try asking it a question a real user would ask. Code execution and secrets aren&apos;t available until it&apos;s published.
              </p>
            )}
            {testMessages.length > 0 && (
              <div className="max-h-48 overflow-y-auto space-y-2">
                {testMessages.map((m, i) => (
                  <div
                    key={i}
                    className={`text-xs rounded-lg px-2.5 py-1.5 ${
                      m.role === "user" ? "bg-white border border-slate-200 text-slate-700" : "bg-amber-50 border border-amber-100 text-slate-700"
                    }`}
                  >
                    {m.role === "assistant" ? (
                      <div dangerouslySetInnerHTML={{ __html: renderMarkdown(m.content) }} />
                    ) : m.content}
                  </div>
                ))}
              </div>
            )}
            {testError && <div className="text-[11px] text-red-600">{testError}</div>}
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                value={testInput}
                onChange={e => setTestInput(e.target.value)}
                onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void sendTestMessage(); } }}
                placeholder={testMessages.length === 0 ? `Ask ${proposal.name} something…` : "Ask a follow-up…"}
                className="flex-1 text-xs bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 placeholder:text-slate-400 focus:outline-none focus:border-slate-400"
              />
              <button
                type="button"
                onClick={() => void sendTestMessage()}
                disabled={!testInput.trim() || testLoading}
                className="w-7 h-7 shrink-0 rounded-lg bg-slate-900 text-white flex items-center justify-center hover:bg-slate-700 transition-colors disabled:opacity-30"
              >
                {testLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : <Send className="w-3 h-3" />}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Ask AI to edit */}
      <div>
        <button
          type="button"
          onClick={() => setEditOpen(v => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800 transition-colors"
        >
          <Wand2 className="w-3.5 h-3.5" />
          Ask AI to edit
          {editOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        {editOpen && (
          <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 space-y-2">
            {!suggestion ? (
              <>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={editInput}
                    onChange={e => setEditInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void suggestEdit(); } }}
                    placeholder="e.g. Make the tone friendlier, add a step for…"
                    className="flex-1 text-xs bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 placeholder:text-slate-400 focus:outline-none focus:border-slate-400"
                  />
                  <button
                    type="button"
                    onClick={() => void suggestEdit()}
                    disabled={!editInput.trim() || editLoading}
                    className="shrink-0 text-xs font-medium bg-slate-900 text-white rounded-lg px-2.5 py-1.5 hover:bg-slate-700 transition-colors disabled:opacity-30 flex items-center gap-1"
                  >
                    {editLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : "Suggest edit"}
                  </button>
                </div>
                {editError && <div className="text-[11px] text-red-600">{editError}</div>}
              </>
            ) : (
              <div className="space-y-2">
                <div className="text-xs text-slate-700">{suggestion.reply}</div>
                {suggestion.summary && (
                  <div className="text-[11px] text-slate-400 italic">{suggestion.summary}</div>
                )}
                {editError && <div className="text-[11px] text-red-600">{editError}</div>}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void applySuggestion()}
                    disabled={applying}
                    className="flex items-center gap-1 text-xs font-medium bg-slate-900 text-white rounded-lg px-2.5 py-1.5 hover:bg-slate-700 transition-colors disabled:opacity-50"
                  >
                    {applying ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                    Apply
                  </button>
                  <button
                    type="button"
                    onClick={() => { setSuggestion(null); setEditInput(""); }}
                    disabled={applying}
                    className="text-xs text-slate-500 hover:text-slate-800 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                  >
                    Discard suggestion
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Inline test / AI-edit (automation) ─────────────────────────────────────────

function AutomationTestAndEdit({
  proposal,
  onContentApplied,
  onCreatedResourceId,
}: {
  proposal: Proposal;
  onContentApplied: (content: Proposal["content"]) => void;
  onCreatedResourceId: (id: string) => void;
}) {
  const [testLoading, setTestLoading] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [automationId, setAutomationId] = useState<string | null>(proposal.created_resource_id);
  const [runs, setRuns] = useState<Run[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const pollRuns = useCallback((id: string) => {
    if (pollRef.current) clearInterval(pollRef.current);
    pollRef.current = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/automations/${id}/runs`);
        if (!res.ok) return;
        const data: Run[] = await res.json();
        setRuns(data);
        const latest = data[0];
        if (latest && latest.status !== "running" && pollRef.current) {
          clearInterval(pollRef.current);
          pollRef.current = null;
        }
      } catch {
        // transient network errors shouldn't stop polling
      }
    }, 2000);
  }, []);

  const runTest = async () => {
    if (testLoading) return;
    setTestLoading(true);
    setTestError(null);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/test`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const data: { automation_id: string } = await res.json();
      setAutomationId(data.automation_id);
      onCreatedResourceId(data.automation_id);
      setRuns([]);
      pollRuns(data.automation_id);
    } catch (err) {
      setTestError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setTestLoading(false);
    }
  };

  const latestRun = runs[0];
  const isRunning = testLoading || (!!latestRun && latestRun.status === "running");

  const [editOpen, setEditOpen] = useState(false);
  const [editInput, setEditInput] = useState("");
  const [editLoading, setEditLoading] = useState(false);
  const [editError, setEditError] = useState<string | null>(null);
  const [suggestion, setSuggestion] = useState<{ content: AutomationContent; summary: string; reply: string } | null>(null);
  const [applying, setApplying] = useState(false);

  const suggestEdit = async () => {
    const text = editInput.trim();
    if (!text || editLoading) return;
    setEditLoading(true);
    setEditError(null);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/ai-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: [] }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const data: { content: AutomationContent; summary: string; reply: string } = await res.json();
      setSuggestion(data);
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setEditLoading(false);
    }
  };

  const applySuggestion = async () => {
    if (!suggestion || applying) return;
    setApplying(true);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: suggestion.content }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      onContentApplied(suggestion.content);
      setSuggestion(null);
      setEditInput("");
    } catch (err) {
      setEditError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setApplying(false);
    }
  };

  return (
    <div className="mt-4 space-y-2 border-t border-slate-100 pt-4">
      {/* Test run */}
      <div className="space-y-2">
        <button
          type="button"
          onClick={() => void runTest()}
          disabled={isRunning}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800 transition-colors disabled:opacity-50"
        >
          {isRunning ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
          {isRunning ? "Testing… usually under a minute" : automationId ? "Test run again" : "Test run"}
        </button>
        {testError && <div className="text-[11px] text-red-600">{testError}</div>}
        {latestRun && !isRunning && (
          <div className="rounded-lg border border-slate-200 bg-slate-50 p-2.5">
            {latestRun.status === "success" ? (
              <div className="space-y-1.5">
                <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-700">
                  <CheckCircle2 className="w-3.5 h-3.5" /> Ran successfully
                </div>
                {latestRun.output && (
                  <div
                    className="text-xs text-slate-600 max-h-40 overflow-y-auto"
                    dangerouslySetInnerHTML={{ __html: renderRunMarkdown(extractFinalOutput(latestRun.output)) }}
                  />
                )}
                <div className="text-[10px] text-slate-400">Took {fmtDuration(latestRun.duration_secs)}</div>
              </div>
            ) : (
              <div className="space-y-1">
                <div className="flex items-center gap-1.5 text-xs font-medium text-red-700">
                  <XCircle className="w-3.5 h-3.5" /> Something went wrong
                </div>
                <div className="text-xs text-red-800">{humanizeRunError(latestRun.error)}</div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Ask AI to edit */}
      <div>
        <button
          type="button"
          onClick={() => setEditOpen(v => !v)}
          className="flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-slate-800 transition-colors"
        >
          <Wand2 className="w-3.5 h-3.5" />
          Ask AI to edit
          {editOpen ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </button>
        {editOpen && (
          <div className="mt-2 rounded-lg border border-slate-200 bg-slate-50 p-2.5 space-y-2">
            {!suggestion ? (
              <>
                <div className="flex items-center gap-1.5">
                  <input
                    type="text"
                    value={editInput}
                    onChange={e => setEditInput(e.target.value)}
                    onKeyDown={e => { if (e.key === "Enter") { e.preventDefault(); void suggestEdit(); } }}
                    placeholder="e.g. Run it every hour instead, or skip weekends"
                    className="flex-1 text-xs bg-white border border-slate-200 rounded-lg px-2.5 py-1.5 placeholder:text-slate-400 focus:outline-none focus:border-slate-400"
                  />
                  <button
                    type="button"
                    onClick={() => void suggestEdit()}
                    disabled={!editInput.trim() || editLoading}
                    className="shrink-0 text-xs font-medium bg-slate-900 text-white rounded-lg px-2.5 py-1.5 hover:bg-slate-700 transition-colors disabled:opacity-30 flex items-center gap-1"
                  >
                    {editLoading ? <Loader2 className="w-3 h-3 animate-spin" /> : "Suggest edit"}
                  </button>
                </div>
                {editError && <div className="text-[11px] text-red-600">{editError}</div>}
              </>
            ) : (
              <div className="space-y-2">
                <div className="text-xs text-slate-700">{suggestion.reply}</div>
                {suggestion.summary && (
                  <div className="text-[11px] text-slate-400 italic">{suggestion.summary}</div>
                )}
                {editError && <div className="text-[11px] text-red-600">{editError}</div>}
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => void applySuggestion()}
                    disabled={applying}
                    className="flex items-center gap-1 text-xs font-medium bg-slate-900 text-white rounded-lg px-2.5 py-1.5 hover:bg-slate-700 transition-colors disabled:opacity-50"
                  >
                    {applying ? <Loader2 className="w-3 h-3 animate-spin" /> : <CheckCircle2 className="w-3 h-3" />}
                    Apply
                  </button>
                  <button
                    type="button"
                    onClick={() => { setSuggestion(null); setEditInput(""); }}
                    disabled={applying}
                    className="text-xs text-slate-500 hover:text-slate-800 px-2.5 py-1.5 rounded-lg hover:bg-slate-100 transition-colors"
                  >
                    Discard suggestion
                  </button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// ── Component ─────────────────────────────────────────────────────────────────

export function ProposalCard({
  proposalId,
  initialKind,
}: {
  proposalId: string;
  initialKind: MentionKind;
}) {
  const navigate = useNavigate();
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [acting, setActing] = useState<"approve" | "discard" | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [modalOpen, setModalOpen] = useState(false);

  const fetchProposal = useCallback(async () => {
    try {
      const res = await apiFetch(`/api/proposals/${proposalId}`);
      if (!res.ok) throw new Error(`Request failed (${res.status})`);
      const data: Proposal = await res.json();
      setProposal(data);
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Failed to load proposal.");
    } finally {
      setLoading(false);
    }
  }, [proposalId]);

  useEffect(() => {
    fetchProposal();
  }, [fetchProposal]);

  const goToPublish = useCallback((content: Proposal["content"], name: string) => {
    const skillMd = (content as SkillContent).skill_md || "";
    navigate("/publish", { state: { preloadedSkillContent: skillMd, preloadedSkillName: name } });
  }, [navigate]);

  const handleContentApplied = useCallback((content: Proposal["content"]) => {
    setProposal(prev => (prev ? { ...prev, content } : prev));
  }, []);

  const handleCreatedResourceId = useCallback((id: string) => {
    setProposal(prev => (prev ? { ...prev, created_resource_id: id } : prev));
  }, []);

  const handleApprove = async () => {
    if (!proposal || proposal.status !== "pending" || acting) return;
    setActing("approve");
    setActionError(null);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/approve`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const updated: Proposal = await res.json();
      setProposal(updated);
      if (updated.kind === "skill") {
        goToPublish(updated.content, updated.name);
      } else if (updated.kind === "lab" && updated.created_resource_id) {
        navigate(`/lab/${updated.created_resource_id}`);
      }
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Something went wrong.");
      // Someone else (e.g. another tab) may have already resolved this — resync.
      fetchProposal();
    } finally {
      setActing(null);
    }
  };

  const handleDiscard = async () => {
    if (!proposal || proposal.status !== "pending" || acting) return;
    setActing("discard");
    setActionError(null);
    try {
      const res = await apiFetch(`/api/proposals/${proposal.id}/discard`, { method: "POST" });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }
      const updated: Proposal = await res.json();
      setProposal(updated);
    } catch (err) {
      setActionError(err instanceof Error ? err.message : "Something went wrong.");
      fetchProposal();
    } finally {
      setActing(null);
    }
  };

  const kind = proposal?.kind ?? initialKind;
  const meta = KIND_META[kind];
  const Icon = meta.icon;

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm">
        <div className="flex items-center gap-2 text-sm text-slate-400">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Loading draft…
        </div>
      </div>
    );
  }

  if (loadError || !proposal) {
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-600">
        {loadError || "Proposal not found."}
      </div>
    );
  }

  const renderActions = () => (
    <>
      {actionError && <div className="text-xs text-red-600 mt-2">{actionError}</div>}
      {proposal.status === "pending" ? (
        <div className="flex items-center gap-2 mt-3">
          <button
            type="button"
            onClick={handleApprove}
            disabled={!!acting}
            className="flex items-center gap-1.5 bg-slate-900 text-white text-xs font-medium rounded-lg px-3 py-1.5 hover:bg-slate-700 transition-colors disabled:opacity-50"
          >
            {acting === "approve" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <CheckCircle2 className="w-3.5 h-3.5" />}
            Approve
          </button>
          <button
            type="button"
            onClick={handleDiscard}
            disabled={!!acting}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800 px-3 py-1.5 rounded-lg hover:bg-slate-100 transition-colors disabled:opacity-50 font-medium"
          >
            {acting === "discard" ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <XCircle className="w-3.5 h-3.5" />}
            Discard
          </button>
        </div>
      ) : proposal.status === "discarded" ? (
        <div className="flex items-center gap-1.5 mt-3 text-xs text-slate-400 font-medium">
          <XCircle className="w-3.5 h-3.5" />
          Discarded
        </div>
      ) : (
        <div className="flex items-center gap-3 mt-3">
          <div className="flex items-center gap-1.5 text-xs text-emerald-600 font-medium">
            <CheckCircle2 className="w-3.5 h-3.5" />
            {proposal.kind === "skill" ? "Draft approved" : "Created"}
          </div>
          {proposal.kind === "skill" && (
            <button
              type="button"
              onClick={() => goToPublish(proposal.content, proposal.name)}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800 underline underline-offset-2 transition-colors"
            >
              Review &amp; publish
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
          {proposal.kind === "lab" && proposal.created_resource_id && (
            <button
              type="button"
              onClick={() => navigate(`/lab/${proposal.created_resource_id}`)}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800 underline underline-offset-2 transition-colors"
            >
              Open Lab
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
          {proposal.kind === "automation" && proposal.created_resource_id && (
            <button
              type="button"
              onClick={() => navigate(`/automations?id=${proposal.created_resource_id}`)}
              className="flex items-center gap-1 text-xs text-slate-500 hover:text-slate-800 underline underline-offset-2 transition-colors"
            >
              Open Automation
              <ExternalLink className="w-3 h-3" />
            </button>
          )}
        </div>
      )}
    </>
  );

  return (
    <>
      <div
        role="button"
        tabIndex={0}
        onClick={() => setModalOpen(true)}
        onKeyDown={e => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setModalOpen(true); } }}
        className="w-full text-left rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-sm max-w-md hover:border-slate-300 hover:shadow-md transition-all cursor-pointer"
      >
        {/* Header */}
        <div className="flex items-center gap-2 mb-2.5">
          <Icon className={`w-4 h-4 shrink-0 ${meta.iconClass}`} />
          <span className="text-sm font-semibold text-slate-800 truncate">{proposal.name}</span>
          <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 ml-auto">
            {meta.label}
          </span>
          <Maximize2 className="w-3 h-3 text-slate-300 shrink-0" />
        </div>

        {/* Body (clipped preview) */}
        <ProposalBody proposal={proposal} compact />

        {/* Actions — stop propagation so clicking them doesn't also open the modal */}
        <div onClick={e => e.stopPropagation()}>
          {renderActions()}
        </div>
      </div>

      {modalOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4"
          onClick={() => setModalOpen(false)}
        >
          <div
            className="bg-white rounded-2xl shadow-2xl w-full max-w-2xl border border-slate-200 flex flex-col max-h-[90vh]"
            onClick={e => e.stopPropagation()}
          >
            {/* Header */}
            <div className="px-6 pt-5 pb-4 border-b border-slate-100 flex items-start justify-between shrink-0">
              <div className="flex items-center gap-2 min-w-0">
                <Icon className={`w-4 h-4 shrink-0 ${meta.iconClass}`} />
                <h2 className="text-base font-bold text-slate-900 truncate">{proposal.name}</h2>
                <span className="text-[10px] font-semibold uppercase tracking-wider text-slate-400 shrink-0">
                  {meta.label}
                </span>
              </div>
              <button
                onClick={() => setModalOpen(false)}
                className="text-slate-400 hover:text-slate-700 text-lg leading-none ml-4 shrink-0"
              >
                ✕
              </button>
            </div>

            {/* Scrollable content */}
            <div className="px-6 py-5 overflow-y-auto flex-1">
              <ProposalBody proposal={proposal} compact={false} />
              {proposal.status === "pending" && proposal.kind === "skill" && (
                <SkillTestAndEdit proposal={proposal} onContentApplied={handleContentApplied} />
              )}
              {proposal.status === "pending" && proposal.kind === "automation" && (
                <AutomationTestAndEdit
                  proposal={proposal}
                  onContentApplied={handleContentApplied}
                  onCreatedResourceId={handleCreatedResourceId}
                />
              )}
            </div>

            {/* Footer actions */}
            <div className="px-6 pb-5 shrink-0 border-t border-slate-100 pt-4">
              {renderActions()}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
