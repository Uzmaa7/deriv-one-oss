import {
  useState,
  useEffect,
  useRef,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from "react";
import { useLocation } from "react-router-dom";
import {
  Upload,
  X,
  CheckCircle2,
  XCircle,
  Clock,
  AlertTriangle,
  Loader2,
  FileText,
  RefreshCw,
  KeyRound,
  Eye,
  EyeOff,
  Plus,
  ShieldCheck,
  RotateCcw,
  ChevronDown,
  ChevronUp,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { parseUTC, timeAgo } from "@/lib/time";
import {
  RISK_LEVEL_OPTIONS,
  CROSS_DEPT_POLICY_OPTIONS,
  DEFAULT_GRANT_OPTIONS,
  DepartmentPicker,
  IntegrationTags,
  type RiskLevel,
  type CrossDeptPolicy,
  type CrossDeptDefaultGrant,
} from "@/components/SkillFormFields";
import { SystemLinksConfirmModal } from "@/components/SystemLinksConfirmModal";

// ── Types ─────────────────────────────────────────────────────────────────────
type ReviewStatus = "pending" | "reviewing" | "awaiting_confirmation" | "pass" | "fail" | "modify";
type PublishStep = "upload" | "form";

interface Submission {
  id: string;
  proposed_slug: string;
  description: string;
  risk_level: RiskLevel;
  departments: string[];
  review_status: ReviewStatus;
  review_reason: string | null;
  reviewed_at: string | null;
  submitted_at: string;
  proposed_system_links?: unknown[] | null;
}

interface SecretEntry {
  key: string;
  value: string;
}

// ── Constants (see @/components/SkillFormFields for shared constants) ─────────

// ── Client-side extraction helpers ────────────────────────────────────────────

/**
 * Parse YAML front matter from a Markdown string.
 * Handles simple key: value and key: [list] (- item) blocks.
 */
function parseFrontmatter(md: string): Record<string, string | string[]> {
  const match = md.match(/^---\r?\n([\s\S]*?)\r?\n---/);
  if (!match) return {};
  const lines = match[1].split("\n");
  const result: Record<string, string | string[]> = {};
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const colonIdx = line.indexOf(":");
    if (colonIdx === -1) { i++; continue; }
    const key = line.slice(0, colonIdx).trim();
    const rest = line.slice(colonIdx + 1).trim();
    if (rest === "") {
      // Collect list items on following lines
      const items: string[] = [];
      i++;
      while (i < lines.length && /^\s*-\s/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*-\s*/, "").replace(/^["']|["']$/g, "").trim());
        i++;
      }
      if (items.length > 0) result[key] = items;
    } else {
      result[key] = rest.replace(/^["']|["']$/g, "");
      i++;
    }
  }
  return result;
}

/**
 * Extract UPPER_SNAKE_CASE env var names from patterns:
 *   os.environ.get("KEY")  os.environ["KEY"]  os.getenv("KEY")
 */
function extractEnvVarKeys(md: string): string[] {
  const RE =
    /os\.(?:environ(?:\.get)?\s*[\[(]|getenv\s*\()\s*["']([A-Z][A-Z0-9_]{0,99})["']/g;
  const keys = new Set<string>();
  let m: RegExpExecArray | null;
  while ((m = RE.exec(md)) !== null) {
    keys.add(m[1]);
  }
  return Array.from(keys);
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function slugify(name: string): string {
  return name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9\s-]/g, "")
    .replace(/\s+/g, "-")
    .replace(/-+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 64);
}

function relativeTime(iso: string): string {
  const diff = Date.now() - parseUTC(iso).getTime();
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.floor(hours / 24)}d ago`;
}

// ── Status badge ──────────────────────────────────────────────────────────────

function StatusBadge({ status }: { status: ReviewStatus }) {
  const map: Record<ReviewStatus, { icon: React.ReactNode; label: string; cls: string }> = {
    pending: {
      icon: <Clock className="w-3.5 h-3.5" />,
      label: "Pending",
      cls: "bg-slate-100 text-slate-600 border-slate-200",
    },
    reviewing: {
      icon: <Loader2 className="w-3.5 h-3.5 animate-spin" />,
      label: "Reviewing",
      cls: "bg-blue-50 text-blue-600 border-blue-200",
    },
    pass: {
      icon: <CheckCircle2 className="w-3.5 h-3.5" />,
      label: "Approved",
      cls: "bg-emerald-50 text-emerald-700 border-emerald-200",
    },
    fail: {
      icon: <XCircle className="w-3.5 h-3.5" />,
      label: "Rejected",
      cls: "bg-red-50 text-red-700 border-red-200",
    },
    modify: {
      icon: <AlertTriangle className="w-3.5 h-3.5" />,
      label: "Needs Changes",
      cls: "bg-amber-50 text-amber-700 border-amber-200",
    },
    awaiting_confirmation: {
      icon: <AlertTriangle className="w-3.5 h-3.5" />,
      label: "Action Required",
      cls: "bg-blue-50 text-blue-700 border-blue-200",
    },
  };
  const { icon, label, cls } = map[status] ?? map.pending;
  return (
    <span
      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold border ${cls}`}
    >
      {icon}
      {label}
    </span>
  );
}

// ── Security review banner ─────────────────────────────────────────────────────

function SecurityReviewBanner({
  status,
  reason,
}: {
  status: ReviewStatus | null;
  reason: string | null;
}) {
  if (!status) return null;

  if (status === "pending" || status === "reviewing") {
    return (
      <div className="flex items-center gap-3 bg-blue-50 border border-blue-200 rounded-xl px-4 py-3">
        <Loader2 className="w-4 h-4 text-blue-500 animate-spin flex-shrink-0" />
        <div>
          <p className="text-sm font-semibold text-blue-800">AI security review in progress…</p>
          <p className="text-xs text-blue-600 mt-0.5">
            We're scanning your file for hardcoded secrets and unsafe instructions.
          </p>
        </div>
      </div>
    );
  }

  if (status === "pass") {
    return (
      <div className="flex items-center gap-3 bg-emerald-50 border border-emerald-200 rounded-xl px-4 py-3">
        <ShieldCheck className="w-4 h-4 text-emerald-600 flex-shrink-0" />
        <p className="text-sm font-semibold text-emerald-800">
          Security review passed — safe to submit.
        </p>
      </div>
    );
  }

  if (status === "fail") {
    return (
      <div className="flex items-start gap-3 bg-red-50 border border-red-200 rounded-xl px-4 py-3">
        <XCircle className="w-4 h-4 text-red-600 mt-0.5 flex-shrink-0" />
        <div>
          <p className="text-sm font-semibold text-red-800">Security review failed — fix the issues before submitting.</p>
          {reason && (
            <p className="text-xs text-red-700 mt-1 whitespace-pre-wrap">{reason}</p>
          )}
        </div>
      </div>
    );
  }

  // modify
  return (
    <div className="flex items-start gap-3 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
      <AlertTriangle className="w-4 h-4 text-amber-600 mt-0.5 flex-shrink-0" />
      <div>
        <p className="text-sm font-semibold text-amber-800">
          Review flagged a concern — you may still submit for manual review.
        </p>
        {reason && (
          <p className="text-xs text-amber-700 mt-1 whitespace-pre-wrap">{reason}</p>
        )}
      </div>
    </div>
  );
}

// DepartmentPicker and IntegrationTags are imported from @/components/SkillFormFields

// ── Upload drop zone (Step 1) ─────────────────────────────────────────────────

function UploadDropzone({ onFile }: { onFile: (f: File) => void }) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = (f: File) => {
    if (!f.name.toLowerCase().endsWith(".md")) {
      alert("Only Markdown (.md) files are accepted.");
      return;
    }
    if (f.size > 200 * 1024) {
      alert("File must be smaller than 200 KB.");
      return;
    }
    onFile(f);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) accept(dropped);
  };

  const onInput = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) accept(selected);
    e.target.value = "";
  };

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      className={`border-2 border-dashed rounded-2xl py-20 px-8 flex flex-col items-center justify-center text-center cursor-pointer transition-colors ${
        dragging
          ? "border-slate-400 bg-slate-100"
          : "border-slate-200 bg-white hover:bg-slate-50 hover:border-slate-300"
      }`}
    >
      <div className="w-16 h-16 rounded-2xl bg-slate-100 flex items-center justify-center mb-5">
        <Upload className="w-7 h-7 text-slate-500" />
      </div>
      <p className="text-lg font-bold text-slate-800 mb-1">
        Drop your SKILL.md here
      </p>
      <p className="text-sm text-slate-500 mb-4">or click to browse</p>
      <span className="inline-flex items-center gap-1.5 text-xs text-slate-400 bg-slate-100 px-3 py-1.5 rounded-full">
        <FileText className="w-3.5 h-3.5" />
        Markdown (.md) · up to 200 KB
      </span>
      <p className="text-xs text-slate-400 mt-4 max-w-sm">
        Form fields will be auto-filled from your file's front matter and an AI security
        review will start immediately.
      </p>
      <input
        ref={inputRef}
        type="file"
        accept=".md,text/markdown"
        onChange={onInput}
        className="hidden"
      />
    </div>
  );
}

