import { useEffect, useRef, useState } from "react";
import { useNavigate, useSearchParams, useLocation, NavLink } from "react-router-dom";
import {
  CheckCircle2,
  Clock,
  AlertCircle,
  Loader2,
  ArrowLeft,
  ExternalLink,
  RefreshCw,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { clearPendingAvatar, peekPendingAvatar, pendingAvatarToFile } from "@/lib/pendingAvatar";
import { parseUTC, timeAgo } from "@/lib/time";
import { useBranding } from "@/lib/branding";

type OAuthStep =
  | "config"
  | "install"
  | "pending_approval"
  | "done"
  | "error"
  | "loading";

interface SessionStatus {
  session_id: string;
  step: OAuthStep;
  slack_app_id: string | null;
  agent_id: string | null;
  error: string | null;
  expires_at: string;
}

const STEP_LABELS: Record<string, string> = {
  install: "Installing to workspace…",
  pending_approval: "Waiting for admin approval",
  done: "Agent registered!",
  error: "Something went wrong",
};

const POLL_INTERVAL_MS = 3000;

export default function AgentOAuthPendingPage() {
  const branding = useBranding();
  const appName = branding.app_name;
  const [searchParams] = useSearchParams();
  const navigate = useNavigate();
  const location = useLocation();
  const sessionId = searchParams.get("session") ?? "";

  // Slack install URL passed from AgentRegisterPage via route state.
  // Present when the user just came from the registration form; absent when
  // Slack redirected back to our install-callback which then sent the user here.
  const slackInstallUrl: string | undefined =
    (location.state as { slack_install_url?: string } | null)?.slack_install_url;

  const [status, setStatus] = useState<SessionStatus | null>(null);
  const [step, setStep] = useState<OAuthStep>("loading");
  const [completing, setCompleting] = useState(false);
  const [completeError, setCompleteError] = useState<string | null>(null);
  const [slackOpened, setSlackOpened] = useState(false);
  const [markingPending, setMarkingPending] = useState(false);
  const [showApprovalPrompt, setShowApprovalPrompt] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const approvalPromptTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const avatarRetryRef = useRef<string | null>(null);

  const retryPendingAvatar = async (agentId: string) => {
    const b64 = peekPendingAvatar(agentId);
    if (!b64) return;
    try {
      const form = new FormData();
      form.append("file", pendingAvatarToFile(b64));
      const res = await apiFetch(`/api/agents/${agentId}/avatar`, { method: "POST", body: form });
      if (res.ok) clearPendingAvatar(agentId);
    } catch {
      // best-effort — Slack icon is cosmetic and can be set later from the agent page
    }
  };

  // ── Polling ───────────────────────────────────────────────────────────────

  const fetchStatus = async () => {
    if (!sessionId) {
      setStep("error");
      return;
    }
    try {
      const res = await apiFetch(`/api/slack-oauth/session/${sessionId}`);
      if (res.status === 404) {
        setStep("error");
        // Ensure there's always an error message even when status has never been set
        // (first poll on an already-expired session returns 404 immediately).
        setStatus({
          session_id: sessionId,
          step: "error",
          agent_id: status?.agent_id ?? null,
          slack_app_id: status?.slack_app_id ?? null,
          error: "Session not found or expired. If you were waiting for admin approval, click 'Back to agents' and use 'Resume Slack setup' on the agent page.",
          expires_at: status?.expires_at ?? "",
        });
        stopPolling();
        return;
      }
      if (!res.ok) {
        return; // transient error — keep polling
      }
      const data: SessionStatus = await res.json();
      setStatus(data);
      setStep(data.step);

      if (data.step === "done") {
        stopPolling();
        if (approvalPromptTimerRef.current) clearTimeout(approvalPromptTimerRef.current);
        setTimeout(() => navigate(`/agents/${data.agent_id}`), 1500);
      } else if (data.step === "error") {
        stopPolling();
        if (approvalPromptTimerRef.current) clearTimeout(approvalPromptTimerRef.current);
      } else if (data.step === "install" && !approvalPromptTimerRef.current) {
        // After 12 s on the install step, prompt the user to mark pending approval
        approvalPromptTimerRef.current = setTimeout(() => setShowApprovalPrompt(true), 12000);
      }
      // Keep polling in pending_approval so we auto-redirect once Complete Setup
      // finishes and the install-callback marks the session done.
    } catch {
      // network hiccup — keep polling
    }
  };

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  // ── Mark pending approval (user signals Slack showed approval screen) ──────

  const handleMarkPendingApproval = async () => {
    setMarkingPending(true);
    try {
      const res = await apiFetch(
        `/api/slack-oauth/session/${sessionId}/mark-pending-approval`,
        { method: "POST" }
      );
      if (res.ok) {
        setStep("pending_approval");
        setShowApprovalPrompt(false);
        // Keep polling — we need to detect when Complete Setup finishes.
      }
    } catch {
      // best-effort; the pending page will keep polling
    } finally {
      setMarkingPending(false);
    }
  };

  useEffect(() => {
    if (!sessionId) {
      setStep("error");
      return;
    }
    fetchStatus();
    pollRef.current = setInterval(fetchStatus, POLL_INTERVAL_MS);
    return () => {
      stopPolling();
      if (approvalPromptTimerRef.current) clearTimeout(approvalPromptTimerRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  // If auto-create failed to push the framed icon to Slack, retry once the
  // pending agent id is known (and again after install completes).
  useEffect(() => {
    const agentId = status?.agent_id;
    if (!agentId) return;
    const retryKey = `${agentId}:${step}`;
    if (avatarRetryRef.current === retryKey) return;
    if (step !== "install" && step !== "pending_approval" && step !== "done") return;
    avatarRetryRef.current = retryKey;
    void retryPendingAvatar(agentId);
  }, [status?.agent_id, step]);

  // Auto-open the Slack install URL in a new tab once on mount.
  // Using window.open keeps the user on this page so they aren't stranded
  // when Slack shows an admin-approval waiting screen without redirecting back.
  useEffect(() => {
    if (!slackInstallUrl) return;
    const tab = window.open(slackInstallUrl, "_blank", "noopener,noreferrer");
    setSlackOpened(tab !== null);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── Complete setup (after admin approval) ─────────────────────────────────

  const handleCompleteSetup = async () => {
    setCompleting(true);
    setCompleteError(null);
    try {
      const res = await apiFetch(
        `/api/slack-oauth/session/${sessionId}/retry-install`,
        { method: "POST" }
      );
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || `HTTP ${res.status}`);
      }
      const { redirect_url } = await res.json();
      // Open Slack in a new tab — keeps this page alive so polling can
      // detect the install-callback firing and auto-redirect to the agent.
      window.open(redirect_url, "_blank", "noopener,noreferrer");
      // Restart polling if it was stopped
      if (!pollRef.current) {
        pollRef.current = setInterval(fetchStatus, POLL_INTERVAL_MS);
      }
    } catch (err) {
      setCompleteError(err instanceof Error ? err.message : "Could not restart install.");
    } finally {
      setCompleting(false);
    }
  };

  // ── Render ────────────────────────────────────────────────────────────────

  return (
    <div className="h-full overflow-y-auto bg-[#F9FAFB] flex items-start justify-center pt-16 px-4">
      <div className="w-full max-w-md space-y-6">
        <NavLink
          to="/agents"
          className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 transition-colors"
        >
          <ArrowLeft className="w-3 h-3" /> Back to agents
        </NavLink>

        <div className="bg-white border border-slate-200 rounded-2xl p-6 space-y-6">
          <header className="space-y-1">
            <h1 className="text-lg font-bold text-slate-900">Creating your Slack app</h1>
            <p className="text-sm text-slate-500">
              {appName} is setting up a dedicated Slack bot for your agent.
            </p>
          </header>

          {/* Slack install banner — shown while waiting for the user to complete the Slack install screen */}
          {slackInstallUrl && step !== "done" && step !== "error" && (
            <div className="flex items-start gap-3 bg-violet-50 border border-violet-200 rounded-xl px-4 py-3">
              <ExternalLink className="w-4 h-4 text-violet-600 mt-0.5 shrink-0" />
              <div className="flex-1 min-w-0 space-y-1.5">
                <p className="text-sm font-semibold text-violet-900">
                  {slackOpened
                    ? "Slack opened in a new tab"
                    : "Slack couldn't open automatically"}
                </p>
                <p className="text-xs text-violet-700 leading-relaxed">
                  Complete the Slack install screen there.{" "}
                  If your workspace requires admin approval, close that tab and wait here — this page will update automatically when you're approved.
                </p>
                <a
                  href={slackInstallUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 text-xs font-semibold text-violet-700 underline underline-offset-2 hover:text-violet-900"
                >
                  {slackOpened ? "Re-open Slack tab" : "Open Slack install page"}
                  <ExternalLink className="w-3 h-3" />
                </a>
              </div>
            </div>
          )}

          {/* Step indicator */}
          <StepIndicator current={step} />

          {/* Draft agent link — visible as soon as agent_id is known */}
          {status?.agent_id && step !== "done" && (
            <div className="flex items-center gap-2 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
              <span>
                Your agent draft is saved.{" "}
                <NavLink
                  to={`/agents/${status.agent_id}`}
                  className="font-semibold text-slate-700 underline underline-offset-2 hover:text-slate-900"
                >
                  View draft agent →
                </NavLink>
              </span>
            </div>
          )}

          {/* State-specific content */}
          {step === "loading" && (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin" />
              Connecting…
            </div>
          )}

          {step === "install" && (
            <div className="space-y-3">
              <div className="flex items-center gap-2 text-sm text-slate-500">
                <Loader2 className="w-4 h-4 animate-spin" />
                {STEP_LABELS[step]}
              </div>
              {showApprovalPrompt && (
                <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                  <Clock className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
                  <div className="space-y-2 flex-1">
                    <p className="text-sm font-semibold text-amber-800">
                      Did Slack ask you to wait for admin approval?
                    </p>
                    <p className="text-xs text-amber-700 leading-relaxed">
                      If Slack showed "You've submitted a request" instead of completing the install,
                      click below. We'll hold your agent draft and show you the next steps.
                    </p>
                    <button
                      type="button"
                      onClick={handleMarkPendingApproval}
                      disabled={markingPending}
                      className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold bg-amber-700 hover:bg-amber-800 text-white rounded-lg transition-colors disabled:opacity-50"
                    >
                      {markingPending && <Loader2 className="w-3 h-3 animate-spin" />}
                      Yes, waiting for approval
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {step === "pending_approval" && (
            <PendingApprovalPanel
              slackAppId={status?.slack_app_id ?? null}
              agentId={status?.agent_id ?? null}
              completing={completing}
              completeError={completeError}
              onCompleteSetup={handleCompleteSetup}
            />
          )}

          {step === "done" && (
            <div className="space-y-4">
              <div className="flex items-center gap-2 text-sm text-emerald-700 font-medium">
                <CheckCircle2 className="w-4 h-4 text-emerald-600 shrink-0" />
                Agent registered! Redirecting to your agent…
              </div>
              <div className="bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3 space-y-2">
                <p className="text-xs font-semibold text-emerald-800">Next steps after redirect</p>
                <ol className="list-decimal list-inside space-y-1 text-xs text-emerald-800 leading-relaxed">
                  <li>Click <strong>Activate</strong> on the agent page to enable it.</li>
                  <li>In Slack, type <code className="font-mono bg-emerald-100 px-1 rounded">/invite @YourBot</code> in a channel to add it.</li>
                  <li>@mention the bot to send it a message and verify it responds.</li>
                </ol>
              </div>
            </div>
          )}

          {step === "error" && (
            <ErrorPanel
              message={status?.error ?? "An unknown error occurred."}
            />
          )}
        </div>

        {sessionId && step !== "done" && step !== "error" && (
          <p className="text-center text-[11px] text-slate-400">
            Session ID: <code className="font-mono">{sessionId.slice(0, 8)}…</code>
            {status?.expires_at && (
              <> · expires {new Date(status.expires_at).toLocaleTimeString()}</>
            )}
          </p>
        )}
      </div>
    </div>
  );
}

// ── Step indicator ────────────────────────────────────────────────────────────

const ORDERED_STEPS: Array<{ key: OAuthStep; label: string }> = [
  { key: "install", label: "Create Slack app" },
  { key: "pending_approval", label: "Install to workspace" },
  { key: "done", label: "Register agent" },
];

function StepIndicator({ current }: { current: OAuthStep }) {
  const doneIndex =
    current === "done"
      ? ORDERED_STEPS.length
      : current === "pending_approval"
      ? 1
      : current === "install"
      ? 0
      : -1;

  return (
    <ol className="space-y-2">
      {ORDERED_STEPS.map(({ key, label }, idx) => {
        const isDone = idx < doneIndex || current === "done";
        const isActive = idx === doneIndex && current !== "done" && current !== "error";
        return (
          <li key={key} className="flex items-center gap-3">
            <span
              className={`w-5 h-5 rounded-full border-2 flex items-center justify-center shrink-0 text-[10px] font-bold ${
                isDone
                  ? "border-emerald-500 bg-emerald-500 text-white"
                  : isActive
                  ? "border-slate-900 bg-slate-900 text-white"
                  : "border-slate-200 text-slate-400"
              }`}
            >
              {isDone ? (
                <CheckCircle2 className="w-3 h-3" />
              ) : isActive ? (
                <Loader2 className="w-3 h-3 animate-spin" />
              ) : (
                idx + 1
              )}
            </span>
            <span
              className={`text-sm ${
                isDone
                  ? "text-emerald-700 font-medium"
                  : isActive
                  ? "text-slate-900 font-semibold"
                  : "text-slate-400"
              }`}
            >
              {label}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

// ── Pending approval panel ────────────────────────────────────────────────────

function PendingApprovalPanel({
  slackAppId,
  agentId,
  completing,
  completeError,
  onCompleteSetup,
}: {
  slackAppId: string | null;
  agentId: string | null;
  completing: boolean;
  completeError: string | null;
  onCompleteSetup: () => void;
}) {
  return (
    <div className="space-y-4">
      {/* Status */}
      <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
        <Clock className="w-4 h-4 text-amber-600 mt-0.5 shrink-0" />
        <div className="text-sm text-amber-800 space-y-1">
          <p className="font-semibold">Waiting for workspace admin approval</p>
          <p className="text-xs leading-relaxed">
            Your Slack bot request is with your workspace admins.
            Your agent draft is safely saved — you can close this tab and come back later.
          </p>
        </div>
      </div>

      {/* What happens next */}
      <div className="bg-slate-50 border border-slate-200 rounded-xl px-4 py-3 space-y-2">
        <p className="text-xs font-semibold text-slate-700">What happens next</p>
        <ol className="space-y-2 text-xs text-slate-600 leading-relaxed list-none">
          <li className="flex gap-2">
            <span className="text-emerald-600 font-bold shrink-0">1.</span>
            <span>
              <strong>Admin approves</strong> — Slack sends you a DM:{" "}
              <em>"Your request to install [App] has been approved."</em>
            </span>
          </li>
          <li className="flex gap-2">
            <span className="text-emerald-600 font-bold shrink-0">2.</span>
            <span>
              Return here and click <strong>Complete Slack install</strong> below.
              Slack will open in a new tab — authorise the install.
            </span>
          </li>
          <li className="flex gap-2">
            <span className="text-emerald-600 font-bold shrink-0">3.</span>
            <span>
              This page <strong>auto-updates</strong> once the install finishes and
              redirects you to your live agent.
            </span>
          </li>
        </ol>
      </div>

      {/* Rejection info */}
      <div className="flex items-start gap-2 text-xs text-slate-500 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2.5">
        <AlertCircle className="w-3.5 h-3.5 mt-0.5 text-slate-400 shrink-0" />
        <span>
          <strong className="text-slate-600">If rejected:</strong> Slack will DM you.
          Your draft agent remains saved.{" "}
          {agentId && (
            <NavLink
              to={`/agents/${agentId}`}
              className="underline text-slate-600 hover:text-slate-900 font-medium"
            >
              View draft agent
            </NavLink>
          )}{" "}
          — you can decommission it and{" "}
          <NavLink to="/agents/new" className="underline text-slate-600 hover:text-slate-900 font-medium">
            start fresh
          </NavLink>
          .
        </span>
      </div>

      {slackAppId && (
        <a
          href={`https://api.slack.com/apps/${slackAppId}`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-700 transition-colors underline underline-offset-2"
        >
          View app in Slack dashboard
          <ExternalLink className="w-3 h-3" />
        </a>
      )}

      {completeError && (
        <div className="flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
          <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
          {completeError}
        </div>
      )}

      <button
        type="button"
        onClick={onCompleteSetup}
        disabled={completing}
        className="w-full flex items-center justify-center gap-2 px-4 py-2.5 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50"
      >
        {completing ? (
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
        ) : (
          <ExternalLink className="w-3.5 h-3.5" />
        )}
        Complete Slack install
      </button>
      <p className="text-[10px] text-slate-400 text-center">
        Opens Slack in a new tab. This page keeps watching and will auto-redirect when done.
      </p>
    </div>
  );
}

// ── Error panel ───────────────────────────────────────────────────────────────

function ErrorPanel({ message }: { message: string }) {
  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
        <AlertCircle className="w-4 h-4 text-red-600 mt-0.5 shrink-0" />
        <div className="text-sm text-red-800 space-y-1">
          <p className="font-semibold">Setup failed</p>
          <p className="text-xs">{message}</p>
        </div>
      </div>
      <NavLink
        to="/agents/new"
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-slate-900 underline underline-offset-2 hover:no-underline transition-all"
      >
        <ArrowLeft className="w-3.5 h-3.5" />
        Try again
      </NavLink>
    </div>
  );
}
