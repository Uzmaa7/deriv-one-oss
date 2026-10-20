import Mention from "@tiptap/extension-mention";
import { ReactRenderer, ReactNodeViewRenderer } from "@tiptap/react";
import { apiFetch } from "@/lib/api";
import MentionChipView from "./MentionChipView";
import MentionPopup, { type MentionPopupHandle } from "./MentionPopup";
import { mentionPluginKey, type MentionItem, type MentionKind } from "./types";

export interface MentionExtensionOptions {
  /** Live snapshot of Skill mention items — reads from a ref so the editor never needs recreating. */
  getSkillItems: () => MentionItem[];
  /** Picking a "Create new X" row calls this instead of opening a form — see ChatComposer's triggerCreate. */
  onCreateKind: (kind: MentionKind) => void;
}

async function fetchAutomationItems(): Promise<MentionItem[]> {
  try {
    const res = await apiFetch("/api/automations");
    if (!res.ok) return [];
    const data = await res.json();
    return (Array.isArray(data) ? data : []).map((a: { id: string; name: string }) => ({
      id: a.id,
      label: a.name,
      kind: "automation" as const,
    }));
  } catch {
    return [];
  }
}

async function fetchLabItems(): Promise<MentionItem[]> {
  try {
    const res = await apiFetch("/api/apps");
    if (!res.ok) return [];
    const data = await res.json();
    return (Array.isArray(data) ? data : []).map((a: { id: string; name: string }) => ({
      id: a.id,
      label: a.name,
      kind: "lab" as const,
    }));
  } catch {
    return [];
  }
}

/**
 * Builds the Mention node extension: a `kind`-aware chip node rendered via
 * `MentionChipView`, backed by a suggestion popup (`MentionPopup`) that browses
 * existing Skills/Automations/Labs or quick-creates a new one.
 */
export function createMentionExtension(options: MentionExtensionOptions) {
  // Scoped to this editor instance; refreshed once per popup-open (see onStart).
  let automationsCache: MentionItem[] | null = null;
  let labsCache: MentionItem[] | null = null;

  return Mention.extend({
    addAttributes() {
      return {
        ...this.parent?.(),
        kind: {
          default: "skill",
          parseHTML: (element: HTMLElement) => element.getAttribute("data-kind"),
          renderHTML: (attributes: Record<string, unknown>) =>
            attributes.kind ? { "data-kind": attributes.kind } : {},
        },
      };
    },
    addNodeView() {
      return ReactNodeViewRenderer(MentionChipView);
    },
  }).configure({
    suggestion: {
      char: "@",
      pluginKey: mentionPluginKey,
      command: ({ editor, range, props: item }) => {
        const mentionItem = item as unknown as MentionItem;
        editor
          .chain()
          .focus()
          .insertContentAt(range, [
            { type: "mention", attrs: { id: mentionItem.id, label: mentionItem.label, kind: mentionItem.kind } },
            { type: "text", text: " " },
          ])
          .run();
      },
      items: async ({ query }: { query: string }) => {
        if (automationsCache === null) automationsCache = await fetchAutomationItems();
        if (labsCache === null) labsCache = await fetchLabItems();
        const all = [...options.getSkillItems(), ...automationsCache, ...labsCache];
        const q = query.trim().toLowerCase();
        if (!q) return all.slice(0, 30);
        return all.filter(i => i.label.toLowerCase().includes(q)).slice(0, 30);
      },
      render: () => {
        let component: ReactRenderer<MentionPopupHandle> | null = null;
        let unmount: (() => void) | null = null;

        return {
          onStart: props => {
            // Refetch once per popup-open so newly created items show up next time.
            automationsCache = null;
            labsCache = null;

            component = new ReactRenderer(MentionPopup, {
              props: {
                ...props,
                onCreateKind: options.onCreateKind,
              },
              editor: props.editor,
            });
            unmount = props.mount(component.element);
          },
          onUpdate: props => {
            component?.updateProps({
              ...props,
              onCreateKind: options.onCreateKind,
            });
          },
          onKeyDown: props => component?.ref?.onKeyDown(props) ?? false,
          onExit: () => {
            unmount?.();
            component?.destroy();
            component = null;
            unmount = null;
          },
        };
      },
    },
  });
}
