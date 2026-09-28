import { useState, useEffect, useCallback, useRef } from "react";
import {
  CheckCircle,
  Circle,
  RotateCcw,
  Trash2,
  Loader2,
  AlertCircle,
  Eye,
  EyeOff,
  ExternalLink,
  X,
  KeyRound,
  Plus,
  Plug,
} from "lucide-react";
import { INTEGRATIONS, isIntegrationConnected, type Integration, type CredentialField, type CredentialFieldType } from "../data/integrations";
import IntegrationLogo from "@/src/components/IntegrationLogo";
import { apiFetch } from "@/lib/api";
import { parseUTC, timeAgo } from "@/lib/time";

// ── Types ─────────────────────────────────────────────────────────────────────

interface VaultSecret {
  id: string;
  slug: string;
  display_name: string | null;
  description: string | null;
  updated_at: string;
  created_at: string;
}

type VaultMap = Record<string, VaultSecret>; // slug → secret

function isConnected(integration: Integration, vault: VaultMap): boolean {
  return isIntegrationConnected(integration, vault);
}

// ── CredentialInput ───────────────────────────────────────────────────────────

function CredentialInput({
  field,
  value,
  onChange,
}: {
  field: CredentialField;
  value: string;
  onChange: (v: string) => void;
}) {
  const [show, setShow] = useState(false);
  const isSensitive: boolean =
    field.type === "token" || field.type === "password" || field.type === "multiline";

  const baseClass =
    "w-full px-3 py-2 text-sm rounded-lg border border-slate-200 bg-white outline-none focus:ring-2 focus:ring-slate-900/10 focus:border-slate-400 transition-colors";

  if (field.type === "multiline") {
    return (
      <textarea
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        rows={6}
        className={`${baseClass} font-mono resize-none`}
        spellCheck={false}
        autoComplete="off"
      />
    );
  }

  const inputType: string =
    isSensitive && !show
      ? "password"
      : field.type === "url"
        ? "url"
        : "text";

  return (
    <div className="relative">
      <input
        type={inputType}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={field.placeholder}
        className={`${baseClass} ${isSensitive ? "pr-9 font-mono" : ""}`}
        autoComplete="new-password"
        spellCheck={false}
      />
      {isSensitive && (
        <button
          type="button"
          tabIndex={-1}
          onClick={() => setShow((v) => !v)}
          className="absolute right-2.5 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-600 transition-colors"
        >
          {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      )}
    </div>
  );
}

// ── Drawer ────────────────────────────────────────────────────────────────────

function Drawer({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex">
      <div className="flex-1 bg-black/20 backdrop-blur-[1px]" onClick={onClose} />
      <div className="w-[460px] bg-white border-l border-slate-200 flex flex-col shadow-xl">
        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-200 shrink-0">
          <span className="text-sm font-bold text-slate-900">{title}</span>
          <button
            onClick={onClose}
            className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-6 py-5">{children}</div>
      </div>
    </div>
  );
}

// ── RotateForm — rotate a single credential ───────────────────────────────────

function RotateForm({
  field,
  secret,
  onSuccess,
  onCancel,
}: {
  field: CredentialField;
  secret: VaultSecret;
  onSuccess: (updated: VaultSecret) => void;
  onCancel: () => void;
}) {
  const [value, setValue] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!value.trim()) {
      setError("Value is required.");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/secrets/${secret.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ slug: secret.slug, value: value.trim() }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
      onSuccess(await res.json() as VaultSecret);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg border border-slate-200">
        <KeyRound className="w-3.5 h-3.5 text-slate-400 shrink-0" />
        <span className="text-sm font-mono text-slate-700">{field.slug}</span>
      </div>

      <div>
        <label className="block text-xs font-semibold text-slate-600 mb-1.5">
          New value
        </label>
        <CredentialInput field={field} value={value} onChange={setValue} />
        {field.hint && (
          <p className="mt-1.5 text-xs text-slate-400">{field.hint}</p>
        )}
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
          disabled={saving || !value.trim()}
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold bg-slate-900 text-white rounded-lg hover:bg-slate-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
          Rotate
        </button>
      </div>
    </form>
  );
}

// ── ConnectForm — enter all credentials to connect an integration ─────────────

