/** Passed via react-router location.state when opening Workspace from Home. */
export interface WorkspaceDraftState {
  prompt?: string;
  skillId?: string;
  /** When true (default for Home Ask), send the prompt into the new chat immediately. */
  autoSend?: boolean;
}

export function readWorkspaceDraft(state: unknown): WorkspaceDraftState | null {
  if (!state || typeof state !== "object") return null;
  const draft = (state as { workspaceDraft?: WorkspaceDraftState }).workspaceDraft;
  if (!draft || typeof draft !== "object") return null;
  return {
    prompt: typeof draft.prompt === "string" ? draft.prompt : undefined,
    skillId: typeof draft.skillId === "string" ? draft.skillId : undefined,
    // Default to auto-send when a prompt is present (Home Ask / templates).
    autoSend:
      typeof draft.autoSend === "boolean"
        ? draft.autoSend
        : typeof draft.prompt === "string" && draft.prompt.trim().length > 0,
  };
}
