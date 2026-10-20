import { useEffect, useImperativeHandle, useState, forwardRef } from "react";
import { Zap } from "lucide-react";
import { exitSuggestion, type SuggestionProps, type SuggestionKeyDownProps } from "@tiptap/suggestion";
import { skillCommandPluginKey, type MentionItem } from "./types";

export interface SkillCommandPopupHandle {
  onKeyDown: (props: SuggestionKeyDownProps) => boolean;
}

// Flat, searchable list of Skills the user has access to — picking one
// activates it as the chat's context (no chip is inserted into the editor).
const SkillCommandPopup = forwardRef<SkillCommandPopupHandle, SuggestionProps<MentionItem, MentionItem>>(
  function SkillCommandPopup(props, ref) {
    const [selectedIndex, setSelectedIndex] = useState(0);
    const items = props.items;

    useEffect(() => {
      setSelectedIndex(0);
    }, [props.query]);

    const selectIndex = (index: number) => {
      const item = items[index];
      if (item) props.command(item);
    };

    useImperativeHandle(ref, () => ({
      onKeyDown({ event }) {
        if (event.key === "ArrowDown") {
          event.preventDefault();
          setSelectedIndex(i => (items.length === 0 ? 0 : (i + 1) % items.length));
          return true;
        }
        if (event.key === "ArrowUp") {
          event.preventDefault();
          setSelectedIndex(i => (items.length === 0 ? 0 : (i - 1 + items.length) % items.length));
          return true;
        }
        if (event.key === "Enter" || event.key === "Tab") {
          event.preventDefault();
          selectIndex(selectedIndex);
          return true;
        }
        if (event.key === "Escape") {
          event.preventDefault();
          exitSuggestion(props.editor.view, skillCommandPluginKey);
          return true;
        }
        return false;
      },
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }), [selectedIndex, items]);

    return (
      <div className="w-72 bg-white border border-slate-200 rounded-xl shadow-xl overflow-hidden">
        <div className="max-h-80 overflow-y-auto py-1">
          <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Skills</div>
          {items.map((item, idx) => (
            <button
              key={item.id}
              type="button"
              onMouseEnter={() => setSelectedIndex(idx)}
              onClick={() => selectIndex(idx)}
              className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${selectedIndex === idx ? "bg-slate-50" : "hover:bg-slate-50"}`}
            >
              <Zap className="w-3.5 h-3.5 shrink-0 text-amber-600" />
              <div className="flex-1 min-w-0">
                <div className="text-sm text-slate-700 truncate">{item.label}</div>
                {item.sublabel && <div className="text-[10px] text-slate-400 truncate">{item.sublabel}</div>}
              </div>
            </button>
          ))}
          {items.length === 0 && (
            <div className="px-3 py-4 text-xs text-slate-400 text-center">No matching skills.</div>
          )}
        </div>
      </div>
    );
  },
);

export default SkillCommandPopup;
