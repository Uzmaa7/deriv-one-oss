import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  Check,
  CheckCircle2,
  ClipboardCopy,
  ExternalLink,
  Loader2,
  MessageSquare,
  RefreshCw,
  Server,
  Shield,
  Sparkles,
  Users,
  Wand2,
} from "lucide-react";
import { apiFetch } from "@/lib/api";
import { useBranding } from "@/lib/branding";

type DeploymentMode = "personal" | "team";

export interface SetupDraft {
  deployment_mode: DeploymentMode;
  branding: {
    app_name: string;
    app_short_name: string;
    app_logo_url: string;
    org_noun: string;
    brand_color: string;
    brand_color_hover: string;
    brand_color_soft: string;
  };
  core: {
    app_base_url: string;
    resource_prefix: string;
    app_env: string;
  };
  models: {
    gateway_url: string;
    gateway_api_key: string;
    encryption_key: string;
  };
  identity: {
    mock_user_email: string;
    mock_user_groups: string;
    oidc_issuer: string;
    policy_aud: string;
    oidc_jwks_url: string;
    admin_groups: string;
    member_groups: string;
  };
  directory: {
    provider: "claims" | "file";
    file_content: string;
  };
  slack: {
    mode: "skip" | "manual" | "auto_create";
    config_collaborator: string;
    config_access_token: string;
    config_refresh_token: string;
    ip_allowlist: string;
  };
}

const SETUP_STEP_KEY = "platform-setup-step";

function readStoredStep(): number {
  try {
    const raw = sessionStorage.getItem(SETUP_STEP_KEY);
    if (raw == null) return 0;
    const n = Number.parseInt(raw, 10);
    if (Number.isNaN(n) || n < 0) return 0;
    return n;
  } catch {
    return 0;
  }
}

function writeStoredStep(step: number): void {
  try {
    sessionStorage.setItem(SETUP_STEP_KEY, String(step));
  } catch {
    /* private mode / blocked storage */
  }
}

function clearStoredStep(): void {
  try {
    sessionStorage.removeItem(SETUP_STEP_KEY);
  } catch {
    /* ignore */
  }
}

const STEPS = [
  { id: "welcome", title: "Welcome", icon: Sparkles },
  { id: "branding", title: "Branding", icon: Wand2 },
  { id: "models", title: "Models", icon: Server },
  { id: "identity", title: "Identity", icon: Shield },
  { id: "directory", title: "Directory", icon: Users },
  { id: "slack", title: "Slack", icon: Users },
  { id: "review", title: "Review", icon: CheckCircle2 },
] as const;

const DEFAULT_DRAFT: SetupDraft = {
  deployment_mode: "personal",
  branding: {
    app_name: "Platform",
    app_short_name: "Platform",
    app_logo_url: "/app-logo-icon.svg",
    org_noun: "employee",
    brand_color: "#FF444F",
    brand_color_hover: "#e03d47",
    brand_color_soft: "#FFF0F0",
  },
  core: {
    app_base_url: "http://localhost:8080",
    resource_prefix: "platform",
    app_env: "development",
  },
  models: {
    gateway_url: "http://host.docker.internal:4000",
    gateway_api_key: "",
    encryption_key: "",
  },
  identity: {
    mock_user_email: "dev@localhost.local",
    mock_user_groups: "platform-admins,platform-users",
    oidc_issuer: "",
    policy_aud: "",
    oidc_jwks_url: "",
    admin_groups: "platform-admins",
    member_groups: "platform-users",
  },
  directory: {
    provider: "claims",
    file_content: "",
  },
  slack: {
    mode: "skip",
    config_collaborator: "",
    config_access_token: "",
    config_refresh_token: "",
    ip_allowlist: "",
  },
};

function mergeDraft(base: SetupDraft, patch?: Partial<SetupDraft> & Record<string, unknown>): SetupDraft {
  if (!patch) return base;
  return {
    ...base,
    ...patch,
    branding: { ...base.branding, ...(patch.branding as SetupDraft["branding"] | undefined) },
    core: { ...base.core, ...(patch.core as SetupDraft["core"] | undefined) },
    models: { ...base.models, ...(patch.models as SetupDraft["models"] | undefined) },
    identity: { ...base.identity, ...(patch.identity as SetupDraft["identity"] | undefined) },
    directory: { ...base.directory, ...(patch.directory as SetupDraft["directory"] | undefined) },
    slack: { ...base.slack, ...(patch.slack as SetupDraft["slack"] | undefined) },
  };
}

