'use strict';

import {
  useState,
  useEffect,
  useRef,
  useCallback,
  type ClipboardEvent,
  type FormEvent,
  type KeyboardEvent,
} from "react";
import {
  AlertCircle,
  CheckCircle2,
  Loader2,
  Search,
  ShieldCheck,
  UserCheck,
  X,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface EmployeeResult {
  email: string;
  name: string;
  job_title: string;
  department: string;
  status: string;
}

interface GrantResult {
  grant_id: string;
  user: { email: string; name: string; department: string; role: string };
  skill_slug: string;
  skill_name: string;
  grant_type: string;
  expires_at: string | null;
  granted_by: string;
}

interface BulkGrantResponse {
  granted: number;
  failed: number;
  grants: GrantResult[];
  errors: Array<{ email: string; error: string }>;
  skill_slug?: string;
  skill_name?: string;
  grant_type?: string;
  expires_at?: string | null;
  granted_by?: string;
}

interface Props {
  skillSlug: string;
  skillName?: string;
  onClose: () => void;
  onGranted?: (grant: GrantResult | BulkGrantResponse) => void;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const GRANT_TYPES = [
  { value: "time_limited", label: "Time-limited", description: "Access expires after N days" },
  { value: "permanent",    label: "Permanent",    description: "Access never expires" },
  { value: "restricted",   label: "Restricted",   description: "Read-only / interactive-only (30-day default)" },
] as const;

type GrantTypeValue = typeof GRANT_TYPES[number]["value"];

const EMAIL_RE = /^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{1,63}$/i;

function parseEmails(text: string): string[] {
  return [...new Set(
    text
      .split(/[,;\s]+/)
      .map(e => e.trim().toLowerCase())
      .filter(Boolean),
  )];
}

function useDebounce<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(id);
  }, [value, delayMs]);
  return debounced;
}

// ── Component ─────────────────────────────────────────────────────────────────

