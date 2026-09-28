import { PluginKey } from "@tiptap/pm/state";

export type MentionKind = "skill" | "automation" | "lab";

export interface MentionItem {
  id: string;
  label: string;
  kind: MentionKind;
  /** Small secondary line shown in the browse list (e.g. publisher, schedule). */
  sublabel?: string;
}

/** Shared plugin key so the popup can programmatically exit the suggestion (Escape / close). */
export const mentionPluginKey = new PluginKey("mention");

/** Plugin key for the "/" skill-command trigger (separate from "@" mentions). */
export const skillCommandPluginKey = new PluginKey("skillCommand");

/**
 * Fallback chat bubble when the user picks "Create new X" and hits send with an
 * empty composer. Create intent itself is sent as `create_kind` metadata — this
 * text is only so the transcript isn't blank.
 */
export const CREATE_STARTER_MESSAGES: Record<MentionKind, string> = {
  skill: "Create a new Skill",
  automation: "Create a new Automation",
  lab: "Create a new Lab",
};

/** Label + chip styling for the "Creating: X" pill shown while a create-kind is pending in the composer. */
export const CREATE_KIND_META: Record<MentionKind, { label: string; className: string }> = {
  skill: { label: "Skill", className: "bg-amber-50 text-amber-700 border-amber-200" },
  automation: { label: "Automation", className: "bg-sky-50 text-sky-700 border-sky-200" },
  lab: { label: "Lab", className: "bg-violet-50 text-violet-700 border-violet-200" },
};
