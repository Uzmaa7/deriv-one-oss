import { useState, useEffect, useCallback, useRef } from "react";
import { Plus, Trash2, RotateCcw, KeyRound, Eye, EyeOff, Loader2, AlertCircle, X, ChevronDown } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { parseUTC, timeAgo } from "@/lib/time";

// ── Types ─────────────────────────────────────────────────────────────────────

interface UserSecret {
  id: string;
  slug: string;
  display_name: string | null;
  description: string | null;
  created_at: string;
  updated_at: string;
}

// ── Helpers ───────────────────────────────────────────────────────────────────

const SLUG_RE = /^[A-Z][A-Z0-9_]{1,99}$/;
const BLOCKED_PREFIXES = ["MODEL_GATEWAY_", "GCP_", "GOOGLE_", "TEAM_", "POLICY_", "ENABLE_MOCK", "HUB_DATABASE", "ANTHROPIC_"];
const BLOCKED_EXACT = new Set(["PATH", "HOME", "USER", "TMPDIR", "TEMP", "TMP", "LANG", "LC_ALL", "LC_CTYPE", "PYTHONDONTWRITEBYTECODE", "PYTHONUNBUFFERED", "SLACK_TOKEN"]);

function validateSlug(slug: string): string | null {
  if (!SLUG_RE.test(slug))
    return "Must be UPPER_SNAKE_CASE, start with a letter, 2–100 chars (e.g. MY_API_KEY)";
  if (BLOCKED_EXACT.has(slug))
    return `"${slug}" is a reserved name`;
  for (const p of BLOCKED_PREFIXES)
    if (slug.startsWith(p)) return `Cannot start with reserved prefix "${p}"`;
  return null;
}

// ── SecretForm — shared for add and rotate ────────────────────────────────────

interface SecretFormProps {
  mode: "add" | "rotate";
  existingId?: string;   // UUID — used in PUT URL
  existingSlug?: string; // displayed to the user
  onSuccess: (secret: UserSecret) => void;
  onCancel: () => void;
}

