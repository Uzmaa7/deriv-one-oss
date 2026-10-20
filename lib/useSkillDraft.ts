import { useCallback, useEffect, useRef, useState } from "react";
import { apiFetch } from "@/lib/api";

export interface SkillDraftPayload {
  step: "upload" | "form";
  skill_name: string;
  slug: string;
  slug_manual: boolean;
  description: string;
  departments: string[];
  integrations: string[];
  risk_level: string;
  cross_dept_policy: string;
  cross_dept_default_grant: string;
  cross_dept_approvers: string[];
  /** Env var key names only — never values. */
  secret_keys: string[];
  skill_file_content: string | null;
  skill_file_name: string | null;
}

/**
 * Server-backed auto-save for the skill publish form. One draft per user.
 *
 * - Loads the saved draft once on mount (so the user can resume after leaving).
 * - Debounced auto-save 800ms after any change.
 * - `discardDraft()` clears the server draft (call after a successful submit).
 * - Never persists secret values — only key names and SKILL.md text.
 */
export function useSkillDraft() {
  const [loaded, setLoaded] = useState(false);
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const latest = useRef<SkillDraftPayload | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const res = await apiFetch("/api/skills/draft");
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

  const onRestoreRef = useRef<((d: SkillDraftPayload) => void) | null>(null);
  const registerRestore = useCallback((fn: (d: SkillDraftPayload) => void) => {
    onRestoreRef.current = fn;
  }, []);

  const save = useCallback(async (draft: SkillDraftPayload) => {
    latest.current = draft;
    setSaving(true);
    try {
      const res = await apiFetch("/api/skills/draft", {
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

  const scheduleSave = useCallback((draft: SkillDraftPayload) => {
    latest.current = draft;
    if (timer.current) clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      if (latest.current) save(latest.current);
    }, 800);
  }, [save]);

  const discardDraft = useCallback(async () => {
    if (timer.current) clearTimeout(timer.current);
    setSavedAt(null);
    try {
      await apiFetch("/api/skills/draft", { method: "DELETE" });
    } catch {
      /* ignore */
    }
  }, []);

  useEffect(() => () => { if (timer.current) clearTimeout(timer.current); }, []);

  return { loaded, savedAt, saving, scheduleSave, saveNow: save, discardDraft, registerRestore };
}
