import { apiFetch } from "@/lib/api";
import type { ElementContext } from "./serializeTarget";

export interface FeedbackRow {
  id: string;
  created_at: string;
  created_by_email: string;
  route: string;
  element_context: ElementContext;
  user_message: string;
  category: "bug" | "feature" | "ux" | "other";
  status: "open" | "triaged" | "in_progress" | "done" | "wont_do";
  assignee: string | null;
  priority: "low" | "medium" | "high" | "urgent" | null;
  resolution_note: string | null;
  triaged_at: string | null;
}

export async function submitFeedback(payload: {
  route: string;
  element_context: ElementContext;
  user_message: string;
  category: string;
}): Promise<FeedbackRow> {
  const res = await apiFetch("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `HTTP ${res.status}`);
  }
  return res.json();
}

export async function listFeedback(): Promise<FeedbackRow[]> {
  const res = await apiFetch("/api/feedback");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}

export interface FeedbackAssignee {
  email: string;
  name: string;
}

export async function listFeedbackAssignees(): Promise<FeedbackAssignee[]> {
  const res = await apiFetch("/api/admin/feedback/assignees");
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const data = await res.json();
  return data.assignees ?? [];
}

export async function patchFeedback(
  id: string,
  patch: {
    status?: string;
    resolution_note?: string;
    assignee?: string | null;
    priority?: string | null;
  }
): Promise<FeedbackRow> {
  const res = await apiFetch(`/api/feedback/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  return res.json();
}