function SecretForm({ mode, existingId, existingSlug, onSuccess, onCancel }: SecretFormProps) {
  const [slug, setSlug] = useState(existingSlug ?? "");
  const [value, setValue] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [description, setDescription] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const slugError = mode === "add" ? validateSlug(slug) : null;
  const slugRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (mode === "add") slugRef.current?.focus();
  }, [mode]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (mode === "add" && slugError) return;
    if (!value.trim()) { setError("Secret value is required."); return; }

    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(mode === "add" ? "/api/secrets" : `/api/secrets/${existingId}`, {
        method: mode === "add" ? "POST" : "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slug: mode === "add" ? slug : existingSlug,
          value: value.trim(),
          display_name: displayName.trim() || null,
          description: description.trim() || null,
        }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail ?? `HTTP ${res.status}`);
      }
      const created: UserSecret = await res.json();
      onSuccess(created);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      {/* Slug */}
      {mode === "add" ? (
        <div>
          <label className="block text-xs font-semibold text-slate-600 mb-1.5">
            Secret name <span className="text-slate-400 font-normal">(env var key)</span>
          </label>
          <input
            ref={slugRef}
            value={slug}
            onChange={e => setSlug(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))}
            placeholder="MY_API_KEY"
            className={`w-full px-3 py-2 text-sm font-mono rounded-lg border bg-white transition-colors outline-none focus:ring-2 ${
              slug && slugError
                ? "border-red-300 focus:ring-red-100"
                : "border-slate-200 focus:ring-slate-900/10 focus:border-slate-400"
            }`}
            maxLength={100}
            autoComplete="off"
            spellCheck={false}
          />
          {slug && slugError && (
            <p className="mt-1.5 text-xs text-red-600">{slugError}</p>
          )}
          <p className="mt-1.5 text-xs text-slate-400">
            Available inside every <code className="bg-slate-100 px-1 rounded text-[11px]">run_python</code> call as <code className="bg-slate-100 px-1 rounded text-[11px]">os.environ['{slug || "KEY"}']</code>
          </p>
        </div>
      ) : (
        <div className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg border border-slate-200">
          <KeyRound className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <span className="text-sm font-mono text-slate-700">{existingSlug}</span>
        </div>
      )}

      {/* Value */}
      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5">
          {mode === "rotate" ? "New value" : "Secret value"}
        </label>
        <div className="relative">
          <input
            type={showValue ? "text" : "password"}
            value={value}
            onChange={e => setValue(e.target.value)}
            placeholder={mode === "rotate" ? "Enter new value to replace current…" : "Paste your secret here…"}
            className="w-full px-3 py-2 pr-9 text-sm font-mono rounded-lg border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10 focus:border-slate-400 transition-colors"
            autoComplete="new-password"
            spellCheck={false}
          />
          <button
            type="button"
            tabIndex={-1}
            onClick={() => setShowValue(v => !v)}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
          >
            {showValue ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
        <p className="mt-1.5 text-xs text-slate-400">
          Stored in the vault. Never saved to the database.
        </p>
      </div>

      {/* Display name */}
      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5">
          Label <span className="text-slate-400 font-normal">(optional)</span>
        </label>
        <input
          type="text"
          value={displayName}
          onChange={e => setDisplayName(e.target.value)}
          placeholder="e.g. Personal Slack token"
          className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10 focus:border-slate-400 transition-colors"
          maxLength={200}
        />
      </div>

      {/* Description */}
      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5">
          Description <span className="text-slate-400 font-normal">(optional)</span>
        </label>
        <textarea
          value={description}
          onChange={e => setDescription(e.target.value)}
          placeholder="What is this secret used for?"
          rows={2}
          className="w-full px-3 py-2 text-sm rounded-lg border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10 focus:border-slate-400 transition-colors resize-none"
          maxLength={500}
        />
      </div>

      {error && (
        <div className="flex items-start gap-2 px-3 py-2.5 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
          <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
          {error}
        </div>
      )}

      <div className="flex items-center justify-end gap-2 pt-1">
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          type="submit"
          disabled={saving || (mode === "add" && (!slug || !!slugError)) || !value.trim()}
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          {mode === "rotate" ? "Rotate secret" : "Save secret"}
        </button>
      </div>
    </form>
  );
}

// ── SecretRow ─────────────────────────────────────────────────────────────────

interface SecretRowProps {
  secret: UserSecret;
  onRotate: () => void;
  onDelete: () => void;
}

