import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

export interface AgentDraftPayload {
  name: string;
  description: string | null;
  usecase_description: string | null;
  skill_ids: string[];
  soul_text: string | null;
  channel_ids: string[];
  trigger_mode: string;
  guardrails: { id: string; type: string; enabled: boolean }[];
  token_mode: "oauth" | "guided";
  llm_model?: string | null;
}

/**
 * Server-backed auto-save for the agent registration form. One draft per user.
 *
 * - Loads the saved draft once on mount (so the user can resume after leaving).
 * - Debounced auto-save 800ms after any change.
 * - `discardDraft()` clears the server draft (call after a successful registration).
 */
export function useAgentDraft() {
  const [loaded, setLoaded] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<AgentDraftPayload | null>(null);

  // Load on mount.
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch("/api/agents/draft");
        if (!res.ok) return;
        const data = await res.json();
        if (cancelled) return;
        if (data?.draft) {
          onRestoreRef.current?.(data.draft);
          setSavedAt(data.updated_at ?? null);
        }
      } catch {
        /* ignore — draft is a convenience */
      } finally {
        if (!cancelled) setLoaded(true);
      }
    })();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Restore callback registered by the page.
  const onRestoreRef = useRef<((d: AgentDraftPayload) => void) | null>(null);
  const registerRestore = useCallback((fn: (d: AgentDraftPayload) => void) => {
    onRestoreRef.current = fn;
  }, []);

  const save = useCallback(async (draft: AgentDraftPayload) => {
    latest.current = draft;
    setSaving(true);
    try {
      const res = await apiFetch("/api/agents/draft", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(draft),
      });
      if (res.ok) {
        const data = await res.json().catch(() => ({}));
        setSavedAt(data?.updated_at ?? null);
      }
    } catch {
      /* ignore */
    } finally {
      setSaving(false);
    }
  }, []);

  // Debounced auto-save: schedule a save 800ms after the latest change.
  const scheduleSave = useCallback((draft: AgentDraftPayload) => {
    latest.current = draft;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (latest.current) save(latest.current);
    }, 800);
  }, [save]);

  const discardDraft = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    try {
      await apiFetch("/api/agents/draft", { method: "DELETE" });
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { loaded, savedAt, saving, scheduleSave, saveNow: save, discardDraft, registerRestore };
}
