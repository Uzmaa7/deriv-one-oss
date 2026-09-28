import { useState, useEffect, type ReactNode, type FormEvent } from "react";
import { NavLink } from "react-router-dom";
import {
  CheckCircle2,
  XCircle,
  Clock,
  AlertCircle,
  RefreshCw,
  ArrowLeft,
  ShieldOff,
  CalendarClock,
  ClipboardList,
  Users,
  ChevronDown,
  ChevronUp,
  Lock,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { parseUTC, timeAgo } from "@/lib/time";

// ── Types ─────────────────────────────────────────────────────────────────────

interface Grant {
  id: string;
  user_email: string;
  user_name: string | null;
  user_department: string;
  skill_slug: string;
  skill_name: string;
  skill_department: string;
  skill_risk_level: "standard" | "elevated" | "critical";
  justification: string;
  requested_at: string;
  status: string;
  decided_by_email: string | null;
  decided_by_name: string | null;
  decided_at: string | null;
  deny_reason: string | null;
  grant_type: string | null;
  expires_at: string | null;
  restrictions: string | null;
  last_used_at: string | null;
  usage_count: number;
  revoked_at: string | null;
  revoke_reason: string | null;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function relative(iso: string | null): string {
  if (!iso) return "—";
  const diff = Date.now() - parseUTC(iso).getTime();
  const mins = Math.floor(diff / 60000);
  if (mins < 2) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  return `${days}d ago`;
}

function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return parseUTC(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

const RISK_COLORS = {
  standard: "text-emerald-700 bg-emerald-50 border-emerald-200",
  elevated: "text-amber-700 bg-amber-50 border-amber-200",
  critical: "text-red-700 bg-red-50 border-red-200",
};

// ── Section component ─────────────────────────────────────────────────────────

function Section({ title, icon, count, children }: { title: string; icon: ReactNode; count?: number; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div className="flex items-center gap-2">
        {icon}
        <h2 className="text-sm font-bold text-slate-900">{title}</h2>
        {count !== undefined && (
          <span className="text-xs text-slate-500 bg-slate-100 rounded-full px-2 py-0.5">{count}</span>
        )}
      </div>
      {children}
    </section>
  );
}

// ── Pending request card ──────────────────────────────────────────────────────

function PendingCard({
  g,
  onApprove,
  onDeny,
}: {
  g: Grant;
  key?: string | number;
  onApprove: (id: string, days: number | null) => void | Promise<void>;
  onDeny: (id: string) => void | Promise<void>;
}) {
  const [expanded, setExpanded] = useState(false);
  const [denyReason, setDenyReason] = useState("");
  const [denyMode, setDenyMode] = useState(false);

  return (
    <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
      <div className="px-4 py-3 flex items-start gap-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span className="text-sm font-bold text-slate-900">{g.user_name || g.user_email}</span>
            <span className="text-xs text-slate-400">from <strong>{g.user_department}</strong></span>
            <span className="text-xs text-slate-400">→</span>
            <span className="text-sm font-semibold text-slate-700">{g.skill_slug}</span>
            <span className={`text-[9px] font-bold uppercase px-1.5 py-0.5 rounded border ${RISK_COLORS[g.skill_risk_level]}`}>
              {g.skill_risk_level}
            </span>
          </div>
          <div className="text-xs text-slate-500 mt-0.5">{relative(g.requested_at)}</div>
          <div className="mt-2 text-xs text-slate-700 leading-relaxed bg-slate-50 rounded-lg px-3 py-2 border border-slate-100">
            "{g.justification}"
          </div>
        </div>
        <button onClick={() => setExpanded(e => !e)} className="text-slate-400 hover:text-slate-700 shrink-0 mt-0.5">
          {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
        </button>
      </div>

      {expanded && !denyMode && (
        <div className="border-t border-slate-100 px-4 py-3 bg-slate-50 flex flex-wrap gap-2">
          <button
            onClick={() => onApprove(g.id, 30)}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 transition-colors"
          >
            <CheckCircle2 className="w-3.5 h-3.5" /> Approve (30 days)
          </button>
          <button
            onClick={() => onApprove(g.id, 90)}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 bg-emerald-50 text-emerald-700 border border-emerald-300 rounded-lg hover:bg-emerald-100 transition-colors"
          >
            Approve (90 days)
          </button>
          <button
            onClick={() => onApprove(g.id, null)}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 bg-slate-100 text-slate-700 border border-slate-300 rounded-lg hover:bg-slate-200 transition-colors"
          >
            Approve (permanent)
          </button>
          <button
            onClick={() => setDenyMode(true)}
            className="flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 text-red-600 border border-red-200 rounded-lg hover:bg-red-50 transition-colors"
          >
            <XCircle className="w-3.5 h-3.5" /> Deny
          </button>
        </div>
      )}

      {denyMode && (
        <div className="border-t border-slate-100 px-4 py-3 bg-red-50 space-y-2">
          <textarea
            rows={2}
            value={denyReason}
            onChange={e => setDenyReason(e.target.value)}
            placeholder="Reason for denial (required)"
            className="w-full text-xs px-3 py-2 border border-red-200 rounded-lg focus:outline-none focus:border-red-400 bg-white resize-none"
          />
          <div className="flex gap-2">
            <button
              onClick={() => { if (denyReason.trim()) onDeny(g.id); }}
              className="text-xs font-bold px-3 py-1.5 bg-red-600 text-white rounded-lg hover:bg-red-700 transition-colors"
            >
              Confirm Deny
            </button>
            <button
              onClick={() => { setDenyMode(false); setDenyReason(""); }}
              className="text-xs px-3 py-1.5 text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Active grant row ──────────────────────────────────────────────────────────

function GrantRow({ g, onRevoke }: { g: Grant; key?: string | number; onRevoke: (id: string) => void | Promise<void> }) {
  const isExpiring = g.expires_at && parseUTC(g.expires_at) < new Date(Date.now() + 14 * 86400000);
  return (
    <tr className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
      <td className="px-4 py-3 text-sm">
        <div className="font-semibold text-slate-900">{g.user_name || g.user_email}</div>
        <div className="text-xs text-slate-500">{g.user_department}</div>
      </td>
      <td className="px-4 py-3">
        <div className="text-sm font-mono text-slate-700">{g.skill_slug}</div>
        <div className="text-xs text-slate-500">{g.skill_department}</div>
      </td>
      <td className="px-4 py-3 text-xs text-slate-600">{g.decided_by_name || g.decided_by_email || "—"}</td>
      <td className="px-4 py-3 text-xs">
        <span className={`font-medium ${isExpiring ? "text-amber-700" : "text-slate-600"}`}>
          {g.expires_at ? formatDate(g.expires_at) : "Permanent"}
        </span>
        {isExpiring && <div className="text-[10px] text-amber-600">Expiring soon</div>}
      </td>
      <td className="px-4 py-3 text-xs text-slate-500">{g.usage_count} sessions</td>
      <td className="px-4 py-3">
        <button
          onClick={() => onRevoke(g.id)}
          className="text-[10px] font-semibold text-red-600 border border-red-200 rounded px-2 py-0.5 hover:bg-red-50 transition-colors"
        >
          Revoke
        </button>
      </td>
    </tr>
  );
}

// ── Revoke modal ──────────────────────────────────────────────────────────────

function RevokeModal({ grantId, onClose, onRevoked }: { grantId: string; onClose: () => void; onRevoked: () => void }) {
  const [reason, setReason] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!reason.trim()) { setError("Reason is required."); return; }
    setSubmitting(true);
    try {
      const res = await apiFetch(`/api/admin/access/${grantId}/revoke`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: reason.trim() }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d?.detail || `Error ${res.status}`);
      }
      onRevoked();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-sm border border-slate-200 p-6 space-y-4">
        <h2 className="text-base font-bold text-slate-900">Revoke Grant</h2>
        <form onSubmit={handleSubmit} className="space-y-3">
          <textarea
            rows={3}
            value={reason}
            onChange={e => setReason(e.target.value)}
            placeholder="Reason for revocation"
            className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400 resize-none"
          />
          {error && <p className="text-xs text-red-600">{error}</p>}
          <div className="flex gap-2">
            <button type="button" onClick={onClose}
              className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50">
              Cancel
            </button>
            <button type="submit" disabled={submitting}
              className="flex-1 px-4 py-2 text-sm font-bold bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-50">
              {submitting ? "Revoking…" : "Revoke"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function AdminAccessPage() {
  const [pending, setPending] = useState<Grant[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [expiring, setExpiring] = useState<Grant[]>([]);
  const [audit, setAudit] = useState<Grant[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revokeId, setRevokeId] = useState<string | null>(null);
  const [activeSection, setActiveSection] = useState<"pending" | "grants" | "expiring" | "audit">("pending");

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [pRes, gRes, eRes, aRes] = await Promise.all([
        apiFetch("/api/admin/access/pending"),
        apiFetch("/api/admin/access/grants"),
        apiFetch("/api/admin/access/expiring"),
        apiFetch("/api/admin/access/audit"),
      ]);
      if (!pRes.ok || !gRes.ok || !eRes.ok || !aRes.ok) throw new Error("Failed to load access data.");
      const [p, g, e, a] = await Promise.all([pRes.json(), gRes.json(), eRes.json(), aRes.json()]);
      setPending(p.pending ?? []);
      setGrants(g.grants ?? []);
      setExpiring(e.expiring ?? []);
      setAudit(a.audit ?? []);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Error.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); }, []);

  const handleApprove = async (id: string, days: number | null) => {
    const body = days
      ? { grant_type: "time_limited", expires_in_days: days }
      : { grant_type: "permanent" };
    const res = await apiFetch(`/api/admin/access/${id}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (res.ok) load();
  };

  const handleDeny = async (id: string) => {
    const g = pending.find(p => p.id === id);
    if (!g) return;
    const res = await apiFetch(`/api/admin/access/${id}/deny`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reason: "Denied by admin." }),
    });
    if (res.ok) load();
  };

  const NAV = [
    { id: "pending" as const, label: "Pending", icon: <Clock className="w-3.5 h-3.5" />, count: pending.length },
    { id: "grants" as const, label: "Active Grants", icon: <CheckCircle2 className="w-3.5 h-3.5" />, count: grants.length },
    { id: "expiring" as const, label: "Expiring Soon", icon: <CalendarClock className="w-3.5 h-3.5" />, count: expiring.length },
    { id: "audit" as const, label: "Audit Log", icon: <ClipboardList className="w-3.5 h-3.5" />, count: audit.length },
  ];

  return (
    <div className="h-full overflow-y-auto bg-[#F9FAFB]">
      <div className="max-w-5xl mx-auto px-6 py-6 space-y-6">

        <div className="flex items-center gap-4">
          <NavLink to="/" className="inline-flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900 transition-colors">
            <ArrowLeft className="w-3 h-3" /> Back
          </NavLink>
          <div className="flex-1">
            <h1 className="text-xl font-bold text-slate-900 flex items-center gap-2">
              <Lock className="w-5 h-5" /> Skills Access Control
            </h1>
            <p className="text-sm text-slate-500">Review cross-department skill access requests and manage active grants.</p>
          </div>
          <button onClick={load} className="flex items-center gap-1.5 text-xs text-slate-500 border border-slate-200 rounded-lg px-3 py-1.5 hover:bg-slate-50 transition-colors">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>

        {error && (
          <div className="flex items-center gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
            <AlertCircle className="w-4 h-4 shrink-0" /> {error}
          </div>
        )}

        {/* Tab nav */}
        <div className="flex gap-1 bg-white border border-slate-200 rounded-xl p-1">
          {NAV.map(n => (
            <button
              key={n.id}
              onClick={() => setActiveSection(n.id)}
              className={`flex-1 flex items-center justify-center gap-1.5 px-3 py-2 text-xs font-semibold rounded-lg transition-colors ${
                activeSection === n.id ? "bg-slate-900 text-white" : "text-slate-500 hover:text-slate-900 hover:bg-slate-50"
              }`}
            >
              {n.icon} {n.label}
              {n.count > 0 && (
                <span className={`rounded-full px-1.5 text-[10px] font-bold ${activeSection === n.id ? "bg-white/20 text-white" : "bg-slate-100 text-slate-600"}`}>
                  {n.count}
                </span>
              )}
            </button>
          ))}
        </div>

        {loading ? (
          <div className="text-center py-16 text-slate-500 text-sm">Loading…</div>
        ) : (
          <>
            {/* Pending requests */}
            {activeSection === "pending" && (
              <Section title="Pending Requests" icon={<Clock className="w-4 h-4 text-amber-600" />} count={pending.length}>
                {pending.length === 0 ? (
                  <div className="text-center py-12 bg-white border border-slate-200 rounded-xl text-slate-500 text-sm">
                    No pending requests. 🎉
                  </div>
                ) : (
                  <div className="space-y-3">
                    {pending.map(g => (
                      <PendingCard
                          key={g.id}
                          g={g}
                          onApprove={(id, days) => void handleApprove(id, days)}
                          onDeny={(id) => void handleDeny(id)}
                        />
                    ))}
                  </div>
                )}
              </Section>
            )}

            {/* Active grants */}
            {activeSection === "grants" && (
              <Section title="Active Grants" icon={<Users className="w-4 h-4 text-emerald-600" />} count={grants.length}>
                {grants.length === 0 ? (
                  <div className="text-center py-12 bg-white border border-slate-200 rounded-xl text-slate-500 text-sm">No active grants.</div>
                ) : (
                  <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-slate-100 bg-slate-50">
                          {["User", "Skill", "Granted By", "Expires", "Usage", ""].map(h => (
                            <th key={h} className="px-4 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {grants.map(g => (
                          <GrantRow key={g.id} g={g} onRevoke={(id) => setRevokeId(id)} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}

            {/* Expiring */}
            {activeSection === "expiring" && (
              <Section title="Expiring in Next 14 Days" icon={<CalendarClock className="w-4 h-4 text-amber-600" />} count={expiring.length}>
                {expiring.length === 0 ? (
                  <div className="text-center py-12 bg-white border border-slate-200 rounded-xl text-slate-500 text-sm">No grants expiring soon.</div>
                ) : (
                  <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-slate-100 bg-slate-50">
                          {["User", "Skill", "Expires", "Usage", ""].map(h => (
                            <th key={h} className="px-4 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {expiring.map(g => (
                          <GrantRow key={g.id} g={g} onRevoke={(id) => setRevokeId(id)} />
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}

            {/* Audit log */}
            {activeSection === "audit" && (
              <Section title="Audit Log" icon={<ClipboardList className="w-4 h-4 text-slate-600" />} count={audit.length}>
                {audit.length === 0 ? (
                  <div className="text-center py-12 bg-white border border-slate-200 rounded-xl text-slate-500 text-sm">No audit records.</div>
                ) : (
                  <div className="bg-white border border-slate-200 rounded-xl overflow-hidden">
                    <table className="w-full text-left">
                      <thead>
                        <tr className="border-b border-slate-100 bg-slate-50">
                          {["User", "Skill", "Status", "Decided By", "Date", "Details"].map(h => (
                            <th key={h} className="px-4 py-2.5 text-[10px] font-bold text-slate-400 uppercase tracking-wider">{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {audit.map(g => (
                          <tr key={g.id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50 transition-colors">
                            <td className="px-4 py-3 text-sm">
                              <div className="font-medium text-slate-800">{g.user_name || g.user_email}</div>
                              <div className="text-xs text-slate-500">{g.user_department}</div>
                            </td>
                            <td className="px-4 py-3 text-xs font-mono text-slate-700">{g.skill_slug}</td>
                            <td className="px-4 py-3">
                              <StatusBadge status={g.status} />
                            </td>
                            <td className="px-4 py-3 text-xs text-slate-600">{g.decided_by_name || g.decided_by_email || "—"}</td>
                            <td className="px-4 py-3 text-xs text-slate-500">{relative(g.decided_at)}</td>
                            <td className="px-4 py-3 text-xs text-slate-500">
                              {g.deny_reason && <span className="text-red-600">"{g.deny_reason}"</span>}
                              {g.revoke_reason && <span className="text-orange-600">"{g.revoke_reason}"</span>}
                              {g.expires_at && <span className="text-slate-500">until {formatDate(g.expires_at)}</span>}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </Section>
            )}
          </>
        )}
      </div>

      {revokeId && (
        <RevokeModal
          grantId={revokeId}
          onClose={() => setRevokeId(null)}
          onRevoked={() => { setRevokeId(null); load(); }}
        />
      )}
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const map: Record<string, string> = {
    approved: "text-emerald-700 bg-emerald-50 border-emerald-200",
    denied: "text-red-700 bg-red-50 border-red-200",
    expired: "text-slate-500 bg-slate-100 border-slate-200",
    revoked: "text-orange-700 bg-orange-50 border-orange-200",
  };
  return (
    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border text-[10px] font-bold uppercase tracking-wider ${map[status] ?? "text-slate-500 bg-slate-100 border-slate-200"}`}>
      {status}
    </span>
  );
}