function ConnectForm({
  integration,
  onSuccess,
  onCancel,
}: {
  integration: Integration;
  onSuccess: (secrets: VaultSecret[]) => void;
  onCancel: () => void;
}) {
  const [values, setValues] = useState<Record<string, string>>(
    Object.fromEntries(integration.credentials.map((c) => [c.slug, ""]))
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const requiredFields = integration.credentials.filter((c) => c.required);
  const allRequiredFilled = requiredFields.every((c) => values[c.slug]?.trim());

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSaving(true);
    setError(null);

    const created: VaultSecret[] = [];
    try {
      for (const field of integration.credentials) {
        const val = values[field.slug]?.trim();
        if (!val) continue; // skip optional empty fields
        const res = await apiFetch("/api/secrets", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            slug: field.slug,
            value: val,
            display_name: `${integration.name} — ${field.label}`,
            description: `Credential for ${integration.name} integration`,
          }),
        });
        if (!res.ok) {
          const err = await res.json().catch(() => ({}));
          throw new Error((err as { detail?: string }).detail ?? `HTTP ${res.status}`);
        }
        created.push(await res.json() as VaultSecret);
      }
      onSuccess(created);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="space-y-5">
      {integration.docsUrl && (
        <a
          href={integration.docsUrl}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-800 transition-colors"
        >
          <ExternalLink className="w-3 h-3" />
          How to get credentials
        </a>
      )}

      {integration.credentials.map((field) => (
        <div key={field.slug}>
          <label className="block text-xs font-semibold text-slate-600 mb-1.5">
            {field.label}
            {!field.required && (
              <span className="ml-1.5 text-slate-400 font-normal">(optional)</span>
            )}
          </label>
          <CredentialInput
            field={field}
            value={values[field.slug] ?? ""}
            onChange={(v) => setValues((prev) => ({ ...prev, [field.slug]: v }))}
          />
          {field.hint && (
            <p className="mt-1.5 text-xs text-slate-400">{field.hint}</p>
          )}
        </div>
      ))}

      <p className="text-xs text-slate-400 pt-1">
        Values are stored in the vault and injected as environment variables when
        the agent runs code.
      </p>

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
          disabled={saving || !allRequiredFilled}
          className="flex items-center gap-2 px-4 py-2 text-sm font-bold bg-emerald-600 text-white rounded-lg hover:bg-emerald-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
        >
          {saving ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
          ) : (
            <Plug className="w-3.5 h-3.5" />
          )}
          Connect
        </button>
      </div>
    </form>
  );
}

// ── IntegrationDetail ─────────────────────────────────────────────────────────

type DrawerState =
  | { kind: "none" }
  | { kind: "connect" }
  | { kind: "rotate"; field: CredentialField; secret: VaultSecret }
  | { kind: "disconnect" };

