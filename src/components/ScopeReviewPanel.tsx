import { useState } from "react";
import { Loader2, ShieldCheck, Sparkles, AlertCircle, Check, X, Plus } from "lucide-react";
import { apiFetch } from "@/lib/api";

// ── Types (mirror backend scope_router.ScopeProposal + analyze-scopes response) ──

export interface ScopeProposal {
  scope: string;
  reason: string;
  confidence: number;
  locked: boolean;
  description?: string;
}

export interface ScopeReport {
  report_id: string;
  proposals: ScopeProposal[];
  base: string[];
  approved: string[];
  notes?: string;
}

interface Verdict {
  scope: string;
  allowed: boolean;
  reason: string;
  confidence: number;
}

interface Props {
  report: ScopeReport;
  /** Called whenever the user's approved selection changes (scopes the user kept). */
  onApprovedChange: (approved: string[]) => void;
  /** The user's current removal-only selection (drives the disabled state of approve). */
  approved: string[];
  onApprove: () => void;
  approving: boolean;
  analyzing: boolean;
  avatarMissing?: boolean;
}

// Full catalog of scopes the UI may show as "requestable" suggestions. The
// server is the allowlist; this is purely for the typeahead.
const SUGGESTED_REQUESTABLE = [
  "files:write", "files:read", "search:read",
  "channels:history", "channels:read", "channels:join",
  "groups:history", "groups:read",
  "im:read", "im:history", "im:write",
  "mpim:history", "mpim:read", "team:read",
  "reactions:write", "reactions:read", "pins:write", "bookmarks:write",
  "reminders:write", "usergroups:read", "usergroups:write",
];

/**
 * Scope review step shown in the auto-create flow AFTER "Analyze scopes".
 *
 * Rules enforced by the UI (and re-enforced server-side):
 *  - Base scopes: shown, locked (cannot remove).
 *  - AI-proposed conditional scopes: checked by default; user can UNCHECK
 *    (remove) them. Cannot check new ones here.
 *  - To add a scope the AI didn't propose, the user types it and submits a
 *    "request"; the AI adjudicates allow/deny.
 */