function SecretRow({ secret, onRotate, onDelete }: SecretRowProps) {
  return (
    <div className="flex items-center gap-3 px-4 py-3.5 bg-white border border-slate-200 rounded-xl group hover:border-slate-300 transition-colors">
      <div className="w-8 h-8 rounded-lg bg-slate-100 flex items-center justify-center shrink-0">
        <KeyRound className="w-4 h-4 text-slate-500" />
      </div>

      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-sm font-mono font-semibold text-slate-800">
            {secret.slug}
          </span>
          {secret.display_name && (
            <span className="text-xs text-slate-500 truncate">
              — {secret.display_name}
            </span>
          )}
        </div>
        {secret.description && (
          <p className="text-xs text-slate-400 mt-0.5 truncate">{secret.description}</p>
        )}
        <div className="flex items-center gap-3 mt-1">
          <span className="text-[11px] text-slate-400">
            Added {timeAgo(secret.created_at)}
          </span>
          {secret.updated_at !== secret.created_at && (
            <span className="text-[11px] text-slate-400">
              · Rotated {timeAgo(secret.updated_at)}
            </span>
          )}
        </div>
      </div>

      {/* Value is always masked — only show placeholder */}
      <div className="flex items-center gap-1 px-2.5 py-1 bg-slate-50 border border-slate-200 rounded-md">
        <span className="text-xs font-mono text-slate-400 tracking-widest">••••••••</span>
      </div>

      <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
        <button
          onClick={onRotate}
          title="Rotate value"
          className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
        >
          <RotateCcw className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={onDelete}
          title="Delete secret"
          className="p-1.5 text-slate-400 hover:text-red-600 hover:bg-red-50 rounded-lg transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  );
}

// ── DeleteConfirm ─────────────────────────────────────────────────────────────

interface DeleteConfirmProps {
  secret: UserSecret;
  onConfirm: () => Promise<void>;
  onCancel: () => void;
}

function DeleteConfirm({ secret, onConfirm, onCancel }: DeleteConfirmProps) {
  const [deleting, setDeleting] = useState(false);

  async function go() {
    setDeleting(true);
    await onConfirm();
    setDeleting(false);
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-slate-700">
        This will permanently delete{" "}
        <span className="font-mono font-semibold text-slate-900">{secret.slug}</span>{" "}
        from the vault. Any skill or automation that relied on it will stop working.
      </p>
      <div className="flex items-center justify-end gap-2">
        <button
          onClick={onCancel}
          className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
        >
          Cancel
        </button>
        <button
          onClick={go}
          disabled={deleting}
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-40 transition-colors"
        >
          {deleting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Delete
        </button>
      </div>
    </div>
  );
}

// ── Drawer — slide-in panel for forms ────────────────────────────────────────

interface DrawerProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

function Drawer({ title, onClose, children }: DrawerProps) {
  return (
    <div className="fixed inset-0 z-50 flex">
      {/* backdrop */}
      <div className="flex-1 bg-black/20 backdrop-blur-[1px]" onClick={onClose} />
      {/* panel */}
      <div className="w-[440px] bg-white border-l border-slate-200 flex flex-col shadow-xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 shrink-0">
          <span className="text-sm font-bold text-slate-900">{title}</span>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">
          {children}
        </div>
      </div>
    </div>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

type DrawerState =
  | { kind: "none" }
  | { kind: "add" }
  | { kind: "rotate"; secret: UserSecret }
  | { kind: "delete"; secret: UserSecret };

export default function UserVaultTab() {
  const [secrets, setSecrets] = useState<UserSecret[]>([]);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [drawer, setDrawer] = useState<DrawerState>({ kind: "none" });
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [expanded, setExpanded] = useState(false);

  const successTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  function showSuccess(msg: string) {
    setSuccessMsg(msg);
    if (successTimer.current) clearTimeout(successTimer.current);
    successTimer.current = setTimeout(() => setSuccessMsg(null), 3000);
  }

  const load = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await apiFetch("/api/secrets");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSecrets(await res.json());
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : "Failed to load secrets.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  function handleAdded(s: UserSecret) {
    setSecrets(prev => {
      const idx = prev.findIndex(x => x.id === s.id);
      return idx >= 0 ? prev.map((x, i) => i === idx ? s : x) : [s, ...prev];
    });
    setDrawer({ kind: "none" });
    showSuccess(`"${s.slug}" saved.`);
  }

  function handleRotated(s: UserSecret) {
    setSecrets(prev => prev.map(x => x.id === s.id ? s : x));
    setDrawer({ kind: "none" });
    showSuccess(`"${s.slug}" rotated.`);
  }

  async function handleDelete(secret: UserSecret) {
    const res = await apiFetch(`/api/secrets/${secret.id}`, { method: "DELETE" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    setSecrets(prev => prev.filter(x => x.id !== secret.id));
    setDrawer({ kind: "none" });
    showSuccess(`"${secret.slug}" deleted.`);
  }

  return (
    <>
      <div className="max-w-2xl mx-auto px-8 py-10 space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div>
            <h2 className="text-base font-bold text-slate-900">User Vault</h2>
            <p className="text-sm text-slate-500 mt-1 leading-relaxed">
              Personal secrets injected as environment variables into every skill's{" "}
              <code className="bg-slate-100 px-1 rounded text-[12px]">run_python</code> sandbox.
              Values are stored in the vault and never saved to the database.
            </p>
          </div>
          <button
            onClick={() => setDrawer({ kind: "add" })}
            className="flex items-center gap-2 px-3.5 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors shrink-0"
          >
            <Plus className="w-4 h-4" />
            Add secret
          </button>
        </div>

        {/* How it works — collapsible */}
        <div className="border border-slate-200 rounded-xl bg-white overflow-hidden">
          <button
            className="w-full flex items-center justify-between px-4 py-3 text-sm font-semibold text-slate-700 hover:bg-slate-50 transition-colors"
            onClick={() => setExpanded(v => !v)}
          >
            <span>How it works</span>
            <ChevronDown className={`w-4 h-4 text-slate-400 transition-transform ${expanded ? "rotate-180" : ""}`} />
          </button>
          {expanded && (
            <div className="px-4 pb-4 text-sm text-slate-600 space-y-2 border-t border-slate-100">
              <p className="pt-3">
                Secrets you add here are injected alongside any skill-level secrets when the
                agent executes Python code. Skill secrets (set by admins) take precedence if a
                name collides.
              </p>
              <p>
                Inside <code className="bg-slate-100 px-1 rounded text-[12px]">run_python</code> tool calls, access your secret with:
              </p>
              <pre className="bg-slate-50 border border-slate-200 rounded-lg px-4 py-3 text-[12px] font-mono text-slate-700 overflow-x-auto">
{`import os
token = os.environ["MY_API_KEY"]`}
              </pre>
              <p className="text-xs text-slate-400">
                Secrets are never logged, never echoed back by the model, and never
                included in conversation history.
              </p>
            </div>
          )}
        </div>

        {/* Success banner */}
        {successMsg && (
          <div className="flex items-center gap-2 px-4 py-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-700 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
            {successMsg}
          </div>
        )}

        {/* Body */}
        {loading && (
          <div className="flex items-center justify-center py-16 text-slate-400">
            <Loader2 className="w-5 h-5 animate-spin mr-2" />
            <span className="text-sm">Loading vault…</span>
          </div>
        )}

        {fetchError && !loading && (
          <div className="flex items-start gap-2 px-4 py-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            {fetchError}
          </div>
        )}

        {!loading && !fetchError && secrets.length === 0 && (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <div className="w-12 h-12 rounded-2xl bg-slate-100 flex items-center justify-center mb-4">
              <KeyRound className="w-6 h-6 text-slate-400" />
            </div>
            <p className="text-sm font-semibold text-slate-700">No secrets yet</p>
            <p className="text-sm text-slate-400 mt-1">
              Add your first secret to make it available in skill executions.
            </p>
            <button
              onClick={() => setDrawer({ kind: "add" })}
              className="mt-4 flex items-center gap-2 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-800 transition-colors"
            >
              <Plus className="w-4 h-4" /> Add secret
            </button>
          </div>
        )}

        {!loading && !fetchError && secrets.length > 0 && (
          <div className="space-y-2">
            {secrets.map(s => (
              <SecretRow
                key={s.id}
                secret={s}
                onRotate={() => setDrawer({ kind: "rotate", secret: s })}
                onDelete={() => setDrawer({ kind: "delete", secret: s })}
              />
            ))}
          </div>
        )}
      </div>

      {/* Drawers */}
      {drawer.kind === "add" && (
        <Drawer title="Add secret" onClose={() => setDrawer({ kind: "none" })}>
          <SecretForm
            mode="add"
            onSuccess={handleAdded}
            onCancel={() => setDrawer({ kind: "none" })}
          />
        </Drawer>
      )}

      {drawer.kind === "rotate" && (
        <Drawer title="Rotate secret" onClose={() => setDrawer({ kind: "none" })}>
          <SecretForm
            mode="rotate"
            existingId={drawer.secret.id}
            existingSlug={drawer.secret.slug}
            onSuccess={handleRotated}
            onCancel={() => setDrawer({ kind: "none" })}
          />
        </Drawer>
      )}

      {drawer.kind === "delete" && (
        <Drawer title="Delete secret" onClose={() => setDrawer({ kind: "none" })}>
          <DeleteConfirm
            secret={drawer.secret}
            onConfirm={() => handleDelete(drawer.secret)}
            onCancel={() => setDrawer({ kind: "none" })}
          />
        </Drawer>
      )}
    </>
  );
}