export default function SetupWizardPage() {
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const branding = useBranding();
  const statusLoadedRef = useRef(false);
  const [step, setStep] = useState(() => {
    const stored = readStoredStep();
    return stored >= 0 && stored < STEPS.length ? stored : 0;
  });
  const [draft, setDraft] = useState<SetupDraft>(DEFAULT_DRAFT);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [completing, setCompleting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [modelTest, setModelTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [oidcTest, setOidcTest] = useState<{ ok: boolean; message: string } | null>(null);
  const [completeResult, setCompleteResult] = useState<SetupCompleteResult | null>(null);

  const goToStep = useCallback((next: number | ((prev: number) => number)) => {
    setStep(prev => {
      const resolved = typeof next === "function" ? next(prev) : next;
      const clamped = Math.max(0, Math.min(STEPS.length - 1, resolved));
      writeStoredStep(clamped);
      return clamped;
    });
  }, []);

  const isRerun = searchParams.get("rerun") === "1";

  useEffect(() => {
    if (isRerun) {
      clearStoredStep();
      goToStep(0);
    }
  }, [isRerun, goToStep]);

  useEffect(() => {
    let cancelled = false;
    const isInitialLoad = !statusLoadedRef.current;
    (async () => {
      if (isInitialLoad) {
        setLoading(true);
      }
      try {
        const res = await apiFetch("/api/admin/setup/status");
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        if (cancelled) return;
        const saved = data.row?.draft;
        if (saved && typeof saved === "object") {
          setDraft(mergeDraft(DEFAULT_DRAFT, saved as Partial<SetupDraft>));
        }
        if (data.row?.deployment_mode === "team" || data.row?.deployment_mode === "personal") {
          setDraft(d => ({ ...d, deployment_mode: data.row.deployment_mode }));
        }
        if (isInitialLoad && !isRerun) {
          const stored = readStoredStep();
          if (stored >= 0 && stored < STEPS.length) {
            goToStep(stored);
          }
        }
      } catch (e) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : "Could not load setup status");
        }
      } finally {
        if (!cancelled) {
          setLoading(false);
          statusLoadedRef.current = true;
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [goToStep, isRerun]);

  const persistDraft = async (next: SetupDraft) => {
    setSaving(true);
    try {
      await apiFetch("/api/admin/setup/draft", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ deployment_mode: next.deployment_mode, draft: next }),
      });
    } catch {
      /* non-blocking */
    } finally {
      setSaving(false);
    }
  };

  const updateDraft = (patch: Partial<SetupDraft>) => {
    setDraft(prev => {
      const next = mergeDraft(prev, patch);
      void persistDraft(next);
      return next;
    });
  };

  const generateKey = async () => {
    const res = await apiFetch("/api/admin/setup/generate-encryption-key", { method: "POST" });
    const data = await res.json();
    if (data.encryption_key) {
      updateDraft({ models: { ...draft.models, encryption_key: data.encryption_key } });
    }
  };

  const testModels = async () => {
    setModelTest(null);
    const res = await apiFetch("/api/admin/setup/validate/models", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        gateway_url: draft.models.gateway_url,
        gateway_api_key: draft.models.gateway_api_key,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      setModelTest({
        ok: true,
        message: `Connected — ${data.model_count ?? 0} models found`,
      });
    } else {
      setModelTest({ ok: false, message: data.error || "Connection failed" });
    }
  };

  const testOidc = async () => {
    setOidcTest(null);
    const res = await apiFetch("/api/admin/setup/validate/oidc", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        oidc_issuer: draft.identity.oidc_issuer,
        policy_aud: draft.identity.policy_aud,
        oidc_jwks_url: draft.identity.oidc_jwks_url,
      }),
    });
    const data = await res.json();
    if (data.ok) {
      setOidcTest({ ok: true, message: `JWKS resolved at ${data.jwks_uri}` });
    } else {
      setOidcTest({ ok: false, message: data.error || "OIDC validation failed" });
    }
  };

  const complete = async () => {
    setCompleting(true);
    setError(null);
    try {
      const res = await apiFetch("/api/admin/setup/complete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ draft }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || `HTTP ${res.status}`);
      }
      setCompleteResult(data as SetupCompleteResult);
      clearStoredStep();
      goToStep(STEPS.length - 1);
      await branding.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Setup failed");
    } finally {
      setCompleting(false);
    }
  };

  const canNext = useMemo(() => {
    if (step === 2) {
      return (
        draft.models.gateway_url.trim() &&
        draft.models.encryption_key.trim()
      );
    }
    if (step === 3 && draft.deployment_mode === "team") {
      return draft.identity.oidc_issuer.trim() && draft.identity.policy_aud.trim();
    }
    if (step === 4 && draft.directory.provider === "file") {
      return draft.directory.file_content.trim().length > 10;
    }
    return true;
  }, [step, draft]);

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full bg-[#F9FAFB]">
        <Loader2 className="w-6 h-6 animate-spin text-slate-400" />
      </div>
    );
  }

  const StepIcon = STEPS[step].icon;

  return (
    <div className="h-full overflow-y-auto bg-[#F9FAFB]">
      <div className="max-w-3xl mx-auto px-6 py-8 space-y-6">
        <header className="space-y-2">
          <p className="text-xs font-bold uppercase tracking-widest text-[var(--brand)]">
            Platform setup
          </p>
          <h1 className="text-2xl font-bold text-slate-900">Configure {branding.app_name}</h1>
          <p className="text-sm text-slate-500 max-w-xl">
            A guided first-run setup for personal use or your company. When you finish, you will
            get copy-paste restart commands and a readiness check before opening Workspace.
          </p>
        </header>

        {/* Stepper */}
        <div className="flex flex-wrap gap-2">
          {STEPS.map((s, i) => (
            <button
              key={s.id}
              type="button"
              onClick={() => i < step && goToStep(i)}
              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-[11px] font-semibold border transition-colors ${
                i === step
                  ? "bg-[var(--brand)] text-white border-[var(--brand)]"
                  : i < step
                  ? "bg-white text-slate-600 border-slate-200 hover:border-slate-300"
                  : "bg-slate-50 text-slate-400 border-slate-100"
              }`}
            >
              {i < step ? <Check className="w-3 h-3" /> : null}
              {s.title}
            </button>
          ))}
        </div>

        <div className="bg-white border border-slate-200 rounded-2xl shadow-sm overflow-hidden">
          <div className="flex items-center gap-2 px-5 py-4 border-b border-slate-100 bg-slate-50/80">
            <StepIcon className="w-4 h-4 text-[var(--brand)]" />
            <h2 className="text-sm font-bold text-slate-800">{STEPS[step].title}</h2>
            {saving && <Loader2 className="w-3.5 h-3.5 animate-spin text-slate-400 ml-auto" />}
          </div>

          <div className="p-5 space-y-4 text-sm">
            {step === 0 && (
              <>
                <p className="text-slate-600">Who is this deployment for?</p>
                <div className="grid sm:grid-cols-2 gap-3">
                  {(
                    [
                      {
                        mode: "personal" as const,
                        title: "Personal / solo",
                        desc: "Mock auth, local providers. Best for trying the product on your laptop.",
                      },
                      {
                        mode: "team" as const,
                        title: "Team / company",
                        desc: "Real SSO (OIDC), production posture. For cloud or on-prem deployments.",
                      },
                    ] as const
                  ).map(opt => (
                    <button
                      key={opt.mode}
                      type="button"
                      onClick={() =>
                        updateDraft({
                          deployment_mode: opt.mode,
                          core: {
                            ...draft.core,
                            app_env: opt.mode === "personal" ? "development" : "production",
                          },
                        })
                      }
                      className={`text-left p-4 rounded-xl border-2 transition-all ${
                        draft.deployment_mode === opt.mode
                          ? "border-[var(--brand)] bg-[var(--brand-soft)]"
                          : "border-slate-200 hover:border-slate-300"
                      }`}
                    >
                      <p className="font-bold text-slate-900">{opt.title}</p>
                      <p className="text-xs text-slate-500 mt-1 leading-relaxed">{opt.desc}</p>
                    </button>
                  ))}
                </div>
              </>
            )}

            {step === 1 && (
              <div className="grid gap-3">
                <Field label="App name" value={draft.branding.app_name} onChange={v => updateDraft({ branding: { ...draft.branding, app_name: v, app_short_name: v } })} />
                <Field label="Logo URL" value={draft.branding.app_logo_url} onChange={v => updateDraft({ branding: { ...draft.branding, app_logo_url: v } })} />
                <Field label="Org noun (prompts)" value={draft.branding.org_noun} onChange={v => updateDraft({ branding: { ...draft.branding, org_noun: v } })} placeholder="employee" />
                <Field label="Brand color" value={draft.branding.brand_color} onChange={v => updateDraft({ branding: { ...draft.branding, brand_color: v } })} />
                <Field label="Public URL" value={draft.core.app_base_url} onChange={v => updateDraft({ core: { ...draft.core, app_base_url: v } })} />
              </div>
            )}

            {step === 2 && (
              <div className="space-y-3">
                <Field label="Model gateway URL" value={draft.models.gateway_url} onChange={v => updateDraft({ models: { ...draft.models, gateway_url: v } })} />
                <Field label="Model gateway API key (optional for LM Studio / Ollama)" value={draft.models.gateway_api_key} onChange={v => updateDraft({ models: { ...draft.models, gateway_api_key: v } })} secret />
                <p className="text-[11px] text-slate-400 -mt-1">
                  Leave blank when your local gateway does not require authentication (e.g. http://127.0.0.1:1234).
                </p>
                <div>
                  <div className="flex items-center justify-between mb-1">
                    <label className="text-xs font-semibold text-slate-600">Secret encryption key</label>
                    <button type="button" onClick={generateKey} className="text-[11px] font-semibold text-[var(--brand)] hover:underline">
                      Generate
                    </button>
                  </div>
                  <input
                    type="password"
                    value={draft.models.encryption_key}
                    onChange={e => updateDraft({ models: { ...draft.models, encryption_key: e.target.value } })}
                    className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg font-mono"
                  />
                  <p className="text-[11px] text-slate-400 mt-1">Back this up — losing it loses all stored credentials.</p>
                </div>
                <button type="button" onClick={testModels} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold">
                  <RefreshCw className="w-3.5 h-3.5" /> Test gateway
                </button>
                {modelTest && (
                  <p className={`text-xs ${modelTest.ok ? "text-emerald-700" : "text-red-600"}`}>{modelTest.message}</p>
                )}
              </div>
            )}

            {step === 3 && draft.deployment_mode === "personal" && (
              <div className="space-y-3">
                <p className="text-slate-600">Mock authentication stays enabled for solo use.</p>
                <Field label="Mock user email" value={draft.identity.mock_user_email} onChange={v => updateDraft({ identity: { ...draft.identity, mock_user_email: v } })} />
                <Field label="Mock user groups" value={draft.identity.mock_user_groups} onChange={v => updateDraft({ identity: { ...draft.identity, mock_user_groups: v } })} />
              </div>
            )}

            {step === 3 && draft.deployment_mode === "team" && (
              <div className="space-y-3">
                <Field label="OIDC issuer" value={draft.identity.oidc_issuer} onChange={v => updateDraft({ identity: { ...draft.identity, oidc_issuer: v } })} />
                <Field label="Policy audience (POLICY_AUD)" value={draft.identity.policy_aud} onChange={v => updateDraft({ identity: { ...draft.identity, policy_aud: v } })} />
                <Field label="JWKS URL (optional)" value={draft.identity.oidc_jwks_url} onChange={v => updateDraft({ identity: { ...draft.identity, oidc_jwks_url: v } })} placeholder="Auto-discovered when empty" />
                <Field label="Admin groups" value={draft.identity.admin_groups} onChange={v => updateDraft({ identity: { ...draft.identity, admin_groups: v } })} />
                <Field label="Member groups" value={draft.identity.member_groups} onChange={v => updateDraft({ identity: { ...draft.identity, member_groups: v } })} />
                <button type="button" onClick={testOidc} className="inline-flex items-center gap-2 px-3 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold">
                  <RefreshCw className="w-3.5 h-3.5" /> Test OIDC / JWKS
                </button>
                {oidcTest && (
                  <p className={`text-xs ${oidcTest.ok ? "text-emerald-700" : "text-red-600"}`}>{oidcTest.message}</p>
                )}
              </div>
            )}

            {step === 4 && (
              <div className="space-y-3">
                <p className="text-slate-600">How should access approvals resolve people?</p>
                <div className="flex gap-2">
                  {(["claims", "file"] as const).map(p => (
                    <button
                      key={p}
                      type="button"
                      onClick={() => updateDraft({ directory: { ...draft.directory, provider: p } })}
                      className={`px-3 py-2 rounded-lg text-xs font-semibold border ${
                        draft.directory.provider === p
                          ? "bg-[var(--brand)] text-white border-[var(--brand)]"
                          : "border-slate-200 text-slate-600"
                      }`}
                    >
                      {p === "claims" ? "From JWT (default)" : "YAML file"}
                    </button>
                  ))}
                </div>
                {draft.directory.provider === "file" && (
                  <textarea
                    rows={10}
                    value={draft.directory.file_content}
                    onChange={e => updateDraft({ directory: { ...draft.directory, file_content: e.target.value } })}
                    placeholder="Paste directory YAML (see data/directory.example.yaml)"
                    className="w-full text-xs font-mono px-3 py-2 border border-slate-200 rounded-lg"
                  />
                )}
              </div>
            )}

            {step === 5 && (
              <div className="space-y-3">
                <p className="text-slate-600">Slack is optional. You can register agents manually later.</p>
                <div className="flex flex-wrap gap-2">
                  {(["skip", "manual", "auto_create"] as const).map(m => (
                    <button
                      key={m}
                      type="button"
                      onClick={() => updateDraft({ slack: { ...draft.slack, mode: m } })}
                      className={`px-3 py-2 rounded-lg text-xs font-semibold border ${
                        draft.slack.mode === m
                          ? "bg-[var(--brand)] text-white border-[var(--brand)]"
                          : "border-slate-200 text-slate-600"
                      }`}
                    >
                      {m === "skip" ? "Skip" : m === "manual" ? "Manual apps" : "Auto-create"}
                    </button>
                  ))}
                </div>
                {draft.slack.mode === "auto_create" && (
                  <div className="grid gap-3 pt-2">
                    <Field label="Config collaborator (Slack username)" value={draft.slack.config_collaborator} onChange={v => updateDraft({ slack: { ...draft.slack, config_collaborator: v } })} />
                    <Field label="App config access token" value={draft.slack.config_access_token} onChange={v => updateDraft({ slack: { ...draft.slack, config_access_token: v } })} secret />
                    <Field label="App config refresh token" value={draft.slack.config_refresh_token} onChange={v => updateDraft({ slack: { ...draft.slack, config_refresh_token: v } })} secret />
                    <Field label="IP allowlist (CIDRs, comma-separated)" value={draft.slack.ip_allowlist} onChange={v => updateDraft({ slack: { ...draft.slack, ip_allowlist: v } })} />
                  </div>
                )}
              </div>
            )}

            {step === 6 && !completeResult && (
              <div className="space-y-3 text-slate-700">
                <SummaryRow label="Mode" value={draft.deployment_mode} />
                <SummaryRow label="App name" value={draft.branding.app_name} />
                <SummaryRow label="Gateway" value={draft.models.gateway_url} />
                <SummaryRow label="Auth" value={draft.deployment_mode === "personal" ? "Mock (dev)" : "OIDC"} />
                <SummaryRow label="Directory" value={draft.directory.provider} />
                <SummaryRow label="Slack" value={draft.slack.mode} />
                {error && <p className="text-sm text-red-600">{error}</p>}
              </div>
            )}

            {step === 6 && completeResult && (
              <SetupCompletePanel result={completeResult} onNavigate={navigate} />
            )}
          </div>

          {!completeResult && (
            <div className="flex items-center justify-between px-5 py-4 border-t border-slate-100 bg-slate-50/50">
              <button
                type="button"
                disabled={step === 0}
                onClick={() => goToStep(s => Math.max(0, s - 1))}
                className="inline-flex items-center gap-1.5 px-3 py-2 text-xs font-semibold text-slate-600 disabled:opacity-40"
              >
                <ArrowLeft className="w-3.5 h-3.5" /> Back
              </button>
              {step < STEPS.length - 1 ? (
                <button
                  type="button"
                  disabled={!canNext}
                  onClick={() => goToStep(s => Math.min(STEPS.length - 1, s + 1))}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--brand)] text-white text-xs font-semibold disabled:opacity-40"
                >
                  Continue <ArrowRight className="w-3.5 h-3.5" />
                </button>
              ) : (
                <button
                  type="button"
                  disabled={completing}
                  onClick={complete}
                  className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-slate-900 text-white text-xs font-semibold disabled:opacity-40"
                >
                  {completing ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Finish setup
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

interface SetupCompleteResult {
  env_path?: string;
  directory_path?: string | null;
  deployment_mode?: string;
  runtime_environment?: string;
  applied_immediately?: string[];
  requires_restart_for?: string[];
  restart_commands?: {
    primary?: string;
    backend?: string;
    frontend?: string;
    note?: string;
  };
  next_steps?: { id: string; title: string; description: string }[];
}

function SetupCompletePanel({
  result,
  onNavigate,
}: {
  result: SetupCompleteResult;
  onNavigate: (path: string) => void;
}) {
  const [verifying, setVerifying] = useState(false);
  const [verifyState, setVerifyState] = useState<{
    ready: boolean;
    model_count?: number;
    error?: string;
    checks?: Record<string, { status: string; label: string; detail: string }>;
  } | null>(null);
  const [copiedKey, setCopiedKey] = useState<string | null>(null);

  const copyText = async (key: string, text: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setCopiedKey(key);
      window.setTimeout(() => setCopiedKey(null), 2000);
    } catch {
      /* clipboard unavailable */
    }
  };

  const verifyRestart = async () => {
    setVerifying(true);
    setVerifyState(null);
    try {
      const res = await apiFetch("/api/admin/setup/verify", { method: "POST" });
      const data = await res.json();
      setVerifyState({
        ready: Boolean(data.ready),
        model_count: data.model_count,
        checks: data.checks as Record<string, { status: string; label: string; detail: string }> | undefined,
        error: data.models_error || (!data.ready ? "Backend is not fully ready yet." : undefined),
      });
    } catch (e) {
      setVerifyState({
        ready: false,
        error: e instanceof Error ? e.message : "Could not reach the backend.",
      });
    } finally {
      setVerifying(false);
    }
  };

  const commands = result.restart_commands ?? {};
  const isDocker = result.runtime_environment === "docker";

  return (
    <div className="space-y-5 py-2">
      <div className="text-center space-y-2">
        <CheckCircle2 className="w-12 h-12 text-emerald-500 mx-auto" />
        <p className="font-bold text-slate-900 text-lg">Setup complete</p>
        <p className="text-sm text-slate-600 max-w-lg mx-auto">
          Your configuration is saved. Finish the steps below so chat, models, and auth work.
        </p>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-emerald-100 bg-emerald-50/50 p-4 space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-emerald-800">Applied now</p>
          <ul className="space-y-1.5">
            {(result.applied_immediately ?? ["Branding", "Setup flag"]).map(item => (
              <li key={item} className="flex items-start gap-2 text-sm text-emerald-900">
                <Check className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
        <div className="rounded-xl border border-amber-100 bg-amber-50/50 p-4 space-y-2">
          <p className="text-xs font-bold uppercase tracking-wide text-amber-900">Needs backend restart</p>
          <ul className="space-y-1.5">
            {(result.requires_restart_for ?? ["Model gateway", "Encryption key", "Auth"]).map(item => (
              <li key={item} className="flex items-start gap-2 text-sm text-amber-950">
                <RefreshCw className="w-4 h-4 shrink-0 mt-0.5" />
                <span>{item}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>

      {result.env_path && (
        <p className="text-xs text-slate-500 text-center">
          Saved to <code className="bg-slate-100 px-1 rounded">{result.env_path}</code>
          {result.directory_path ? (
            <>
              {" "}
              and <code className="bg-slate-100 px-1 rounded">{result.directory_path}</code>
            </>
          ) : null}
        </p>
      )}

      <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 space-y-3">
        <p className="text-sm font-bold text-slate-800">
          {isDocker ? "Restart the backend container" : "Restart the backend process"}
        </p>
        {commands.note && <p className="text-xs text-slate-600">{commands.note}</p>}
        <div className="space-y-2">
          {isDocker && commands.primary && (
            <CommandBlock
              label="Docker Compose"
              command={commands.primary}
              copied={copiedKey === "docker"}
              onCopy={() => copyText("docker", commands.primary!)}
            />
          )}
          {!isDocker && commands.backend && (
            <CommandBlock
              label="Backend (terminal 1)"
              command={commands.backend}
              copied={copiedKey === "backend"}
              onCopy={() => copyText("backend", commands.backend!)}
            />
          )}
          {!isDocker && commands.frontend && (
            <CommandBlock
              label="Frontend (terminal 2, if not already running)"
              command={commands.frontend}
              copied={copiedKey === "frontend"}
              onCopy={() => copyText("frontend", commands.frontend!)}
            />
          )}
        </div>
      </div>

      {(result.next_steps ?? []).length > 0 && (
        <ol className="space-y-3">
          {result.next_steps!.map((step, index) => (
            <li key={step.id} className="flex gap-3 text-sm">
              <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-200 text-xs font-bold text-slate-700">
                {index + 1}
              </span>
              <div>
                <p className="font-semibold text-slate-900">{step.title}</p>
                <p className="text-slate-600 mt-0.5">{step.description}</p>
              </div>
            </li>
          ))}
        </ol>
      )}

      <div className="rounded-xl border border-slate-200 p-4 space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm font-semibold text-slate-800">After restarting, check readiness</p>
          <button
            type="button"
            onClick={verifyRestart}
            disabled={verifying}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white text-xs font-semibold text-slate-700 hover:bg-slate-50 disabled:opacity-50"
          >
            {verifying ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <RefreshCw className="w-3.5 h-3.5" />
            )}
            Verify backend
          </button>
        </div>
        {verifyState && (
          <div className="space-y-3">
            <p
              className={`text-sm ${
                verifyState.ready ? "text-emerald-700" : "text-amber-800"
              }`}
            >
              {verifyState.ready
                ? `Ready — model gateway is reachable (${verifyState.model_count ?? 0} models).`
                : verifyState.error ?? "Not ready yet. Restart the backend and try again."}
            </p>
            {verifyState.checks && (
              <ul className="grid gap-2 sm:grid-cols-2">
                {Object.entries(verifyState.checks).map(([key, check]) => (
                  <li
                    key={key}
                    className={`rounded-lg border px-3 py-2 text-xs ${
                      check.status === "ok"
                        ? "border-emerald-200 bg-emerald-50/60 text-emerald-900"
                        : check.status === "skip"
                          ? "border-slate-200 bg-slate-50 text-slate-600"
                          : check.status === "warn"
                            ? "border-amber-200 bg-amber-50/60 text-amber-900"
                            : "border-red-200 bg-red-50/60 text-red-900"
                    }`}
                  >
                    <p className="font-semibold">{check.label}</p>
                    <p className="mt-0.5 opacity-90">{check.detail}</p>
                  </li>
                ))}
              </ul>
            )}
          </div>
        )}
      </div>

      <div className="flex flex-wrap items-center justify-center gap-2 pt-1">
        <button
          type="button"
          onClick={() => onNavigate("/workspace")}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg bg-[var(--brand)] text-white text-sm font-semibold"
        >
          <MessageSquare className="w-4 h-4" />
          Open Workspace
        </button>
        <button
          type="button"
          onClick={() => onNavigate("/")}
          className="inline-flex items-center gap-1.5 px-4 py-2 rounded-lg border border-slate-200 bg-white text-sm font-semibold text-slate-700 hover:bg-slate-50"
        >
          <ExternalLink className="w-4 h-4" />
          Go to home
        </button>
      </div>
    </div>
  );
}

function CommandBlock({
  label,
  command,
  copied,
  onCopy,
}: {
  label: string;
  command: string;
  copied: boolean;
  onCopy: () => void;
}) {
  return (
    <div>
      <p className="text-[11px] font-semibold text-slate-500 mb-1">{label}</p>
      <div className="flex items-start gap-2 rounded-lg border border-slate-200 bg-white p-2">
        <code className="flex-1 text-xs text-slate-800 whitespace-pre-wrap break-all">{command}</code>
        <button
          type="button"
          onClick={onCopy}
          className="shrink-0 inline-flex items-center gap-1 px-2 py-1 rounded text-[11px] font-semibold text-slate-600 hover:bg-slate-100"
          title="Copy command"
        >
          <ClipboardCopy className="w-3.5 h-3.5" />
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  secret,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  secret?: boolean;
  placeholder?: string;
}) {
  return (
    <div>
      <label className="block text-xs font-semibold text-slate-600 mb-1">{label}</label>
      <input
        type={secret ? "password" : "text"}
        value={value}
        placeholder={placeholder}
        onChange={e => onChange(e.target.value)}
        className="w-full text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:border-slate-400"
      />
    </div>
  );
}

function SummaryRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between gap-4 text-sm border-b border-slate-100 pb-2">
      <span className="text-slate-500">{label}</span>
      <span className="font-medium text-slate-800 text-right truncate">{value}</span>
    </div>
  );
}