export default function ScopeReviewPanel({
  report,
  onApprovedChange,
  approved,
  onApprove,
  approving,
  analyzing,
  avatarMissing = false,
}: Props) {
  const [requestInput, setRequestInput] = useState("");
  const [requesting, setRequesting] = useState(false);
  const [verdicts, setVerdicts] = useState<Verdict[]>([]);
  const [requestError, setRequestError] = useState<string | null>(null);

  const baseSet = new Set(report.base);
  // Proposals split into base (locked) and conditional (removable).
  const baseProposals = report.proposals.filter(p => p.locked || baseSet.has(p.scope));
  const conditionalProposals = report.proposals.filter(p => !p.locked && !baseSet.has(p.scope));

  // Anything approved but not in proposals (i.e. AI-allowed requests) — shown
  // as removable too, with the AI's reason if we have it.
  const approvedNotProposed = approved.filter(
    s => !baseSet.has(s) && !report.proposals.some(p => p.scope === s)
  );

  const toggleScope = (scope: string) => {
    if (baseSet.has(scope)) return; // locked
    const next = approved.includes(scope)
      ? approved.filter(s => s !== scope)   // remove
      : [...approved, scope];               // (re-)add a previously-removed proposal
    onApprovedChange(next);
  };

  const handleRequest = async () => {
    const scope = requestInput.trim();
    if (!scope || requesting) return;
    setRequesting(true);
    setRequestError(null);
    try {
      const res = await apiFetch("/api/slack-oauth/analyze-scopes/request", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ report_id: report.report_id, requested_scopes: [scope] }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail || `Request failed (HTTP ${res.status}).`);
      }
      const data = await res.json();
      setVerdicts(prev => [...(data.verdicts || []), ...prev]);
      // The server returns the updated approved set; sync upward.
      onApprovedChange(data.approved || []);
      setRequestInput("");
    } catch (err) {
      setRequestError(err instanceof Error ? err.message : "Could not evaluate request.");
    } finally {
      setRequesting(false);
    }
  };

  return (
    <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-5">
      <div className="flex items-start gap-3">
        <ShieldCheck className="w-5 h-5 text-violet-600 mt-0.5 shrink-0" />
        <div className="flex-1">
          <h3 className="text-sm font-bold text-slate-900">Review Slack scopes</h3>
          <p className="text-xs text-slate-500 mt-1 leading-relaxed">
            AI analyzed this agent&apos;s usecase and proposes the <strong>minimum</strong> Slack
            permissions it needs. Required scopes cover mentions, replies, and channel listing;
            message history and DM access are optional and only included when your usecase needs them.
            You can <strong>remove</strong> any optional scope, but cannot add one here.
            If a needed scope is missing, request it below — the system decides whether to allow it.
          </p>
          {report.notes ? (
            <p className="text-[11px] text-slate-400 mt-1.5 italic">{report.notes}</p>
          ) : null}
        </div>
      </div>

      {/* Base / locked scopes */}
      {baseProposals.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Required · cannot be removed
          </p>
          {baseProposals.map(p => (
            <ScopeRow key={p.scope} proposal={p} checked disabled onToggle={() => {}} />
          ))}
        </div>
      )}

      {/* Conditional / removable scopes */}
      {conditionalProposals.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">
            Optional · proposed by AI · you may remove
          </p>
          {conditionalProposals.map(p => (
            <ScopeRow
              key={p.scope}
              proposal={p}
              checked={approved.includes(p.scope)}
              onToggle={() => toggleScope(p.scope)}
            />
          ))}
        </div>
      )}

      {/* AI-allowed requests (not in original proposal) */}
      {approvedNotProposed.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-[11px] font-semibold uppercase tracking-wide text-emerald-600">
            Added via your request · AI-approved
          </p>
          {approvedNotProposed.map(scope => (
            <div
              key={scope}
              className="flex items-start gap-2.5 p-2.5 rounded-lg border border-emerald-200 bg-emerald-50/50"
            >
              <button
                type="button"
                onClick={() => toggleScope(scope)}
                className="mt-0.5 w-4 h-4 rounded border border-emerald-400 bg-emerald-400 flex items-center justify-center shrink-0"
              >
                <Check className="w-3 h-3 text-white" />
              </button>
              <div className="flex-1 min-w-0">
                <span className="text-xs font-mono font-semibold text-slate-900">{scope}</span>
                <p className="text-[11px] text-slate-500 mt-0.5">
                  Approved at your request. You can remove it.
                </p>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Request additional scope */}
      <div className="border-t border-slate-100 pt-4 space-y-2">
        <label className="text-xs font-semibold text-slate-700">
          Need a scope that's missing? Request one
        </label>
        <div className="flex gap-2">
          <input
            type="text"
            list="scope-suggestions"
            value={requestInput}
            onChange={e => setRequestInput(e.target.value)}
            placeholder="e.g. files:write"
            spellCheck={false}
            className="flex-1 text-xs px-2.5 py-1.5 border border-slate-200 rounded-lg font-mono focus:outline-none focus:border-slate-400"
          />
          <datalist id="scope-suggestions">
            {SUGGESTED_REQUESTABLE.map(s => <option key={s} value={s} />)}
          </datalist>
          <button
            type="button"
            onClick={handleRequest}
            disabled={requesting || !requestInput.trim()}
            className="flex items-center gap-1 px-3 py-1.5 text-xs font-semibold bg-slate-100 hover:bg-slate-200 text-slate-700 rounded-lg transition-colors disabled:opacity-50"
          >
            {requesting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
            Request
          </button>
        </div>
        <p className="text-[11px] text-slate-400">
          The AI decides whether to grant it. Denied requests are never added.
        </p>

        {requestError && (
          <div className="flex items-start gap-2 text-red-600 text-[11px]">
            <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
            <span>{requestError}</span>
          </div>
        )}

        {/* Verdict history */}
        {verdicts.length > 0 && (
          <div className="space-y-1 mt-1">
            {verdicts.map((v, i) => (
              <div
                key={`${v.scope}-${i}`}
                className={`flex items-start gap-2 p-2 rounded-lg text-[11px] ${
                  v.allowed ? "bg-emerald-50 text-emerald-700" : "bg-red-50 text-red-600"
                }`}
              >
                {v.allowed ? <Check className="w-3.5 h-3.5 mt-0.5 shrink-0" /> : <X className="w-3.5 h-3.5 mt-0.5 shrink-0" />}
                <div>
                  <span className="font-mono font-semibold">{v.scope}</span>
                  {" — "}
                  <span>{v.allowed ? "allowed" : "denied"}</span>
                  <p className="text-[11px] opacity-80 mt-0.5">{v.reason}</p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* Approve & connect */}
      <div className="border-t border-slate-100 pt-4 flex items-center justify-between gap-3">
        <p className="text-[11px] text-slate-400">
          {approved.length} scope{approved.length === 1 ? "" : "s"} will be requested for this bot.
        </p>
        <button
          type="button"
          onClick={onApprove}
          disabled={approving || analyzing || avatarMissing}
          className="flex items-center gap-2 px-5 py-2 text-sm font-bold bg-violet-600 hover:bg-violet-700 text-white rounded-lg transition-colors disabled:opacity-50"
        >
          {approving ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
          Approve &amp; connect Slack
        </button>
      </div>
    </div>
  );
}

function ScopeRow({
  proposal,
  checked,
  disabled,
  onToggle,
}: {
  proposal: ScopeProposal;
  checked: boolean;
  disabled?: boolean;
  onToggle: () => void;
}) {
  return (
    <div
      className={`flex items-start gap-2.5 p-2.5 rounded-lg border transition-colors ${
        disabled
          ? "border-slate-200 bg-slate-50/60"
          : checked
            ? "border-slate-300 bg-white"
            : "border-slate-200 bg-slate-50/40"
      }`}
    >
      <button
        type="button"
        onClick={disabled ? undefined : onToggle}
        disabled={disabled}
        className={`mt-0.5 w-4 h-4 rounded border flex items-center justify-center shrink-0 ${
          disabled
            ? "border-slate-300 bg-slate-300 cursor-not-allowed"
            : checked
              ? "border-violet-500 bg-violet-500"
              : "border-slate-300 bg-white"
        }`}
      >
        {checked && <Check className="w-3 h-3 text-white" />}
      </button>
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-mono font-semibold text-slate-900">{proposal.scope}</span>
          {disabled && (
            <span className="text-[9px] font-bold uppercase tracking-wide text-slate-400 bg-slate-200 px-1.5 py-0.5 rounded">
              Required
            </span>
          )}
          {!disabled && proposal.confidence != null && (
            <span className="text-[10px] text-slate-400">
              {(proposal.confidence * 100).toFixed(0)}% confidence
            </span>
          )}
        </div>
        <p className="text-[11px] text-slate-500 mt-0.5 leading-relaxed">
          {proposal.description || proposal.reason}
        </p>
        {!disabled && proposal.reason && (
          <p className="text-[11px] text-slate-400 mt-0.5 italic">Why: {proposal.reason}</p>
        )}
      </div>
    </div>
  );
}
