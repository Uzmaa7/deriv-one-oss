import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, FlaskConical, Loader2, Sparkles, Wand2, X } from "lucide-react";
import {
  acceptSuggestion,
  dismissRecommendation,
  editArtifactDraft,
  generateFromRecommendation,
  getArtifactPlan,
  listArtifactRecommendations,
  pollArtifactTest,
  pollSuggestJob,
  startArtifactPlan,
  startArtifactTest,
  startSuggestJob,
  type ArtifactRecommendation,
  type ArtifactSummary,
  type ArtifactTestAttempt,
} from "@/lib/skillArtifactsApi";

const MAX_TEST_ATTEMPTS = 3;

function statusLabel(status: string): string {
  switch (status) {
    case "proposed":
      return "Suggested";
    case "generating":
      return "Generating…";
    case "draft_ready":
      return "Ready to review";
    case "published":
      return "Published";
    case "failed":
      return "Failed";
    case "dismissed":
      return "Dismissed";
    default:
      return status;
  }
}

type JsonSchemaProperty = {
  type?: string;
  pattern?: string;
  minLength?: number;
  maxLength?: number;
  enum?: string[];
  description?: string;
};

function ParamsTestForm({
  schema,
  values,
  onChange,
  disabled,
}: {
  schema: Record<string, unknown> | null | undefined;
  values: Record<string, unknown>;
  onChange: (next: Record<string, unknown>) => void;
  disabled?: boolean;
}) {
  const properties = (schema?.properties as Record<string, JsonSchemaProperty>) ?? {};
  const required = (schema?.required as string[]) ?? [];
  const keys = Object.keys(properties);

  if (keys.length === 0) {
    return (
      <p className="text-xs text-slate-500">
        No parameters required. Click Run test to execute with empty params.
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {keys.map(key => {
        const prop = properties[key] ?? {};
        const label = (
          <label className="block text-xs font-semibold text-slate-700 mb-1">
            {key}
            {required.includes(key) && <span className="text-red-500 ml-0.5">*</span>}
          </label>
        );

        if (prop.type === "boolean") {
          return (
            <div key={key}>
              {label}
              <input
                type="checkbox"
                checked={Boolean(values[key])}
                disabled={disabled}
                onChange={e => onChange({ ...values, [key]: e.target.checked })}
                className="rounded border-slate-300"
              />
            </div>
          );
        }

        if (prop.type === "integer") {
          return (
            <div key={key}>
              {label}
              <input
                type="number"
                step={1}
                value={values[key] !== undefined ? String(values[key]) : ""}
                disabled={disabled}
                onChange={e =>
                  onChange({
                    ...values,
                    [key]: e.target.value === "" ? undefined : parseInt(e.target.value, 10),
                  })
                }
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2"
              />
            </div>
          );
        }

        if (prop.type === "object") {
          return (
            <div key={key}>
              {label}
              <textarea
                rows={3}
                value={
                  typeof values[key] === "string"
                    ? (values[key] as string)
                    : JSON.stringify(values[key] ?? {}, null, 2)
                }
                disabled={disabled}
                onChange={e => {
                  try {
                    onChange({ ...values, [key]: JSON.parse(e.target.value) });
                  } catch {
                    onChange({ ...values, [key]: e.target.value });
                  }
                }}
                className="w-full text-xs font-mono border border-slate-200 rounded-lg px-3 py-2"
              />
            </div>
          );
        }

        if (prop.enum && prop.enum.length > 0) {
          return (
            <div key={key}>
              {label}
              <select
                value={String(values[key] ?? "")}
                disabled={disabled}
                onChange={e => onChange({ ...values, [key]: e.target.value })}
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2"
              >
                <option value="">Select…</option>
                {prop.enum.map(opt => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            </div>
          );
        }

        return (
          <div key={key}>
            {label}
            <input
              type="text"
              value={String(values[key] ?? "")}
              disabled={disabled}
              onChange={e => onChange({ ...values, [key]: e.target.value })}
              pattern={prop.pattern}
              minLength={prop.minLength}
              maxLength={prop.maxLength}
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2"
            />
            {prop.description && (
              <p className="text-[10px] text-slate-400 mt-0.5">{prop.description}</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function AttemptTimeline({ attempts }: { attempts: ArtifactTestAttempt[] }) {
  if (attempts.length === 0) return null;

  return (
    <ul className="space-y-2 text-xs">
      {attempts.map((a, i) => (
        <li key={`${a.attempt}-${i}`} className="border border-slate-200 rounded-lg p-2 bg-slate-50">
          <p className="font-semibold text-slate-800">
            Attempt {a.attempt}/{MAX_TEST_ATTEMPTS}: {a.status}
            {a.regenerated && " (regenerated)"}
          </p>
          {a.error && <p className="text-red-600 mt-1 font-mono whitespace-pre-wrap">{a.error}</p>}
          {a.output_excerpt && (
            <pre className="text-slate-600 mt-1 font-mono whitespace-pre-wrap max-h-32 overflow-auto">
              {a.output_excerpt}
            </pre>
          )}
        </li>
      ))}
    </ul>
  );
}

export function SkillArtifactCreateModal({
  slug,
  initialReviewJobId,
  onClose,
  onCreated,
}: {
  slug: string;
  initialReviewJobId?: string | null;
  onClose: () => void;
  onCreated: (artifact: ArtifactSummary) => void;
}) {
  const [recommendations, setRecommendations] = useState<ArtifactRecommendation[]>([]);
  const [planRunning, setPlanRunning] = useState(false);
  const [planDetail, setPlanDetail] = useState<string | null>(null);
  const [activePlanId, setActivePlanId] = useState<string | null>(null);
  const [reviewRecId, setReviewRecId] = useState<string | null>(null);
  const [reviewJobId, setReviewJobId] = useState<string | null>(null);
  const [reviewCode, setReviewCode] = useState<string | null>(null);
  const [reviewManifest, setReviewManifest] = useState<Record<string, unknown> | null>(null);
  const [reviewStatus, setReviewStatus] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [nameHint, setNameHint] = useState("");
  const [descriptionHint, setDescriptionHint] = useState("");
  const [customJobId, setCustomJobId] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editBusy, setEditBusy] = useState(false);

  const [showTestPanel, setShowTestPanel] = useState(false);
  const [testParams, setTestParams] = useState<Record<string, unknown>>({});
  const [autoFix, setAutoFix] = useState(true);
  const [testRunId, setTestRunId] = useState<string | null>(null);
  const [testStatus, setTestStatus] = useState<string | null>(null);
  const [testAttempts, setTestAttempts] = useState<ArtifactTestAttempt[]>([]);
  const [testPassed, setTestPassed] = useState(false);
  const [testWarning, setTestWarning] = useState(false);

  const activeJobId = reviewJobId ?? customJobId;

  const paramsSchema = useMemo(() => {
    const schema = reviewManifest?.params_schema;
    if (schema && typeof schema === "object") {
      return schema as Record<string, unknown>;
    }
    return null;
  }, [reviewManifest]);

  const refreshList = useCallback(async () => {
    const data = await listArtifactRecommendations(slug);
    setRecommendations(data.recommendations);
    if (data.runningPlan) {
      setPlanRunning(true);
      setActivePlanId(data.runningPlan.id);
      setPlanDetail(data.runningPlan.status_detail ?? data.runningPlan.status);
    } else {
      setPlanRunning(false);
      setPlanDetail(null);
    }
    return data;
  }, [slug]);

  function resetTestState() {
    setShowTestPanel(false);
    setTestParams({});
    setAutoFix(true);
    setTestRunId(null);
    setTestStatus(null);
    setTestAttempts([]);
    setTestPassed(false);
    setTestWarning(false);
  }

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        await refreshList();
      } catch (e: unknown) {
        if (!cancelled) setError(e instanceof Error ? e.message : "Could not load suggestions");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [refreshList]);

  useEffect(() => {
    if (!initialReviewJobId) return;
    setReviewJobId(initialReviewJobId);
    setReviewRecId(null);
    setCustomJobId(null);
    setReviewCode(null);
    setReviewManifest(null);
    setReviewStatus("pending");
    setError(null);
    resetTestState();
  }, [initialReviewJobId]);

  useEffect(() => {
    if (!activePlanId || !planRunning) return;
    const t = setInterval(async () => {
      try {
        const data = await getArtifactPlan(slug, activePlanId);
        const st = data.plan.status;
        setPlanDetail(data.plan.status_detail ?? st);
        if (st === "done" || st === "failed") {
          setPlanRunning(false);
          setRecommendations(data.recommendations.filter(r => r.status !== "dismissed"));
          if (st === "failed") {
            setError(data.plan.error_message ?? "Analysis failed");
          }
          setActivePlanId(null);
        }
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [slug, activePlanId, planRunning]);

  useEffect(() => {
    const jobId = activeJobId;
    if (!jobId) return;
    if (reviewStatus === "done" || reviewStatus === "failed") return;
    const t = setInterval(async () => {
      try {
        const data = await pollSuggestJob(slug, jobId);
        const st = data.status as string;
        setReviewStatus(st);
        if (st === "done") {
          setReviewCode((data.proposed_code as string) ?? null);
          setReviewManifest((data.proposed_manifest as Record<string, unknown>) ?? null);
        }
        if (st === "failed") {
          setError((data.error_message as string) ?? (data.status_detail as string) ?? "Generation failed");
        }
        await refreshList();
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [slug, activeJobId, reviewStatus, refreshList]);

  useEffect(() => {
    if (!testRunId || !activeJobId) return;
    if (testStatus === "passed" || testStatus === "failed" || testStatus === "error") return;
    const t = setInterval(async () => {
      try {
        const data = await pollArtifactTest(slug, activeJobId, testRunId);
        setTestStatus(data.status);
        setTestAttempts(data.attempts);
        if (data.status === "passed") {
          setTestPassed(true);
          setTestWarning(false);
          if (data.final_code) setReviewCode(data.final_code);
          if (data.final_manifest) setReviewManifest(data.final_manifest);
        }
        if (data.status === "failed" || data.status === "error") {
          setTestPassed(false);
          setTestWarning(true);
          if (data.final_code) setReviewCode(data.final_code);
          if (data.final_manifest) setReviewManifest(data.final_manifest);
        }
      } catch {
        /* ignore */
      }
    }, 2000);
    return () => clearInterval(t);
  }, [slug, activeJobId, testRunId, testStatus]);

  async function runAnalyze() {
    setBusy(true);
    setError(null);
    try {
      const planId = await startArtifactPlan(slug);
      setActivePlanId(planId);
      setPlanRunning(true);
      setPlanDetail("Queued");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Analyze failed");
    } finally {
      setBusy(false);
    }
  }

  async function generateRec(rec: ArtifactRecommendation) {
    setBusy(true);
    setError(null);
    setReviewRecId(rec.id);
    setReviewCode(null);
    setReviewManifest(null);
    setReviewStatus("pending");
    resetTestState();
    try {
      const jobId = await generateFromRecommendation(slug, rec.id);
      setReviewJobId(jobId);
      await refreshList();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Generate failed");
      setReviewRecId(null);
    } finally {
      setBusy(false);
    }
  }

  async function openReview(rec: ArtifactRecommendation) {
    if (!rec.suggest_job_id) return;
    setReviewRecId(rec.id);
    setReviewJobId(rec.suggest_job_id);
    setReviewCode(null);
    setReviewManifest(null);
    setReviewStatus(null);
    setError(null);
    resetTestState();
    try {
      const data = await pollSuggestJob(slug, rec.suggest_job_id);
      const st = data.status as string;
      setReviewStatus(st);
      if (st === "done") {
        setReviewCode((data.proposed_code as string) ?? null);
        setReviewManifest((data.proposed_manifest as Record<string, unknown>) ?? null);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Could not load draft");
    }
  }

  async function dismissRec(rec: ArtifactRecommendation) {
    setBusy(true);
    setError(null);
    try {
      await dismissRecommendation(slug, rec.id);
      if (reviewRecId === rec.id) {
        setReviewRecId(null);
        setReviewJobId(null);
        setReviewCode(null);
        setReviewManifest(null);
        resetTestState();
      }
      await refreshList();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Dismiss failed");
    } finally {
      setBusy(false);
    }
  }

  async function acceptReview() {
    const jobId = activeJobId;
    if (!jobId) return;
    setBusy(true);
    setError(null);
    try {
      const artifact = await acceptSuggestion(slug, jobId);
      onCreated(artifact);
      setReviewRecId(null);
      setReviewJobId(null);
      setReviewCode(null);
      setReviewManifest(null);
      setCustomJobId(null);
      resetTestState();
      await refreshList();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Accept failed");
    } finally {
      setBusy(false);
    }
  }

  async function runCustomSuggest() {
    setBusy(true);
    setError(null);
    setCustomJobId(null);
    setReviewCode(null);
    setReviewManifest(null);
    resetTestState();
    try {
      const id = await startSuggestJob(slug, nameHint, descriptionHint);
      setCustomJobId(id);
      setReviewStatus("pending");
      setReviewRecId(null);
      setReviewJobId(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Suggest failed");
    } finally {
      setBusy(false);
    }
  }

  async function runTest() {
    const jobId = activeJobId;
    if (!jobId) return;
    setBusy(true);
    setError(null);
    setTestPassed(false);
    setTestWarning(false);
    setTestAttempts([]);
    setTestStatus("pending");
    try {
      const id = await startArtifactTest(slug, jobId, testParams, autoFix);
      setTestRunId(id);
      setTestStatus("running");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "Test failed to start");
      setTestStatus(null);
    } finally {
      setBusy(false);
    }
  }

  async function applyAiEdit() {
    const jobId = activeJobId;
    const prompt = editPrompt.trim();
    if (!jobId || !prompt) return;
    setEditBusy(true);
    setError(null);
    resetTestState();
    setReviewStatus("pending");
    try {
      await editArtifactDraft(slug, jobId, prompt);
      setEditPrompt("");
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "AI edit failed");
      setReviewStatus("done");
    } finally {
      setEditBusy(false);
    }
  }

  const showCode = reviewCode !== null;
  const jobRunning =
    reviewStatus === "pending" || reviewStatus === "running" || editBusy;
  const canAccept = showCode && activeJobId;
  const testRunning = testStatus === "pending" || testStatus === "running";

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col border border-slate-200">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
          <div>
            <h2 className="text-lg font-bold text-slate-900">Create artifact</h2>
            <p className="text-xs text-slate-500 mt-0.5">
              Analyze the full skill for suggested artifacts, then generate and publish each one individually.
            </p>
          </div>
          <button type="button" onClick={onClose} className="p-2 text-slate-400 hover:text-slate-700 rounded-lg">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4 overflow-y-auto flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              disabled={busy || planRunning}
              onClick={runAnalyze}
              className="inline-flex items-center gap-2 text-sm font-semibold px-4 py-2 rounded-lg bg-slate-900 text-white disabled:opacity-50"
            >
              {planRunning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Sparkles className="w-4 h-4" />}
              {planRunning ? "Analyzing skill…" : "Analyze skill for suggestions"}
            </button>
            {planRunning && planDetail && (
              <span className="text-xs text-slate-500">{planDetail}</span>
            )}
          </div>

          {recommendations.length > 0 && (
            <ul className="space-y-2">
              {recommendations.map(rec => (
                <li
                  key={rec.id}
                  className={`border rounded-xl p-3 ${
                    reviewRecId === rec.id ? "border-slate-400 bg-slate-50" : "border-slate-200"
                  }`}
                >
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <p className="text-sm font-semibold text-slate-900 font-mono">{rec.name}</p>
                      <p className="text-xs text-slate-600 mt-0.5">{rec.description}</p>
                      <p className="text-[10px] text-slate-400 mt-1 uppercase tracking-wide">
                        {rec.effect_class} · {statusLabel(rec.status)}
                      </p>
                      {rec.error_message && rec.status === "failed" && (
                        <p className="text-xs text-red-600 mt-1">{rec.error_message}</p>
                      )}
                    </div>
                    <div className="flex flex-wrap gap-1 shrink-0">
                      {rec.status === "published" && (
                        <span className="inline-flex items-center gap-1 text-xs text-emerald-700 font-semibold px-2 py-1">
                          <Check className="w-3 h-3" /> Published
                        </span>
                      )}
                      {(rec.status === "proposed" || rec.status === "failed") && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => generateRec(rec)}
                          className="text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-900 text-white disabled:opacity-50"
                        >
                          Generate
                        </button>
                      )}
                      {rec.status === "draft_ready" && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => openReview(rec)}
                          className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-slate-300 text-slate-800"
                        >
                          Review
                        </button>
                      )}
                      {rec.status !== "published" && rec.status !== "dismissed" && (
                        <button
                          type="button"
                          disabled={busy}
                          onClick={() => dismissRec(rec)}
                          className="text-xs font-semibold px-3 py-1.5 rounded-lg text-slate-500 hover:bg-slate-100"
                        >
                          Dismiss
                        </button>
                      )}
                    </div>
                  </div>
                </li>
              ))}
            </ul>
          )}

          {recommendations.length === 0 && !planRunning && (
            <p className="text-sm text-slate-500">
              No saved suggestions yet. Run analyze to let AI read the full skill and propose artifacts.
            </p>
          )}

          {showCode && (
            <div className="space-y-2">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Generated Python</p>
                <button
                  type="button"
                  disabled={busy || testRunning || jobRunning}
                  onClick={() => setShowTestPanel(v => !v)}
                  className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg border border-slate-300 text-slate-800 disabled:opacity-50"
                >
                  <FlaskConical className="w-3.5 h-3.5" />
                  {showTestPanel ? "Hide test" : "Test artifact"}
                </button>
              </div>
              <pre className="text-xs font-mono bg-slate-50 border border-slate-200 rounded-xl p-4 max-h-[40vh] overflow-auto leading-relaxed">
                {reviewCode}
              </pre>

              <div className="border border-slate-200 rounded-xl p-4 space-y-2 bg-white">
                <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Edit with AI</p>
                <textarea
                  value={editPrompt}
                  onChange={e => setEditPrompt(e.target.value)}
                  placeholder="Describe the change you want (e.g. add timeout handling, rename param, fix host allowlist)…"
                  rows={3}
                  maxLength={4000}
                  disabled={busy || testRunning || jobRunning}
                  className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 resize-y min-h-[4rem]"
                />
                <button
                  type="button"
                  disabled={busy || testRunning || jobRunning || !editPrompt.trim()}
                  onClick={applyAiEdit}
                  className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-900 text-white disabled:opacity-50"
                >
                  {jobRunning ? (
                    <>
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                      Applying edit…
                    </>
                  ) : (
                    <>
                      <Wand2 className="w-3.5 h-3.5" />
                      Apply AI edit
                    </>
                  )}
                </button>
              </div>

              {showTestPanel && (
                <div className="border border-slate-200 rounded-xl p-4 space-y-3 bg-slate-50/50">
                  <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Test parameters</p>
                  <ParamsTestForm
                    schema={paramsSchema}
                    values={testParams}
                    onChange={setTestParams}
                    disabled={busy || testRunning}
                  />
                  <label className="flex items-center gap-2 text-xs text-slate-700">
                    <input
                      type="checkbox"
                      checked={autoFix}
                      disabled={busy || testRunning}
                      onChange={e => setAutoFix(e.target.checked)}
                      className="rounded border-slate-300"
                    />
                    Auto-fix on failure (up to {MAX_TEST_ATTEMPTS} attempts)
                  </label>
                  <button
                    type="button"
                    disabled={busy || testRunning}
                    onClick={runTest}
                    className="text-xs font-semibold px-4 py-2 rounded-lg bg-slate-900 text-white disabled:opacity-50"
                  >
                    {testRunning ? (
                      <span className="inline-flex items-center gap-2">
                        <Loader2 className="w-3.5 h-3.5 animate-spin" />
                        Running test…
                      </span>
                    ) : (
                      "Run test"
                    )}
                  </button>

                  {testAttempts.length > 0 && <AttemptTimeline attempts={testAttempts} />}

                  {testPassed && (
                    <p className="text-sm text-emerald-700 font-semibold">
                      Test passed. The code below has been updated to the version that passed.
                    </p>
                  )}
                  {testWarning && (
                    <p className="text-sm text-amber-700 font-semibold">
                      Test did not pass after all attempts. You can still publish, but the artifact may fail at runtime.
                    </p>
                  )}
                </div>
              )}
            </div>
          )}

          {!showCode && activeJobId && jobRunning && (
            <div className="flex items-center gap-2 text-sm text-slate-500 py-4">
              <Loader2 className="w-4 h-4 animate-spin" />
              Generating draft…
            </div>
          )}

          <div className="border-t border-slate-100 pt-4 space-y-2">
            <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400">Custom artifact</p>
            <input
              type="text"
              value={nameHint}
              onChange={e => setNameHint(e.target.value)}
              placeholder="Optional snake_case name"
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2"
            />
            <textarea
              value={descriptionHint}
              onChange={e => setDescriptionHint(e.target.value)}
              placeholder="Describe what this artifact should do (APIs to call, inputs/outputs, behavior)…"
              rows={4}
              maxLength={4000}
              disabled={busy}
              className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 resize-y min-h-[5rem]"
            />
            <p className="text-[10px] text-slate-400 text-right">{descriptionHint.length}/4000</p>
            <button
              type="button"
              disabled={busy}
              onClick={runCustomSuggest}
              className="text-xs font-semibold px-3 py-1.5 rounded-lg border border-slate-300"
            >
              Generate without suggestion
            </button>
          </div>

          {error && <p className="text-sm text-red-600">{error}</p>}
        </div>

        <div className="px-5 py-4 border-t border-slate-100 flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="px-4 py-2 text-sm font-semibold text-slate-600 hover:bg-slate-50 rounded-lg"
          >
            Close
          </button>
          {canAccept && (
            <button
              type="button"
              disabled={busy || jobRunning}
              onClick={acceptReview}
              className="px-4 py-2 text-sm font-semibold rounded-lg bg-[#FF444F] text-white disabled:opacity-50"
            >
              {busy ? "Publishing…" : "Publish artifact"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