function IntegrationDetail({
  integration,
  vault,
  onVaultChange,
}: {
  integration: Integration;
  vault: VaultMap;
  onVaultChange: (updated: VaultMap) => void;
}) {
  const [drawer, setDrawer] = useState<DrawerState>({ kind: "none" });
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [disconnecting, setDisconnecting] = useState(false);
  const [oauthConnecting, setOauthConnecting] = useState(false);
  const [oauthError, setOauthError] = useState<string | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Reset drawer when integration changes
  useEffect(() => {
    setDrawer({ kind: "none" });
    setSuccessMsg(null);
  }, [integration.id]);

  function flash(msg: string) {
    setSuccessMsg(msg);
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => setSuccessMsg(null), 3000);
  }

  const connected = isConnected(integration, vault);

  async function handleOAuthConnect() {
    const endpoint = integration.oauthStartEndpoint;
    if (!endpoint) return;
    setOauthConnecting(true);
    setOauthError(null);
    try {
      const res = await apiFetch(endpoint, { method: "POST" });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error((err as { detail?: string }).detail ?? `HTTP ${res.status}`);
      }
      const data = await res.json() as { redirect_url: string };
      window.location.href = data.redirect_url;
    } catch (err: unknown) {
      setOauthConnecting(false);
      setOauthError(err instanceof Error ? err.message : "Failed to start connection.");
    }
  }

  function handleConnected(secrets: VaultSecret[]) {
    const next = { ...vault };
    for (const s of secrets) next[s.slug] = s;
    onVaultChange(next);
    setDrawer({ kind: "none" });
    flash(`${integration.name} connected.`);
  }

  function handleRotated(updated: VaultSecret) {
    onVaultChange({ ...vault, [updated.slug]: updated });
    setDrawer({ kind: "none" });
    flash(`${updated.slug} rotated.`);
  }

  async function handleDisconnect() {
    setDisconnecting(true);
    const next = { ...vault };
    const slugsToDelete = [
      ...integration.credentials.map((c) => c.slug),
      ...(integration.oauthSecretSlugs ?? []),
    ];
    try {
      for (const slug of slugsToDelete) {
        const secret = vault[slug];
        if (!secret) continue;
        await apiFetch(`/api/secrets/${secret.id}`, { method: "DELETE" });
        delete next[slug];
      }
      onVaultChange(next);
      flash(`${integration.name} disconnected.`);
    } catch {
      flash("Couldn't disconnect. Please try again or refresh the page.");
    } finally {
      setDisconnecting(false);
      setDrawer({ kind: "none" });
    }
  }

  return (
    <>
      <div className="flex-1 overflow-y-auto">
        {/* Header */}
        <div className="px-8 py-6 border-b border-slate-200">
          <div className="flex items-start justify-between gap-4">
            <div className="flex items-center gap-4">
              <div className="w-12 h-12 rounded-xl bg-white border border-slate-200 flex items-center justify-center shrink-0 p-2">
                <IntegrationLogo integration={integration} className="w-8 h-8" />
              </div>
              <div>
                <div className="flex items-center gap-2.5 flex-wrap">
                  <h2 className="text-xl font-bold text-slate-900">{integration.name}</h2>
                  {connected ? (
                    <span className="flex items-center gap-1 text-xs font-semibold text-emerald-600 bg-emerald-50 border border-emerald-100 px-2 py-0.5 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 inline-block" />
                      Connected
                    </span>
                  ) : (
                    <span className="flex items-center gap-1 text-xs font-semibold text-slate-400 bg-slate-50 border border-slate-200 px-2 py-0.5 rounded-full">
                      <span className="w-1.5 h-1.5 rounded-full bg-slate-300 inline-block" />
                      Not connected
                    </span>
                  )}
                </div>
                <p className="text-sm text-slate-500 mt-1 max-w-xl leading-relaxed">
                  {integration.description}
                </p>
              </div>
            </div>

            {connected ? (
              <button
                onClick={() => setDrawer({ kind: "disconnect" })}
                disabled={disconnecting}
                className="flex items-center gap-1.5 px-3.5 py-2 text-sm font-medium text-slate-600 hover:text-red-600 hover:bg-red-50 border border-slate-200 hover:border-red-200 rounded-lg transition-colors shrink-0 disabled:opacity-50"
              >
                {disconnecting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Trash2 className="w-3.5 h-3.5" />
                )}
                Disconnect
              </button>
            ) : integration.oauthFlow ? (
              <button
                onClick={handleOAuthConnect}
                disabled={oauthConnecting}
                className="flex items-center gap-1.5 px-3.5 py-2 text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors shrink-0 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {oauthConnecting ? (
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                ) : (
                  <Plus className="w-3.5 h-3.5" />
                )}
                {oauthConnecting ? "Redirecting…" : `Connect with ${integration.name}`}
              </button>
            ) : (
              <button
                onClick={() => setDrawer({ kind: "connect" })}
                className="flex items-center gap-1.5 px-3.5 py-2 text-sm font-bold text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors shrink-0"
              >
                <Plus className="w-3.5 h-3.5" />
                Connect
              </button>
            )}
          </div>
        </div>

        {/* Success banner */}
        {successMsg && (
          <div className="mx-8 mt-4 flex items-center gap-2 px-4 py-2.5 bg-emerald-50 border border-emerald-200 rounded-lg text-sm text-emerald-700 font-medium">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />
            {successMsg}
          </div>
        )}

        {/* OAuth error banner */}
        {oauthError && (
          <div className="mx-8 mt-4 flex items-start gap-2 px-4 py-2.5 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
            <AlertCircle className="w-4 h-4 shrink-0 mt-0.5" />
            <span>{oauthError}</span>
            <button
              onClick={() => setOauthError(null)}
              className="ml-auto p-0.5 text-red-400 hover:text-red-600 transition-colors"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          </div>
        )}

        {/* Credentials section */}
        <div className="px-8 py-6 space-y-3">
          {integration.oauthFlow ? (
            /* OAuth integration — show token status instead of field list */
            <div className="space-y-3">
              <h3 className="text-xs font-bold text-slate-500 uppercase tracking-widest mb-2">
                Connection
              </h3>
              {(integration.oauthSecretSlugs ?? []).map((slug) => {
                const secret = vault[slug];
                return (
                  <div
                    key={slug}
                    className={`flex items-center gap-3 px-4 py-3.5 rounded-xl border transition-colors ${
                      secret
                        ? "bg-white border-slate-200"
                        : "bg-slate-50/60 border-dashed border-slate-200"
                    }`}
                  >
                    <div
                      className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                        secret ? "bg-emerald-50" : "bg-slate-100"
                      }`}
                    >
                      <KeyRound
                        className={`w-4 h-4 ${secret ? "text-emerald-500" : "text-slate-400"}`}
                      />
                    </div>
                    <div className="flex-1 min-w-0">
                      <span className="text-sm font-mono font-semibold text-slate-800">
                        {slug}
                      </span>
                      {secret ? (
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          Connected via Slack OAuth · Updated {timeAgo(secret.updated_at)}
                        </p>
                      ) : (
                        <p className="text-xs text-slate-400 mt-0.5">
                          Not connected — click "Connect with Slack" above
                        </p>
                      )}
                    </div>
                    {secret && (
                      <div className="px-2.5 py-1 bg-slate-50 border border-slate-200 rounded-md shrink-0">
                        <span className="text-xs font-mono text-slate-400 tracking-widest">
                          ••••••••
                        </span>
                      </div>
                    )}
                  </div>
                );
              })}
              {!connected && (
                <p className="text-xs text-slate-400 pt-1">
                  Clicking "Connect with Slack" will take you to Slack's authorization page.
                  Your token is stored in the vault and injected as{" "}
                  <code className="bg-slate-100 px-1 rounded text-[11px]">SLACK_USER_TOKEN</code>{" "}
                  in every{" "}
                  <code className="bg-slate-100 px-1 rounded text-[11px]">run_python</code> call.
                </p>
              )}
            </div>
          ) : (
            /* Standard credential-paste integration */
            <>
              <div className="flex items-center justify-between mb-2">
                <h3 className="text-xs font-bold text-slate-500 uppercase tracking-widest">
                  Credentials
                </h3>
                {integration.docsUrl && (
                  <a
                    href={integration.docsUrl}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="flex items-center gap-1 text-xs text-slate-400 hover:text-slate-700 transition-colors"
                  >
                    <ExternalLink className="w-3 h-3" />
                    Docs
                  </a>
                )}
              </div>

              {integration.credentials.map((field) => {
                const secret = vault[field.slug];
                return (
                  <div
                    key={field.slug}
                    className={`flex items-center gap-3 px-4 py-3.5 rounded-xl border transition-colors ${
                      secret
                        ? "bg-white border-slate-200 hover:border-slate-300"
                        : "bg-slate-50/60 border-dashed border-slate-200"
                    }`}
                  >
                    <div
                      className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${
                        secret ? "bg-emerald-50" : "bg-slate-100"
                      }`}
                    >
                      <KeyRound
                        className={`w-4 h-4 ${secret ? "text-emerald-500" : "text-slate-400"}`}
                      />
                    </div>

                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span className="text-sm font-mono font-semibold text-slate-800">
                          {field.slug}
                        </span>
                        {!field.required && (
                          <span className="text-[10px] font-semibold text-slate-400 bg-slate-100 px-1.5 py-0.5 rounded">
                            optional
                          </span>
                        )}
                      </div>
                      <p className="text-xs text-slate-500 mt-0.5">{field.label}</p>
                      {secret && (
                        <p className="text-[11px] text-slate-400 mt-0.5">
                          Updated {timeAgo(secret.updated_at)}
                        </p>
                      )}
                    </div>

                    {secret ? (
                      <div className="flex items-center gap-2 shrink-0">
                        <div className="px-2.5 py-1 bg-slate-50 border border-slate-200 rounded-md">
                          <span className="text-xs font-mono text-slate-400 tracking-widest">
                            ••••••••
                          </span>
                        </div>
                        <button
                          onClick={() =>
                            setDrawer({ kind: "rotate", field, secret })
                          }
                          title="Rotate value"
                          className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
                        >
                          <RotateCcw className="w-3.5 h-3.5" />
                        </button>
                      </div>
                    ) : (
                      <button
                        onClick={() => setDrawer({ kind: "connect" })}
                        className="text-xs font-medium text-slate-500 hover:text-emerald-600 transition-colors shrink-0"
                      >
                        Set value
                      </button>
                    )}
                  </div>
                );
              })}

              {!connected && (
                <p className="text-xs text-slate-400 pt-1">
                  Connect this integration to make its credentials available in every{" "}
                  <code className="bg-slate-100 px-1 rounded text-[11px]">run_python</code> call.
                </p>
              )}
            </>
          )}
        </div>
      </div>

      {/* Drawers */}
      {drawer.kind === "connect" && (
        <Drawer
          title={`Connect ${integration.name}`}
          onClose={() => setDrawer({ kind: "none" })}
        >
          <ConnectForm
            integration={integration}
            onSuccess={handleConnected}
            onCancel={() => setDrawer({ kind: "none" })}
          />
        </Drawer>
      )}

      {drawer.kind === "rotate" && (
        <Drawer
          title={`Rotate ${drawer.field.slug}`}
          onClose={() => setDrawer({ kind: "none" })}
        >
          <RotateForm
            field={drawer.field}
            secret={drawer.secret}
            onSuccess={handleRotated}
            onCancel={() => setDrawer({ kind: "none" })}
          />
        </Drawer>
      )}

      {drawer.kind === "disconnect" && (
        <Drawer
          title={`Disconnect ${integration.name}`}
          onClose={() => setDrawer({ kind: "none" })}
        >
          <div className="space-y-4">
            {(() => {
              const slugsToShow = [
                ...integration.credentials.map((c) => c.slug),
                ...(integration.oauthSecretSlugs ?? []),
              ].filter((slug) => vault[slug]);
              return (
                <>
                  <p className="text-sm text-slate-700">
                    This will permanently delete{" "}
                    <span className="font-semibold text-slate-900">
                      {slugsToShow.length}
                    </span>{" "}
                    stored credential{slugsToShow.length !== 1 ? "s" : ""} for{" "}
                    <span className="font-semibold text-slate-900">{integration.name}</span> from
                    the vault. Any skill or automation that relied on them will stop working.
                  </p>
                  <div className="space-y-1.5">
                    {slugsToShow.map((slug) => (
                      <div
                        key={slug}
                        className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg text-xs font-mono text-slate-600 border border-slate-200"
                      >
                        <KeyRound className="w-3 h-3 text-slate-400 shrink-0" />
                        {slug}
                      </div>
                    ))}
                  </div>
                </>
              );
            })()}

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setDrawer({ kind: "none" })}
                className="px-4 py-2 text-sm font-medium text-slate-600 hover:text-slate-900 hover:bg-slate-100 rounded-lg transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleDisconnect}
                disabled={disconnecting}
                className="flex items-center gap-2 px-4 py-2 text-sm font-bold bg-red-600 text-white rounded-lg hover:bg-red-700 disabled:opacity-40 transition-colors"
              >
                {disconnecting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
                Disconnect
              </button>
            </div>
          </div>
        </Drawer>
      )}
    </>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function IntegrationsPage() {
  const [vault, setVault] = useState<VaultMap>({});
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [selected, setSelected] = useState<Integration>(INTEGRATIONS[0]);
  const [oauthBanner, setOauthBanner] = useState<{ kind: "success" | "error"; msg: string } | null>(null);

  const loadVault = useCallback(async () => {
    setLoading(true);
    setFetchError(null);
    try {
      const res = await apiFetch("/api/secrets");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const secrets: VaultSecret[] = await res.json();
      const map: VaultMap = {};
      for (const s of secrets) map[s.slug] = s;
      setVault(map);
    } catch (e: unknown) {
      setFetchError(e instanceof Error ? e.message : "Failed to load credentials.");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    loadVault();
  }, [loadVault]);

  // Handle OAuth callback return params.
  // Generic: ?oauth_connected=<integration-id> | ?oauth_error=<reason>&oauth_integration=<id>
  // Legacy compat: ?slack_connected=1 | ?slack_error=<reason>
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    let shouldClean = false;

    const connectedId = params.get("oauth_connected");
    const errorReason = params.get("oauth_error");
    const errorIntegId = params.get("oauth_integration");

    // Legacy Slack params (backward compat for any bookmarked redirects)
    const legacyConnected = params.get("slack_connected") === "1";
    const legacyError = params.get("slack_error");

    if (connectedId) {
      const integ = INTEGRATIONS.find((i) => i.id === connectedId);
      const name = integ?.name ?? connectedId;
      setOauthBanner({ kind: "success", msg: `${name} connected successfully.` });
      if (integ) setSelected(integ);
      shouldClean = true;
    } else if (errorReason) {
      const integ = INTEGRATIONS.find((i) => i.id === (errorIntegId ?? ""));
      const name = integ?.name ?? errorIntegId ?? "Integration";
      setOauthBanner({ kind: "error", msg: `${name} couldn't connect. Please try again. If this keeps happening, contact support.` });
      if (integ) setSelected(integ);
      shouldClean = true;
    } else if (legacyConnected) {
      setOauthBanner({ kind: "success", msg: "Slack connected successfully." });
      const slackInteg = INTEGRATIONS.find((i) => i.id === "slack");
      if (slackInteg) setSelected(slackInteg);
      shouldClean = true;
    } else if (legacyError) {
      setOauthBanner({ kind: "error", msg: "Slack couldn't connect. Please try again. If this keeps happening, contact support." });
      const slackInteg = INTEGRATIONS.find((i) => i.id === "slack");
      if (slackInteg) setSelected(slackInteg);
      shouldClean = true;
    }

    if (shouldClean) {
      window.history.replaceState({}, "", window.location.pathname);
    }
  }, []);

  const connected = INTEGRATIONS.filter((i) => isConnected(i, vault));
  const available = INTEGRATIONS.filter((i) => !isConnected(i, vault));

  return (
    <div className="flex flex-1 h-full overflow-hidden flex-col">
      {/* Page-level OAuth callback banner */}
      {oauthBanner && (
        <div
          className={`flex items-center gap-3 px-6 py-3 text-sm font-medium border-b ${
            oauthBanner.kind === "success"
              ? "bg-emerald-50 border-emerald-200 text-emerald-800"
              : "bg-red-50 border-red-200 text-red-800"
          }`}
        >
          {oauthBanner.kind === "success" ? (
            <CheckCircle className="w-4 h-4 shrink-0 text-emerald-600" />
          ) : (
            <AlertCircle className="w-4 h-4 shrink-0 text-red-500" />
          )}
          <span className="flex-1">{oauthBanner.msg}</span>
          <button
            onClick={() => setOauthBanner(null)}
            className="p-0.5 text-current opacity-60 hover:opacity-100 transition-opacity"
          >
            <X className="w-3.5 h-3.5" />
          </button>
        </div>
      )}

      <div className="flex flex-1 overflow-hidden">
      {/* Sidebar */}
      <aside className="w-64 shrink-0 border-r border-slate-200 bg-white overflow-y-auto flex flex-col">
        {loading ? (
          <div className="flex items-center justify-center py-16 text-slate-400">
            <Loader2 className="w-4 h-4 animate-spin" />
          </div>
        ) : fetchError ? (
          <div className="px-4 py-6 text-xs text-red-500">{fetchError}</div>
        ) : (
          <>
            {connected.length > 0 && (
              <div className="px-4 pt-5 pb-3">
                <h2 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-2">
                  Connected
                </h2>
                {connected.map((intg) => (
                  <button
                    key={intg.id}
                    onClick={() => setSelected(intg)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors mb-0.5 ${
                      selected.id === intg.id
                        ? "bg-slate-100 text-slate-900"
                        : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
                    }`}
                  >
                    <IntegrationLogo integration={intg} className="w-5 h-5" />
                    <span className="text-sm font-medium flex-1 truncate">{intg.name}</span>
                    <CheckCircle className="w-3.5 h-3.5 text-emerald-500 shrink-0" />
                  </button>
                ))}
              </div>
            )}

            {available.length > 0 && (
              <div className={`px-4 pb-5 pt-2 ${connected.length > 0 ? "border-t border-slate-100" : ""}`}>
                <h2 className="text-[10px] font-bold text-slate-400 uppercase tracking-widest px-2 mb-2 mt-3">
                  Available
                </h2>
                {available.map((intg) => (
                  <button
                    key={intg.id}
                    onClick={() => setSelected(intg)}
                    className={`w-full flex items-center gap-3 px-3 py-2.5 rounded-lg text-left transition-colors mb-0.5 ${
                      selected.id === intg.id
                        ? "bg-slate-100 text-slate-900"
                        : "text-slate-400 hover:bg-slate-50 hover:text-slate-700"
                    }`}
                  >
                    <IntegrationLogo integration={intg} className="w-5 h-5" />
                    <span className="text-sm font-medium flex-1 truncate">{intg.name}</span>
                    <Circle className="w-3.5 h-3.5 text-slate-300 shrink-0" />
                  </button>
                ))}
              </div>
            )}
          </>
        )}
      </aside>

      {/* Detail panel */}
      <div className="flex-1 flex flex-col overflow-hidden bg-white">
        {selected && !loading && (
          <IntegrationDetail
            key={selected.id}
            integration={selected}
            vault={vault}
            onVaultChange={setVault}
          />
        )}
      </div>
      </div>
    </div>
  );
}