export function AdminGrantModal({ skillSlug, skillName, onClose, onGranted }: Props) {
  // Employee search (multi-select)
  const [query, setQuery]           = useState("");
  const [searching, setSearching]   = useState(false);
  const [results, setResults]       = useState<EmployeeResult[]>([]);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [selected, setSelected]     = useState<EmployeeResult[]>([]);

  // Grant config (shared across all selected users)
  const [grantType, setGrantType]       = useState<GrantTypeValue>("time_limited");
  const [expiresInDays, setExpiresInDays] = useState<string>("90");
  const [justification, setJustification] = useState("");

  // Submission
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [success, setSuccess]       = useState<BulkGrantResponse | null>(null);

  const debouncedQuery = useDebounce(query, 350);
  const inputRef = useRef<HTMLInputElement>(null);
  const selectedEmails = new Set(selected.map(e => e.email.toLowerCase()));

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const q = debouncedQuery.trim();
    if (q.length < 2) {
      setResults([]);
      setSearchError(null);
      return;
    }
    let cancelled = false;
    setSearching(true);
    setSearchError(null);
    apiFetch(`/api/employees/search?q=${encodeURIComponent(q)}&limit=8`)
      .then(r => r.ok ? r.json() : Promise.reject(new Error(`Search failed (${r.status})`)))
      .then(d => {
        if (cancelled) return;
        setResults(d.employees ?? []);
        setSearching(false);
      })
      .catch(err => {
        if (cancelled) return;
        setSearchError(err instanceof Error ? err.message : "Search unavailable.");
        setSearching(false);
      });
    return () => { cancelled = true; };
  }, [debouncedQuery]);

  const handleSelect = useCallback((emp: EmployeeResult) => {
    setSelected(prev => {
      if (prev.some(e => e.email.toLowerCase() === emp.email.toLowerCase())) return prev;
      return [...prev, emp];
    });
    setQuery("");
    setResults([]);
    setSubmitError(null);
    setTimeout(() => inputRef.current?.focus(), 50);
  }, []);

  const handleRemove = useCallback((email: string) => {
    setSelected(prev => prev.filter(e => e.email.toLowerCase() !== email.toLowerCase()));
    setSubmitError(null);
  }, []);

  /** Add emails from a pasted/typed comma-separated list (directory validated on submit). */
  const addEmailsFromText = useCallback((text: string): boolean => {
    const emails = parseEmails(text);
    if (emails.length === 0) return false;

    const invalid = emails.filter(e => !EMAIL_RE.test(e));
    if (invalid.length > 0) {
      setSubmitError(`Invalid email: ${invalid[0]}`);
      return true;
    }

    setSelected(prev => {
      const existing = new Set(prev.map(e => e.email.toLowerCase()));
      const additions: EmployeeResult[] = [];
      for (const email of emails) {
        if (existing.has(email)) continue;
        existing.add(email);
        additions.push({
          email,
          name: email,
          job_title: "",
          department: "",
          status: "",
        });
      }
      return additions.length ? [...prev, ...additions] : prev;
    });
    setQuery("");
    setResults([]);
    setSubmitError(null);
    setTimeout(() => inputRef.current?.focus(), 50);
    return true;
  }, []);

  const handleSearchKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      const text = query.trim();
      if (text.includes(",") || text.includes(";") || EMAIL_RE.test(text)) {
        e.preventDefault();
        addEmailsFromText(text);
      }
    }
  };

  const handleSearchPaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const pasted = e.clipboardData.getData("text");
    if (/[,;\s]/.test(pasted) && pasted.includes("@")) {
      e.preventDefault();
      addEmailsFromText(pasted);
    }
  };

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (selected.length === 0) return;

    const days = parseInt(expiresInDays, 10);
    if (grantType !== "permanent" && (!expiresInDays || isNaN(days) || days < 1 || days > 365)) {
      setSubmitError("Expiry must be between 1 and 365 days for time-limited and restricted grants.");
      return;
    }
    if (justification.trim().length < 10) {
      setSubmitError("Justification must be at least 10 characters.");
      return;
    }

    setSubmitting(true);
    setSubmitError(null);

    try {
      const payload: Record<string, unknown> = {
        emails: selected.map(e => e.email),
        skill_slug: skillSlug,
        grant_type: grantType,
        justification: justification.trim(),
        restrictions: {},
      };
      if (grantType !== "permanent") {
        payload.expires_in_days = days;
      }

      const res = await apiFetch("/api/admin/access/grant-direct", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = data?.detail;
        const msg = typeof detail === "string"
          ? detail
          : detail?.message || `Grant failed (${res.status})`;
        throw new Error(msg);
      }

      const bulk = data as BulkGrantResponse;
      setSuccess(bulk);
      onGranted?.(bulk.grants?.length === 1 && !bulk.errors?.length ? bulk.grants[0] : bulk);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Grant failed.");
    } finally {
      setSubmitting(false);
    }
  };

  const displayName = skillName || skillSlug.replace(/-/g, " ").replace(/\b\w/g, c => c.toUpperCase());

  // ── Success view ───────────────────────────────────────────────────────────
  if (success) {
    const expiry = success.expires_at
      ? new Date(success.expires_at).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" })
      : "Never";
    const grants = success.grants ?? [];
    const errors = success.errors ?? [];

    return (
      <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
        <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md border border-slate-200 max-h-[90vh] flex flex-col">
          <div className="px-6 pt-5 pb-4 border-b border-slate-100 flex items-start justify-between shrink-0">
            <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <ShieldCheck className="w-4 h-4 text-emerald-600" />
              {errors.length === 0 ? "Access Granted" : "Grant Results"}
            </h2>
            <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-lg leading-none">✕</button>
          </div>
          <div className="px-6 py-5 space-y-4 overflow-y-auto">
            {grants.length > 0 && (
              <div className="flex items-start gap-3 p-3 bg-emerald-50 border border-emerald-200 rounded-xl">
                <CheckCircle2 className="w-5 h-5 text-emerald-600 mt-0.5 shrink-0" />
                <div className="text-sm">
                  <p className="font-semibold text-emerald-800">
                    {grants.length === 1
                      ? (grants[0].user.name || grants[0].user.email)
                      : `${grants.length} users`}
                  </p>
                  <p className="text-emerald-700 mt-0.5">
                    {grants.length === 1 ? "now has" : "now have"} access to <strong>{displayName}</strong>
                  </p>
                </div>
              </div>
            )}

            {grants.length > 1 && (
              <ul className="space-y-1.5 max-h-40 overflow-y-auto">
                {grants.map(g => (
                  <li key={g.grant_id} className="text-xs flex justify-between gap-2 px-2 py-1.5 bg-slate-50 rounded-lg">
                    <span className="font-medium text-slate-800 truncate">{g.user.name || g.user.email}</span>
                    <span className="font-mono text-slate-500 shrink-0">{g.user.email}</span>
                  </li>
                ))}
              </ul>
            )}

            {grants.length === 1 && (
              <div className="space-y-1.5 text-xs text-slate-600">
                <div className="flex justify-between">
                  <span className="text-slate-400">Email</span>
                  <span className="font-mono">{grants[0].user.email}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Department</span>
                  <span>{grants[0].user.department || "—"}</span>
                </div>
              </div>
            )}

            {grants.length > 0 && (
              <div className="space-y-1.5 text-xs text-slate-600">
                <div className="flex justify-between">
                  <span className="text-slate-400">Grant type</span>
                  <span className="capitalize">{(success.grant_type || grants[0]?.grant_type)?.replace(/_/g, " ")}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Expires</span>
                  <span>{expiry}</span>
                </div>
                <div className="flex justify-between">
                  <span className="text-slate-400">Granted by</span>
                  <span className="font-mono">{success.granted_by || grants[0]?.granted_by}</span>
                </div>
              </div>
            )}

            {errors.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-xs font-semibold text-amber-800">
                  {errors.length} could not be granted
                </p>
                <ul className="space-y-1 max-h-32 overflow-y-auto">
                  {errors.map(err => (
                    <li key={err.email} className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-2.5 py-1.5">
                      <span className="font-mono font-medium">{err.email}</span>
                      <span className="text-amber-700"> — {err.error}</span>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <button
              onClick={onClose}
              className="w-full px-4 py-2 text-sm font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
            >
              Done
            </button>
          </div>
        </div>
      </div>
    );
  }

  // ── Grant form ─────────────────────────────────────────────────────────────
  const filteredResults = results.filter(emp => !selectedEmails.has(emp.email.toLowerCase()));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-lg border border-slate-200 max-h-[90vh] flex flex-col">
        <div className="px-6 pt-5 pb-4 border-b border-slate-100 flex items-start justify-between shrink-0">
          <div>
            <h2 className="text-base font-bold text-slate-900 flex items-center gap-2">
              <UserCheck className="w-4 h-4 text-[#FF444F]" />
              Grant Skill Access
            </h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Skill: <span className="font-semibold text-slate-700">{displayName}</span>
            </p>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 text-lg leading-none mt-0.5">✕</button>
        </div>

        <form onSubmit={handleSubmit} className="flex flex-col min-h-0 flex-1">
          <div className="px-6 py-5 space-y-5 overflow-y-auto">

            {/* Employee multi-select search */}
            <div>
              <label className="block text-xs font-bold text-slate-700 mb-1.5">
                Employees <span className="text-red-500">*</span>
                <span className="font-normal text-slate-400 ml-1.5">
                  Search directory, or paste a comma-separated email list
                </span>
              </label>

              {selected.length > 0 && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {selected.map(emp => (
                    <span
                      key={emp.email}
                      className="inline-flex items-center gap-1 pl-2 pr-1 py-1 bg-blue-50 border border-blue-200 rounded-lg text-xs max-w-full"
                    >
                      <span className="font-semibold text-blue-900 truncate">{emp.name || emp.email}</span>
                      <span className="text-blue-500 font-mono truncate hidden sm:inline">{emp.email}</span>
                      <button
                        type="button"
                        onClick={() => handleRemove(emp.email)}
                        disabled={submitting}
                        className="p-0.5 text-blue-400 hover:text-blue-700 rounded"
                        aria-label={`Remove ${emp.email}`}
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}

              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 pointer-events-none" />
                <input
                  ref={inputRef}
                  type="text"
                  value={query}
                  onChange={e => setQuery(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  onPaste={handleSearchPaste}
                  placeholder="Search name/email, or paste a@x.com, b@x.com…"
                  autoComplete="off"
                  className="w-full text-sm pl-8 pr-8 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-[#FF444F]/60 focus:ring-1 focus:ring-[#FF444F]/20"
                  disabled={submitting}
                />
                {query && (
                  <button
                    type="button"
                    onClick={() => { setQuery(""); setResults([]); }}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600"
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                )}
              </div>

              {filteredResults.length > 0 && (
                <div className="mt-1 border border-slate-200 rounded-lg overflow-hidden shadow-lg bg-white max-h-48 overflow-y-auto">
                  {filteredResults.map(emp => (
                    <button
                      key={emp.email}
                      type="button"
                      onClick={() => handleSelect(emp)}
                      className="w-full px-3 py-2.5 flex items-start gap-3 hover:bg-slate-50 text-left border-b border-slate-100 last:border-0 transition-colors"
                    >
                      <div className="w-7 h-7 rounded-full bg-[#FF444F]/10 flex items-center justify-center shrink-0 mt-0.5">
                        <span className="text-[10px] font-bold text-[#FF444F]">
                          {(emp.name || emp.email).charAt(0).toUpperCase()}
                        </span>
                      </div>
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-slate-800 truncate">{emp.name || emp.email}</div>
                        <div className="text-xs text-slate-400 truncate">{emp.email}</div>
                        {emp.department && (
                          <div className="text-[10px] text-slate-400 mt-0.5">{emp.department}{emp.job_title ? ` · ${emp.job_title}` : ""}</div>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              )}

              {searching && (
                <div className="mt-2 flex items-center gap-1.5 text-xs text-slate-400">
                  <Loader2 className="w-3 h-3 animate-spin" /> Searching org directory…
                </div>
              )}

              {!searching && debouncedQuery.trim().length >= 2 && filteredResults.length === 0 && !searchError && (
                <p className="mt-2 text-xs text-slate-400">
                  {results.length > 0 ? "All matching employees are already selected." : "No matching employees found."}
                </p>
              )}

              {searchError && (
                <p className="mt-2 text-xs text-red-600">{searchError}</p>
              )}

              <p className="mt-1.5 text-[10px] text-slate-400">
                {selected.length > 0
                  ? `${selected.length} selected — same grant type, duration, and note apply to all. Each email is verified against the directory on grant.`
                  : "Tip: paste several emails separated by commas, then press Enter."}
              </p>
            </div>

            {/* Grant type */}
            <div>
              <label className="block text-xs font-bold text-slate-700 mb-1.5">
                Grant Type <span className="text-red-500">*</span>
              </label>
              <div className="grid grid-cols-3 gap-2">
                {GRANT_TYPES.map(gt => (
                  <button
                    key={gt.value}
                    type="button"
                    onClick={() => setGrantType(gt.value)}
                    disabled={submitting}
                    className={`px-3 py-2.5 rounded-lg border text-left transition-colors ${
                      grantType === gt.value
                        ? "border-[#FF444F] bg-[#FF444F]/5 ring-1 ring-[#FF444F]/30"
                        : "border-slate-200 hover:border-slate-300 hover:bg-slate-50"
                    }`}
                  >
                    <div className="text-xs font-semibold text-slate-800">{gt.label}</div>
                    <div className="text-[10px] text-slate-500 mt-0.5 leading-tight">{gt.description}</div>
                  </button>
                ))}
              </div>
            </div>

            {grantType !== "permanent" && (
              <div>
                <label className="block text-xs font-bold text-slate-700 mb-1.5">
                  Duration (days) <span className="text-red-500">*</span>
                </label>
                <input
                  type="number"
                  min={1}
                  max={365}
                  value={expiresInDays}
                  onChange={e => setExpiresInDays(e.target.value)}
                  disabled={submitting}
                  placeholder="e.g. 90"
                  className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-[#FF444F]/60 focus:ring-1 focus:ring-[#FF444F]/20"
                />
                <p className="text-[10px] text-slate-400 mt-1">Must be between 1 and 365 days.</p>
              </div>
            )}

            <div>
              <label className="block text-xs font-bold text-slate-700 mb-1.5">
                Admin Note / Justification <span className="text-red-500">*</span>
              </label>
              <textarea
                value={justification}
                onChange={e => setJustification(e.target.value)}
                disabled={submitting}
                rows={3}
                placeholder="Why is this access being granted? (min 10 characters)"
                className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-[#FF444F]/60 focus:ring-1 focus:ring-[#FF444F]/20 resize-none"
              />
              <p className="text-[10px] text-slate-400 mt-1">{justification.trim().length}/2000 characters (minimum 10)</p>
            </div>

            {submitError && (
              <div className="flex items-start gap-2 text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                <AlertCircle className="w-3.5 h-3.5 mt-0.5 shrink-0" />
                <span>{submitError}</span>
              </div>
            )}
          </div>

          <div className="px-6 pb-5 flex gap-2 shrink-0 border-t border-slate-100 pt-4">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting}
              className="flex-1 px-4 py-2 text-sm text-slate-600 border border-slate-200 rounded-lg hover:bg-slate-50 font-medium transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting || selected.length === 0 || justification.trim().length < 10}
              className="flex-1 px-4 py-2 text-sm font-bold bg-[#FF444F] text-white rounded-lg hover:bg-[#e03c47] disabled:opacity-40 flex items-center justify-center gap-1.5 transition-colors"
            >
              {submitting ? (
                <><Loader2 className="w-3.5 h-3.5 animate-spin" /> Granting…</>
              ) : (
                <><ShieldCheck className="w-3.5 h-3.5" /> Grant Access{selected.length > 1 ? ` (${selected.length})` : ""}</>
              )}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
