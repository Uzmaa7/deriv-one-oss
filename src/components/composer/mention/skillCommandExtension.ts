import { Extension } from "@tiptap/core";
import Suggestion from "@tiptap/suggestion";
import { ReactRenderer } from "@tiptap/react";
import { skillCommandPluginKey, type MentionItem } from "./types";
import SkillCommandPopup, { type SkillCommandPopupHandle } from "./SkillCommandPopup";

export interface SkillCommandExtensionOptions {
  /** Live snapshot of Skills the user has access to — reads from a ref, see ChatComposer. */
  getAccessibleSkillItems: () => MentionItem[];
  /** Picking a skill activates it as the chat's context — no chip is inserted. */
  onSelectSkill: (id: string) => void;
}

/**
 * A Slack-style "/" trigger, separate from the "@" mention system: it never
 * inserts anything into the editor, it just deletes the typed `/query` and
 * activates the chosen Skill (same effect as the "+" menu's Skill picker).
 */
export function createSkillCommandExtension(options: SkillCommandExtensionOptions) {
  return Extension.create({
    name: "skillCommand",
    addProseMirrorPlugins() {
      return [
        Suggestion({
          editor: this.editor,
          char: "/",
          startOfLine: true,
          pluginKey: skillCommandPluginKey,
          command: ({ editor, range, props: item }) => {
            const skill = item as unknown as MentionItem;
            editor.chain().focus().deleteRange(range).run();
            options.onSelectSkill(skill.id);
          },
          items: ({ query }: { query: string }) => {
            const q = query.trim().toLowerCase();
            const all = options.getAccessibleSkillItems();
            if (!q) return all.slice(0, 30);
            return all.filter(i => i.label.toLowerCase().includes(q)).slice(0, 30);
          },
          render: () => {
            let component: ReactRenderer<SkillCommandPopupHandle> | null = null;
            let unmount: (() => void) | null = null;

            return {
              onStart: props => {
                component = new ReactRenderer(SkillCommandPopup, { props, editor: props.editor });
                unmount = props.mount(component.element);
              },
              onUpdate: props => {
                component?.updateProps(props);
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
        }),
      ];
    },
  });
}