// ── Skill secrets editor ──────────────────────────────────────────────────────

const SECRET_KEY_RE = /^[A-Z][A-Z0-9_]{0,98}$/;
const MAX_SECRETS = 20;

/**
 * Secrets editor that supports:
 * - Pre-populated key-only entries (value === "") from env var extraction
 * - Fully committed entries shown with masked value
 * - Inline "Add a secret" form
 */
function SkillSecretsEditor({
  secrets,
  onChange,
}: {
  secrets: SecretEntry[];
  onChange: (s: SecretEntry[]) => void;
}) {
  const [addingKey, setAddingKey] = useState("");
  const [addingValue, setAddingValue] = useState("");
  const [showAddValue, setShowAddValue] = useState(false);
  const [keyError, setKeyError] = useState<string | null>(null);
  const [isAdding, setIsAdding] = useState(false);
  const [visibleValues, setVisibleValues] = useState<Set<string>>(new Set());
  // Track which pre-populated (empty-value) entries are being edited
  const [editingValues, setEditingValues] = useState<Record<string, string>>({});
  const [editingKeys, setEditingKeys] = useState<Record<string, string>>({});

  const toggleValueVisible = (key: string) => {
    setVisibleValues((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  const commitAdd = () => {
    const k = addingKey.trim().toUpperCase().replace(/\s+/g, "_");
    if (!SECRET_KEY_RE.test(k)) {
      setKeyError(
        "Key must start with an uppercase letter and contain only uppercase letters, digits, and underscores."
      );
      return;
    }
    if (secrets.some((s) => s.key === k)) {
      setKeyError(`Key "${k}" already exists.`);
      return;
    }
    if (!addingValue.trim()) {
      setKeyError("Value cannot be empty.");
      return;
    }
    onChange([...secrets, { key: k, value: addingValue }]);
    setAddingKey("");
    setAddingValue("");
    setKeyError(null);
    setIsAdding(false);
    setShowAddValue(false);
  };

  const commitEditValue = (key: string) => {
    const newVal = (editingValues[key] ?? "").trim();
    const newKey = (editingKeys[key] ?? key).trim().toUpperCase().replace(/\s+/g, "_");
    if (!SECRET_KEY_RE.test(newKey)) return;
    if (!newVal) return;
    const other = secrets.filter((s) => s.key !== key);
    if (other.some((s) => s.key === newKey)) return;
    onChange(other.map((s) => s).concat({ key: newKey, value: newVal }));
    setEditingValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
    setEditingKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });
  };

  const remove = (key: string) => {
    onChange(secrets.filter((s) => s.key !== key));
    setEditingValues((prev) => { const n = { ...prev }; delete n[key]; return n; });
    setEditingKeys((prev) => { const n = { ...prev }; delete n[key]; return n; });
  };

  const pendingEntries = secrets.filter((s) => s.value === "");
  const filledEntries = secrets.filter((s) => s.value !== "");

  return (
    <div className="space-y-3">
      {/* Pending (auto-extracted, value not yet filled) */}
      {pendingEntries.length > 0 && (
        <div className="space-y-2">
          {pendingEntries.map((s) => {
            const editKey = editingKeys[s.key] ?? s.key;
            const editVal = editingValues[s.key] ?? "";
            return (
              <div
                key={s.key}
                className="border border-amber-200 bg-amber-50 rounded-lg px-3 py-2.5 space-y-2"
              >
                <div className="flex items-center justify-between gap-2">
                  <div className="flex items-center gap-1.5">
                    <KeyRound className="w-3.5 h-3.5 text-amber-500 flex-shrink-0" />
                    <span className="text-xs font-semibold text-amber-700">
                      Detected from file
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => remove(s.key)}
                    className="text-slate-400 hover:text-red-500 transition-colors"
                    aria-label={`Remove ${s.key}`}
                  >
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="grid sm:grid-cols-2 gap-2">
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-500 mb-1">
                      Variable Name
                    </label>
                    <input
                      type="text"
                      value={editKey}
                      onChange={(e) =>
                        setEditingKeys((prev) => ({
                          ...prev,
                          [s.key]: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""),
                        }))
                      }
                      className="w-full bg-white border border-slate-200 rounded-md px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors"
                    />
                  </div>
                  <div>
                    <label className="block text-[11px] font-semibold text-slate-500 mb-1">
                      Secret Value <span className="text-red-500">*</span>
                    </label>
                    <div className="flex gap-1.5">
                      <input
                        type="password"
                        value={editVal}
                        onChange={(e) =>
                          setEditingValues((prev) => ({ ...prev, [s.key]: e.target.value }))
                        }
                        onKeyDown={(e) => {
                          if (e.key === "Enter") { e.preventDefault(); commitEditValue(s.key); }
                        }}
                        placeholder="Paste secret value…"
                        className="flex-1 min-w-0 bg-white border border-slate-200 rounded-md px-2.5 py-1.5 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors placeholder:text-slate-400"
                      />
                      <button
                        type="button"
                        onClick={() => commitEditValue(s.key)}
                        disabled={!editVal.trim()}
                        className="px-2.5 py-1.5 bg-slate-900 text-white text-xs font-bold rounded-md hover:bg-slate-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors flex-shrink-0"
                      >
                        Save
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Filled / committed entries */}
      {filledEntries.length > 0 && (
        <div className="space-y-2">
          {filledEntries.map((s) => (
            <div
              key={s.key}
              className="flex items-center gap-2 bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2"
            >
              <KeyRound className="w-3.5 h-3.5 text-slate-400 flex-shrink-0" />
              <code className="text-xs font-mono font-bold text-slate-800 flex-shrink-0">
                {s.key}
              </code>
              <span className="text-slate-300 text-xs mx-1">=</span>
              <span className="flex-1 text-xs font-mono text-slate-500 truncate">
                {visibleValues.has(s.key)
                  ? s.value
                  : "•".repeat(Math.min(s.value.length, 20))}
              </span>
              <button
                type="button"
                onClick={() => toggleValueVisible(s.key)}
                className="text-slate-400 hover:text-slate-700 transition-colors p-0.5"
                title={visibleValues.has(s.key) ? "Hide value" : "Show value"}
              >
                {visibleValues.has(s.key) ? (
                  <EyeOff className="w-3.5 h-3.5" />
                ) : (
                  <Eye className="w-3.5 h-3.5" />
                )}
              </button>
              <button
                type="button"
                onClick={() => remove(s.key)}
                className="text-slate-400 hover:text-red-500 transition-colors p-0.5"
                aria-label={`Remove ${s.key}`}
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add new secret */}
      {isAdding ? (
        <div className="border border-slate-200 rounded-lg p-4 bg-[#F9FAFB] space-y-3">
          <div className="grid sm:grid-cols-2 gap-3">
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">
                Variable Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                value={addingKey}
                onChange={(e) => {
                  setAddingKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""));
                  setKeyError(null);
                }}
                placeholder="e.g. IAM_API_KEY"
                className="w-full bg-white border border-slate-200 rounded-md px-3 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors placeholder:text-slate-400"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-xs font-semibold text-slate-600 mb-1">
                Secret Value <span className="text-red-500">*</span>
              </label>
              <div className="relative">
                <input
                  type={showAddValue ? "text" : "password"}
                  value={addingValue}
                  onChange={(e) => { setAddingValue(e.target.value); setKeyError(null); }}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") { e.preventDefault(); commitAdd(); }
                  }}
                  placeholder="Paste secret value…"
                  className="w-full bg-white border border-slate-200 rounded-md pl-3 pr-9 py-2 text-xs font-mono focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors placeholder:text-slate-400"
                />
                <button
                  type="button"
                  onClick={() => setShowAddValue((v) => !v)}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
                  tabIndex={-1}
                >
                  {showAddValue ? (
                    <EyeOff className="w-3.5 h-3.5" />
                  ) : (
                    <Eye className="w-3.5 h-3.5" />
                  )}
                </button>
              </div>
            </div>
          </div>
          {keyError && (
            <p className="text-xs text-red-600 flex items-center gap-1">
              <XCircle className="w-3.5 h-3.5 flex-shrink-0" /> {keyError}
            </p>
          )}
          <div className="flex gap-2 justify-end">
            <button
              type="button"
              onClick={() => {
                setIsAdding(false);
                setAddingKey("");
                setAddingValue("");
                setKeyError(null);
                setShowAddValue(false);
              }}
              className="px-3 py-1.5 text-xs font-semibold text-slate-600 hover:text-slate-900 transition-colors"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={commitAdd}
              className="px-3 py-1.5 bg-slate-900 text-white text-xs font-bold rounded-md hover:bg-slate-700 transition-colors"
            >
              Add Secret
            </button>
          </div>
        </div>
      ) : secrets.length < MAX_SECRETS ? (
        <button
          type="button"
          onClick={() => setIsAdding(true)}
          className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-900 transition-colors"
        >
          <Plus className="w-3.5 h-3.5" />
          Add a secret
        </button>
      ) : (
        <p className="text-xs text-slate-400">Maximum of {MAX_SECRETS} secrets reached.</p>
      )}
    </div>
  );
}

// ── Submission row ────────────────────────────────────────────────────────────

function SubmissionRow({ sub, onConfirmed }: { sub: Submission; onConfirmed: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [showConfirmModal, setShowConfirmModal] = useState(false);
  const hasReason = !!sub.review_reason;
  const awaitingConfirmation = sub.review_status === "awaiting_confirmation";

  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <div
        className="flex items-center gap-3 px-4 py-3 bg-white cursor-pointer hover:bg-slate-50 transition-colors"
        onClick={() => hasReason && setExpanded((e) => !e)}
      >
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <code className="text-sm font-bold text-slate-800">{sub.proposed_slug}</code>
            <StatusBadge status={sub.review_status} />
          </div>
          <p className="text-xs text-slate-500 mt-0.5 truncate">{sub.description}</p>
        </div>
        {awaitingConfirmation && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setShowConfirmModal(true);
            }}
            className="text-xs font-semibold text-blue-700 hover:text-blue-900 flex-shrink-0 whitespace-nowrap"
          >
            Review system access →
          </button>
        )}
        <span className="text-xs text-slate-400 flex-shrink-0">
          {relativeTime(sub.submitted_at)}
        </span>
        {hasReason && (
          <button type="button" className="text-slate-400 flex-shrink-0">
            {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
          </button>
        )}
      </div>

      {expanded && hasReason && (
        <div className="px-4 py-3 bg-slate-50 border-t border-slate-200 text-sm text-slate-700">
          <p className="font-semibold text-slate-600 text-xs uppercase tracking-wide mb-1">
            Reviewer Feedback
          </p>
          <p className="whitespace-pre-wrap">{sub.review_reason}</p>
        </div>
      )}

      {showConfirmModal && (
        <SystemLinksConfirmModal
          submissionId={sub.id}
          slug={sub.proposed_slug}
          onClose={() => setShowConfirmModal(false)}
          onDone={() => {
            setShowConfirmModal(false);
            onConfirmed();
          }}
        />
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function PublishPage() {
  const location = useLocation();

  // ── Step / preflight state ─────────────────────────────────────────────────
  const [step, setStep] = useState<PublishStep>("upload");
  const [skillFile, setSkillFile] = useState<File | null>(null);
  const [preflightId, setPreflightId] = useState<string | null>(null);
  const [preflightStatus, setPreflightStatus] = useState<ReviewStatus | null>(null);
  const [preflightReason, setPreflightReason] = useState<string | null>(null);
  const [preflightLoading, setPreflightLoading] = useState(false);
  const [preflightError, setPreflightError] = useState<string | null>(null);

  // ── Form state ─────────────────────────────────────────────────────────────
  const [skillName, setSkillName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugManual, setSlugManual] = useState(false);
  const [description, setDescription] = useState("");
  const [departments, setDepartments] = useState<string[]>([]);
  const [integrations, setIntegrations] = useState<string[]>([]);
  const [riskLevel, setRiskLevel] = useState<RiskLevel>("standard");
  const [crossDeptPolicy, setCrossDeptPolicy] = useState<CrossDeptPolicy>("requestable");
  const [crossDeptGrant, setCrossDeptGrant] = useState<CrossDeptDefaultGrant>("time_limited_30d");
  const [crossDeptApprovers, setCrossDeptApprovers] = useState<string[]>([]);
  const [secrets, setSecrets] = useState<SecretEntry[]>([]);

  // ── Submission state ───────────────────────────────────────────────────────
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [submitSuccess, setSubmitSuccess] = useState(false);

  // ── My submissions ─────────────────────────────────────────────────────────
  const [submissions, setSubmissions] = useState<Submission[]>([]);
  const [loadingSubmissions, setLoadingSubmissions] = useState(true);

  // Auto-derive slug from skill name unless user has edited it manually
  useEffect(() => {
    if (!slugManual) {
      setSlug(slugify(skillName));
    }
  }, [skillName, slugManual]);

  // Poll preflight status until terminal state is reached, then merge AI-extracted keys
  useEffect(() => {
    if (!preflightId) return;
    const TERMINAL: ReviewStatus[] = ["pass", "fail", "modify"];
    if (preflightStatus && TERMINAL.includes(preflightStatus)) return;

    const timer = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/skills/preflight/${preflightId}`);
        if (!res.ok) return;
        const data = await res.json();
        setPreflightStatus(data.review_status as ReviewStatus);
        setPreflightReason(data.review_reason ?? null);

        if (TERMINAL.includes(data.review_status)) {
          clearInterval(timer);

          // Merge AI-extracted secret keys (keys that aren't already in the list)
          const aiKeys: string[] = Array.isArray(data.extracted_secret_keys)
            ? data.extracted_secret_keys
            : [];
          if (aiKeys.length > 0) {
            setSecrets((prev) => {
              const existingKeys = new Set(prev.map((s) => s.key));
              const newEntries = aiKeys
                .filter((k) => !existingKeys.has(k))
                .map((k) => ({ key: k, value: "" }));
              return newEntries.length > 0 ? [...prev, ...newEntries] : prev;
            });
          }
        }
      } catch {
        // silently retry next tick
      }
    }, 2000);

    return () => clearInterval(timer);
  }, [preflightId, preflightStatus]);

  const fetchSubmissions = async (opts: { silent?: boolean } = {}) => {
    if (!opts.silent) setLoadingSubmissions(true);
    try {
      const res = await apiFetch("/api/skills/my-submissions");
      if (res.ok) {
        const data = await res.json();
        setSubmissions(data);
      }
    } catch {
      // silently ignore
    } finally {
      if (!opts.silent) setLoadingSubmissions(false);
    }
  };

  useEffect(() => {
    fetchSubmissions();
  }, []);

  // Poll the submissions list while anything is still under AI review, so
  // "awaiting_confirmation" rows (and the security verdict itself) surface
  // without the user needing to click Refresh.
  useEffect(() => {
    const IN_FLIGHT: ReviewStatus[] = ["pending", "reviewing"];
    if (!submissions.some((s) => IN_FLIGHT.includes(s.review_status))) return;

    const timer = setInterval(() => fetchSubmissions({ silent: true }), 4000);
    return () => clearInterval(timer);
  }, [submissions]);

  // ── Pre-load skill from Create Skill page (router state) ───────────────────
  // When the user clicks "Publish Skill" on CreateSkillPage, we receive
  // { preloadedSkillContent, preloadedSkillName } via react-router state.
  useEffect(() => {
    const state = location.state as {
      preloadedSkillContent?: string;
      preloadedSkillName?: string;
    } | null;
    if (!state?.preloadedSkillContent) return;

    const content = state.preloadedSkillContent;
    const name = state.preloadedSkillName || "generated-skill";
    const fileName = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "") + ".md";

    // Synthesise a File object from the string content
    const blob = new Blob([content], { type: "text/markdown" });
    const file = new File([blob], fileName, { type: "text/markdown" });

    // Trigger the same handleFileSelected flow as a manual upload
    handleFileSelected(file);

    // Clear the state so a page refresh doesn't re-trigger
    window.history.replaceState({}, "", location.pathname);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── File upload handler ────────────────────────────────────────────────────

  const handleFileSelected = async (file: File) => {
    setSkillFile(file);
    setPreflightError(null);

    const text = await file.text();

    // Client-side parse for auto-fill
    const fm = parseFrontmatter(text);
    const fmName = typeof fm["name"] === "string" ? fm["name"] : "";
    const fmDesc = typeof fm["description"] === "string" ? fm["description"] : "";
    const fmTags = Array.isArray(fm["tags"]) ? fm["tags"] : [];

    if (fmName) { setSkillName(fmName); setSlugManual(false); }
    if (fmDesc) setDescription(fmDesc);
    if (fmTags.length > 0) setIntegrations(fmTags);

    // Pre-populate secrets from detected env var keys (value empty → user must fill)
    const detectedKeys = extractEnvVarKeys(text);
    if (detectedKeys.length > 0) {
      setSecrets(detectedKeys.map((k) => ({ key: k, value: "" })));
    }

    // Move to form step immediately, then kick off preflight in background
    setStep("form");
    setPreflightStatus("pending");
    setPreflightLoading(true);

    try {
      const form = new FormData();
      form.append("skill_file", file, file.name);
      const res = await apiFetch("/api/skills/preflight", { method: "POST", body: form });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        setPreflightError(data?.detail ?? "Preflight request failed.");
        setPreflightStatus(null);
        return;
      }
      const data = await res.json();
      setPreflightId(data.preflight_id);
      setPreflightStatus(data.review_status as ReviewStatus);
    } catch {
      setPreflightError("Could not connect to the security review service.");
      setPreflightStatus(null);
    } finally {
      setPreflightLoading(false);
    }
  };

  // ── Replace file ───────────────────────────────────────────────────────────

  const handleReplaceFile = () => {
    setStep("upload");
    setSkillFile(null);
    setPreflightId(null);
    setPreflightStatus(null);
    setPreflightReason(null);
    setPreflightError(null);
    // Clear auto-filled fields
    setSkillName("");
    setSlug("");
    setSlugManual(false);
    setDescription("");
    setIntegrations([]);
    setSecrets([]);
  };

  // ── Form submit ────────────────────────────────────────────────────────────

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitError(null);

    if (!skillFile) {
      setSubmitError("Please upload your SKILL.md file.");
      return;
    }
    if (departments.length === 0) {
      setSubmitError("Please select at least one department.");
      return;
    }

    // Ensure no pending secrets with empty values remain
    const incompletePending = secrets.filter((s) => s.value === "");
    if (incompletePending.length > 0) {
      setSubmitError(
        `Please fill in the value for: ${incompletePending.map((s) => s.key).join(", ")}`
      );
      return;
    }

    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("proposed_slug", slug);
      form.append("departments", JSON.stringify(departments));
      form.append("integrations", JSON.stringify(integrations));
      form.append("risk_level", riskLevel);
      form.append("cross_dept_policy", crossDeptPolicy);
      form.append("cross_dept_default_grant", crossDeptGrant);
      form.append("cross_dept_approvers", JSON.stringify(crossDeptApprovers));
      form.append("name", skillName.trim());
      form.append("description", description);
      form.append("skill_file", skillFile, skillFile.name);
      form.append("secrets", JSON.stringify(secrets.filter((s) => s.value !== "")));
      if (preflightId) form.append("preflight_id", preflightId);

      const res = await apiFetch("/api/skills/submit", { method: "POST", body: form });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Submission failed (${res.status})`);
      }

      setSubmitSuccess(true);
      // Reset to upload step
      handleReplaceFile();
      setDepartments([]);
      setRiskLevel("standard");
      setCrossDeptPolicy("requestable");
      setCrossDeptGrant("time_limited_30d");
      setCrossDeptApprovers([]);
      await fetchSubmissions();
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setSubmitting(false);
    }
  };

  const submitDisabled =
    submitting ||
    preflightStatus === "reviewing" ||
    preflightStatus === "pending" ||
    preflightStatus === "fail";

  // ── Render ─────────────────────────────────────────────────────────────────

  return (
    <div className="flex-1 bg-[#F9FAFB] py-12">
      <div className="max-w-3xl mx-auto px-4 sm:px-6 lg:px-8 space-y-10">

        {/* Header */}
        <div>
          <h1 className="text-3xl font-bold text-slate-900 mb-1">Publish a Skill</h1>
          <p className="text-slate-500 text-sm">
            {step === "upload"
              ? "Start by uploading your SKILL.md — fields will auto-fill and a security review runs immediately."
              : "Review the auto-filled details, fill in any missing fields, and submit."}
          </p>
        </div>

        {/* Success banner */}
        {submitSuccess && (
          <div className="flex items-start gap-3 bg-emerald-50 border border-emerald-200 rounded-xl p-4">
            <CheckCircle2 className="w-5 h-5 text-emerald-600 mt-0.5 flex-shrink-0" />
            <div>
              <p className="text-sm font-bold text-emerald-800">Skill submitted successfully!</p>
              <p className="text-xs text-emerald-700 mt-0.5">
                {preflightStatus === "pass"
                  ? "Your skill passed security review — confirm its system access below to finish publishing."
                  : "The AI security reviewer is now evaluating your submission. Check the status below."}
              </p>
            </div>
            <button
              type="button"
              onClick={() => setSubmitSuccess(false)}
              className="ml-auto text-emerald-400 hover:text-emerald-600"
            >
              <X className="w-4 h-4" />
            </button>
          </div>
        )}

        {/* ── Step 1: Upload ──────────────────────────────────────────────── */}
        {step === "upload" && (
          <UploadDropzone onFile={handleFileSelected} />
        )}

        {/* ── Step 2: Form ────────────────────────────────────────────────── */}
        {step === "form" && (
          <form
            onSubmit={handleSubmit}
            className="bg-white border border-slate-200 rounded-xl shadow-sm divide-y divide-slate-100"
          >
            {/* File info + replace */}
            <div className="px-6 py-4 flex items-center gap-3">
              <FileText className="w-5 h-5 text-slate-400 flex-shrink-0" />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-semibold text-slate-800 truncate">
                  {skillFile?.name ?? "skill.md"}
                </p>
                {skillFile && (
                  <p className="text-xs text-slate-400">
                    {(skillFile.size / 1024).toFixed(1)} KB
                  </p>
                )}
              </div>
              <button
                type="button"
                onClick={handleReplaceFile}
                className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-900 transition-colors flex-shrink-0"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Replace file
              </button>
            </div>

            {/* Security review banner */}
            {(preflightStatus || preflightError) && (
              <div className="px-6 py-4">
                {preflightError ? (
                  <div className="flex items-center gap-2 text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-xl px-4 py-3">
                    <AlertTriangle className="w-4 h-4 flex-shrink-0" />
                    <span>Security pre-check unavailable: {preflightError}</span>
                  </div>
                ) : (
                  <SecurityReviewBanner
                    status={preflightLoading ? "reviewing" : preflightStatus}
                    reason={preflightReason}
                  />
                )}
              </div>
            )}

            {/* ── Skill Identity ─────────────────────────────────────── */}
            <section className="p-6 space-y-4">
              <h2 className="text-base font-bold text-slate-900">Skill Identity</h2>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">
                  Skill Name
                </label>
                <input
                  type="text"
                  required
                  maxLength={80}
                  value={skillName}
                  onChange={(e) => setSkillName(e.target.value)}
                  placeholder="e.g. Finance Data Analyst"
                  className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">
                  Skill Slug{" "}
                  <span className="font-normal text-slate-400">(auto-generated, editable)</span>
                </label>
                <input
                  type="text"
                  required
                  pattern="[a-z0-9][a-z0-9\-]{0,62}[a-z0-9]|[a-z0-9]"
                  title="Lowercase letters, digits, and hyphens only (max 64 chars)"
                  value={slug}
                  onChange={(e) => {
                    setSlugManual(true);
                    setSlug(e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, ""));
                  }}
                  placeholder="finance-bq-analyst"
                  className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
                />
                <p className="text-xs text-slate-400 mt-1">
                  Lowercase letters, digits, and hyphens only.
                </p>
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">
                  Short Description
                </label>
                <textarea
                  required
                  maxLength={2000}
                  rows={2}
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="One-line summary of what this skill does"
                  className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400 resize-none"
                />
              </div>
            </section>

            {/* ── Departments ─────────────────────────────────────────── */}
            <section className="p-6 space-y-2">
              <h2 className="text-base font-bold text-slate-900">Departments</h2>
              <p className="text-xs text-slate-500">
                Which department(s) get automatic Tier 1 access? Select "Everyone" for a
                universal skill.
              </p>
              <DepartmentPicker selected={departments} onChange={setDepartments} />
              {departments.length > 0 && !departments.includes("*") && (
                <div className="flex flex-wrap gap-1.5 pt-1">
                  {departments.map((d) => (
                    <span
                      key={d}
                      className="inline-flex items-center gap-1 bg-slate-100 text-slate-700 text-xs font-medium px-2 py-1 rounded-full"
                    >
                      {d}
                      <button
                        type="button"
                        onClick={() => setDepartments(departments.filter((x) => x !== d))}
                        className="text-slate-400 hover:text-slate-700"
                      >
                        <X className="w-3 h-3" />
                      </button>
                    </span>
                  ))}
                </div>
              )}
            </section>

            {/* ── Integrations ─────────────────────────────────────────── */}
            <section className="p-6 space-y-2">
              <h2 className="text-base font-bold text-slate-900">Integrations</h2>
              <p className="text-xs text-slate-500">
                List external systems or APIs this skill connects to (e.g. "Data Warehouse (read)").
                Leave empty if none.
              </p>
              <IntegrationTags tags={integrations} onChange={setIntegrations} />
            </section>

            {/* ── Access Control ──────────────────────────────────────── */}
            <section className="p-6 space-y-4">
              <h2 className="text-base font-bold text-slate-900">Access Control</h2>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Risk Level
                </label>
                <div className="grid sm:grid-cols-3 gap-2">
                  {RISK_LEVEL_OPTIONS.map((opt) => (
                    <label
                      key={opt.value}
                      className={`flex flex-col gap-0.5 border rounded-lg p-3 cursor-pointer transition-colors ${
                        riskLevel === opt.value
                          ? "border-slate-900 bg-slate-50"
                          : "border-slate-200 hover:border-slate-300"
                      }`}
                    >
                      <div className="flex items-center gap-2">
                        <input
                          type="radio"
                          name="risk_level"
                          value={opt.value}
                          checked={riskLevel === opt.value}
                          onChange={() => setRiskLevel(opt.value)}
                          className="accent-slate-900"
                        />
                        <span className="text-sm font-semibold text-slate-800">{opt.label}</span>
                      </div>
                      <span className="text-xs text-slate-500 ml-5">{opt.hint}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="grid sm:grid-cols-2 gap-4">
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">
                    Cross-dept Policy
                  </label>
                  <select
                    value={crossDeptPolicy}
                    onChange={(e) => setCrossDeptPolicy(e.target.value as CrossDeptPolicy)}
                    className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors"
                  >
                    {CROSS_DEPT_POLICY_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label} — {opt.hint}
                      </option>
                    ))}
                  </select>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">
                    Default Grant Duration
                  </label>
                  <select
                    value={crossDeptGrant}
                    onChange={(e) => setCrossDeptGrant(e.target.value as CrossDeptDefaultGrant)}
                    className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 transition-colors"
                  >
                    {DEFAULT_GRANT_OPTIONS.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">
                  Cross-dept Approvers{" "}
                  <span className="font-normal text-slate-400">(optional)</span>
                </label>
                <p className="text-xs text-slate-500 mb-2">
                  Departments whose managers can approve cross-dept access requests for this skill.
                </p>
                <DepartmentPicker
                  selected={crossDeptApprovers}
                  onChange={(depts) => setCrossDeptApprovers(depts.filter((d) => d !== "*"))}
                />
              </div>
            </section>

            {/* ── Skill Secrets ───────────────────────────────────────── */}
            <section className="p-6 space-y-3">
              <div>
                <h2 className="text-base font-bold text-slate-900">Skill Secrets</h2>
                <p className="text-xs text-slate-500 mt-1">
                  API keys or credentials your skill needs at runtime. Detected env var names are
                  pre-populated from your file — fill in their values. They are stored securely in
                  the vault and injected as environment variables accessible via{" "}
                  <code className="bg-slate-100 text-slate-700 px-1 py-0.5 rounded text-[11px] font-mono">
                    os.environ.get('KEY')
                  </code>
                  .
                </p>
              </div>
              <SkillSecretsEditor secrets={secrets} onChange={setSecrets} />
            </section>

            {/* ── Error + Submit ──────────────────────────────────────── */}
            <section className="p-6 space-y-3">
              {submitError && (
                <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                  <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                  <span>{submitError}</span>
                </div>
              )}
              {preflightStatus === "fail" && !submitError && (
                <p className="text-xs text-red-600 flex items-center gap-1.5">
                  <XCircle className="w-3.5 h-3.5 flex-shrink-0" />
                  Fix the security issues flagged above before submitting.
                </p>
              )}
              <button
                type="submit"
                disabled={submitDisabled}
                title={
                  preflightStatus === "fail"
                    ? "Fix security issues before submitting"
                    : preflightStatus === "reviewing" || preflightStatus === "pending"
                    ? "Waiting for security review to complete…"
                    : undefined
                }
                className="w-full flex items-center justify-center gap-2 bg-slate-900 hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold py-2.5 rounded-md transition-colors text-sm"
              >
                {submitting ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Submitting…
                  </>
                ) : preflightStatus === "reviewing" || preflightStatus === "pending" ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    Waiting for security review…
                  </>
                ) : (
                  <>
                    <Upload className="w-4 h-4" />
                    Submit for Review
                  </>
                )}
              </button>
            </section>
          </form>
        )}

        {/* ── My Submissions ──────────────────────────────────────────── */}
        <section>
          <div className="flex items-center justify-between mb-4">
            <h2 className="text-lg font-bold text-slate-900">My Submissions</h2>
            <button
              type="button"
              onClick={() => fetchSubmissions()}
              disabled={loadingSubmissions}
              className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-800 transition-colors"
            >
              <RefreshCw className={`w-3.5 h-3.5 ${loadingSubmissions ? "animate-spin" : ""}`} />
              Refresh
            </button>
          </div>

          {loadingSubmissions ? (
            <div className="flex justify-center py-12 text-slate-400">
              <Loader2 className="w-6 h-6 animate-spin" />
            </div>
          ) : submissions.length === 0 ? (
            <div className="text-center py-12 border border-dashed border-slate-200 rounded-xl">
              <FileText className="w-8 h-8 text-slate-300 mx-auto mb-2" />
              <p className="text-sm text-slate-500">No submissions yet.</p>
            </div>
          ) : (
            <div className="space-y-2">
              {submissions.map((sub) => (
                <SubmissionRow key={sub.id} sub={sub} onConfirmed={fetchSubmissions} />
              ))}
            </div>
          )}
        </section>

      </div>
    </div>
  );
}
