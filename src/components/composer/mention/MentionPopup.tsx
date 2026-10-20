import { useEffect, useImperativeHandle, useMemo, useState, forwardRef } from "react";
import { Zap, Clock, FlaskConical, Loader2 } from "lucide-react";
import { exitSuggestion, type SuggestionProps, type SuggestionKeyDownProps } from "@tiptap/suggestion";
import { mentionPluginKey, type MentionItem, type MentionKind } from "./types";

export interface MentionPopupProps extends SuggestionProps<MentionItem, MentionItem> {
  onCreateKind: (kind: MentionKind) => void;
}

export interface MentionPopupHandle {
  onKeyDown: (props: SuggestionKeyDownProps) => boolean;
}

// ── Pinned "create new" rows ─────────────────────────────────────────────────

const CREATE_ROWS: { kind: MentionKind; label: string; icon: typeof Zap; iconClass: string }[] = [
  { kind: "skill", label: "Create new Skill", icon: Zap, iconClass: "text-amber-600" },
  { kind: "automation", label: "Create new Automation", icon: Clock, iconClass: "text-sky-600" },
  { kind: "lab", label: "Create new Lab", icon: FlaskConical, iconClass: "text-violet-600" },
];

function visibleCreateRows(query: string) {
  const q = query.trim().toLowerCase();
  if (!q) return CREATE_ROWS;
  return CREATE_ROWS.filter(r => r.kind.startsWith(q) || r.label.toLowerCase().includes(q));
}

// ── Main popup ────────────────────────────────────────────────────────────────

const MentionPopup = forwardRef<MentionPopupHandle, MentionPopupProps>(function MentionPopup(props, ref) {
  const [selectedIndex, setSelectedIndex] = useState(0);

  const items = props.items;
  const createRows = useMemo(() => visibleCreateRows(props.query), [props.query]);
  const totalRows = createRows.length + items.length;

  useEffect(() => {
    setSelectedIndex(0);
  }, [props.query]);

  const selectIndex = (index: number) => {
    if (index < createRows.length) {
      const kind = createRows[index].kind;
      const { editor, range } = props;
      exitSuggestion(editor.view, mentionPluginKey);
      editor.chain().focus().deleteRange(range).run();
      props.onCreateKind(kind);
      return;
    }
    const item = items[index - createRows.length];
    if (item) props.command(item);
  };

  useImperativeHandle(ref, () => ({
    onKeyDown({ event }) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setSelectedIndex(i => (totalRows === 0 ? 0 : (i + 1) % totalRows));
        return true;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setSelectedIndex(i => (totalRows === 0 ? 0 : (i - 1 + totalRows) % totalRows));
        return true;
      }
      if (event.key === "Enter" || event.key === "Tab") {
        event.preventDefault();
        selectIndex(selectedIndex);
        return true;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        exitSuggestion(props.editor.view, mentionPluginKey);
        return true;
      }
      return false;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }), [selectedIndex, createRows, items, totalRows]);

  return (
    <div className="w-72 bg-white border border-slate-200 rounded-xl shadow-xl overflow-hidden">
      <BrowseView
        createRows={createRows}
        items={items}
        selectedIndex={selectedIndex}
        onHoverIndex={setSelectedIndex}
        onSelect={selectIndex}
        loading={props.loading}
      />
    </div>
  );
});

export default MentionPopup;

// ── Browse view ───────────────────────────────────────────────────────────────

function BrowseView({
  createRows,
  items,
  selectedIndex,
  onHoverIndex,
  onSelect,
  loading,
}: {
  createRows: typeof CREATE_ROWS;
  items: MentionItem[];
  selectedIndex: number;
  onHoverIndex: (i: number) => void;
  onSelect: (i: number) => void;
  loading?: boolean;
}) {
  const skills = items.filter(i => i.kind === "skill");
  const automations = items.filter(i => i.kind === "automation");
  const labs = items.filter(i => i.kind === "lab");
  let cursor = 0;

  const sections = [
    { label: "Skills", icon: Zap, iconClass: "text-amber-500", list: skills },
    { label: "Automations", icon: Clock, iconClass: "text-sky-500", list: automations },
    { label: "Labs", icon: FlaskConical, iconClass: "text-violet-500", list: labs },
  ];

  const empty = !loading && createRows.length === 0 && skills.length === 0 && automations.length === 0 && labs.length === 0;

  return (
    <div className="max-h-80 overflow-y-auto py-1">
      {createRows.length > 0 && (
        <>
          <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Create new</div>
          {createRows.map(row => {
            const idx = cursor++;
            const Icon = row.icon;
            return (
              <button
                key={row.kind}
                type="button"
                onMouseEnter={() => onHoverIndex(idx)}
                onClick={() => onSelect(idx)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${selectedIndex === idx ? "bg-slate-50" : "hover:bg-slate-50"}`}
              >
                <Icon className={`w-3.5 h-3.5 shrink-0 ${row.iconClass}`} />
                <span className="text-sm text-slate-700 font-medium">{row.label}</span>
              </button>
            );
          })}
          <div className="border-t border-slate-100 my-1" />
        </>
      )}

      {loading && (
        <div className="px-3 py-4 text-xs text-slate-400 flex items-center gap-2">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading…
        </div>
      )}

      {!loading && sections.map(section => section.list.length > 0 && (
        <div key={section.label}>
          <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">{section.label}</div>
          {section.list.map(item => {
            const idx = cursor++;
            const Icon = section.icon;
            return (
              <button
                key={item.id}
                type="button"
                onMouseEnter={() => onHoverIndex(idx)}
                onClick={() => onSelect(idx)}
                className={`w-full flex items-center gap-2.5 px-3 py-2 text-left transition-colors ${selectedIndex === idx ? "bg-slate-50" : "hover:bg-slate-50"}`}
              >
                <Icon className={`w-3.5 h-3.5 shrink-0 ${section.iconClass}`} />
                <span className="text-sm text-slate-700 truncate">{item.label}</span>
              </button>
            );
          })}
        </div>
      ))}

      {empty && <div className="px-3 py-4 text-xs text-slate-400 text-center">No matches</div>}
    </div>
  );
}
