'use strict';

import { apiFetch } from "@/lib/api";

export interface ArtifactSummary {
  id: string;
  name: string;
  version: number;
  effect_class: string;
  status: string;
  author_kind: string;
  disabled: boolean;
  api_summary?: string;
  run_count: number;
}

export interface ArtifactDetail extends ArtifactSummary {
  code: string;
  manifest: Record<string, unknown>;
  declared_hosts: string[];
  declared_secrets: string[];
  params_schema?: Record<string, unknown> | null;
  entrypoint?: string;
}

export async function parseApiJson(res: Response): Promise<Record<string, unknown>> {
  const text = await res.text();
  if (!text.trim()) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    const snippet = text.trim().slice(0, 160);
    throw new Error(res.ok ? "Invalid JSON from server." : snippet || `Request failed (${res.status})`);
  }
}

export function apiErrorDetail(data: Record<string, unknown>, fallback: string): string {
  const detail = data.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && "message" in detail) {
    const msg = (detail as { message?: string }).message;
    if (typeof msg === "string") return msg;
  }
  return fallback;
}

export async function listSkillArtifacts(slug: string): Promise<ArtifactSummary[]> {
  const res = await apiFetch(`/api/skills/${slug}/artifacts`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load artifacts."));
  return (data.artifacts as ArtifactSummary[]) ?? [];
}

export async function getSkillArtifact(
  slug: string,
  name: string,
  version: number,
): Promise<ArtifactDetail> {
  const res = await apiFetch(`/api/skills/${slug}/artifacts/${name}/versions/${version}`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load artifact."));
  return data.artifact as ArtifactDetail;
}

export async function startSuggestJob(
  slug: string,
  nameHint?: string,
  description?: string,
): Promise<string> {
  const form = new FormData();
  if (nameHint?.trim()) form.append("artifact_name_hint", nameHint.trim());
  if (description?.trim()) form.append("artifact_description", description.trim());
  const res = await apiFetch(`/api/skills/${slug}/artifacts/suggest-job`, { method: "POST", body: form });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Suggest failed"));
  return data.job_id as string;
}

export async function pollSuggestJob(slug: string, jobId: string) {
  const res = await apiFetch(`/api/skills/${slug}/artifacts/suggest-job/${jobId}`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Poll failed"));
  return data;
}

export async function acceptSuggestion(slug: string, jobId: string) {
  const res = await apiFetch(`/api/skills/${slug}/artifacts/suggestions/${jobId}/accept`, {
    method: "POST",
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Accept failed"));
  return data.artifact as ArtifactSummary;
}

export interface ArtifactPlanSummary {
  id: string;
  slug: string;
  status: string;
  status_detail?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  completed_at?: string | null;
}

export interface ArtifactRecommendation {
  id: string;
  plan_id: string;
  slug: string;
  name: string;
  description: string;
  effect_class: string;
  sort_order: number;
  status: string;
  suggest_job_id?: string | null;
  published_artifact_id?: string | null;
  error_message?: string | null;
}

export async function listArtifactRecommendations(slug: string) {
  const res = await apiFetch(`/api/skills/${slug}/artifact-recommendations`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load suggestions."));
  return {
    plan: (data.plan as ArtifactPlanSummary | null) ?? null,
    runningPlan: (data.running_plan as ArtifactPlanSummary | null) ?? null,
    recommendations: (data.recommendations as ArtifactRecommendation[]) ?? [],
  };
}

export async function startArtifactPlan(slug: string): Promise<string> {
  const res = await apiFetch(`/api/skills/${slug}/artifact-plans`, { method: "POST" });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Analyze failed"));
  return data.plan_id as string;
}

export async function getArtifactPlan(slug: string, planId: string) {
  const res = await apiFetch(`/api/skills/${slug}/artifact-plans/${planId}`);
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Could not load plan."));
  return {
    plan: data.plan as ArtifactPlanSummary,
    recommendations: (data.recommendations as ArtifactRecommendation[]) ?? [],
  };
}

export async function generateFromRecommendation(slug: string, recId: string): Promise<string> {
  const res = await apiFetch(
    `/api/skills/${slug}/artifact-recommendations/${recId}/generate`,
    { method: "POST" },
  );
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Generate failed"));
  return data.job_id as string;
}

export async function dismissRecommendation(slug: string, recId: string) {
  const res = await apiFetch(
    `/api/skills/${slug}/artifact-recommendations/${recId}/dismiss`,
    { method: "POST" },
  );
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Dismiss failed"));
}

export interface ArtifactTestAttempt {
  attempt: number;
  code_sha256: string;
  status: string;
  output_excerpt?: string;
  error?: string | null;
  regenerated?: boolean;
}

export interface ArtifactTestRun {
  test_run_id: string;
  status: string;
  attempts: ArtifactTestAttempt[];
  final_code?: string | null;
  final_manifest?: Record<string, unknown> | null;
  completed_at?: string | null;
}

export async function startArtifactTest(
  slug: string,
  jobId: string,
  params: Record<string, unknown>,
  autoFix: boolean,
): Promise<string> {
  const res = await apiFetch(`/api/skills/${slug}/artifacts/suggest-job/${jobId}/test-runs`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ params, auto_fix: autoFix }),
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Test run failed"));
  return data.test_run_id as string;
}

export async function pollArtifactTest(
  slug: string,
  jobId: string,
  testRunId: string,
): Promise<ArtifactTestRun> {
  const res = await apiFetch(
    `/api/skills/${slug}/artifacts/suggest-job/${jobId}/test-runs/${testRunId}`,
  );
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "Poll test run failed"));
  return {
    test_run_id: data.test_run_id as string,
    status: data.status as string,
    attempts: (data.attempts as ArtifactTestAttempt[]) ?? [],
    final_code: (data.final_code as string | null) ?? null,
    final_manifest: (data.final_manifest as Record<string, unknown> | null) ?? null,
    completed_at: (data.completed_at as string | null) ?? null,
  };
}

export async function editArtifactDraft(
  slug: string,
  jobId: string,
  prompt: string,
): Promise<string> {
  const res = await apiFetch(`/api/skills/${slug}/artifacts/suggest-job/${jobId}/ai-edit`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ prompt: prompt.trim() }),
  });
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "AI edit failed"));
  return data.job_id as string;
}

export async function editPublishedArtifact(
  slug: string,
  name: string,
  version: number,
  prompt: string,
): Promise<string> {
  const res = await apiFetch(
    `/api/skills/${slug}/artifacts/${name}/versions/${version}/ai-edit`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: prompt.trim() }),
    },
  );
  const data = await parseApiJson(res);
  if (!res.ok) throw new Error(apiErrorDetail(data, "AI edit failed"));
  return data.job_id as string;
}
