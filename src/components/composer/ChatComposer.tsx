import {
  forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState,
} from "react";
import { Extension } from "@tiptap/core";
import { useEditor, EditorContent } from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Placeholder from "@tiptap/extension-placeholder";
import {
  Send, Plus, X, Loader2, FileText, Zap, Search, ImagePlus,
  Brain, Clock, FlaskConical,
} from "lucide-react";
import { useSkillAccess, useSkillList } from "@/lib/SkillAccessContext";
import type { Skill } from "@/src/data";
import { StreamingControls, modelDisplayLabel, type PendingAttachment } from "./shared";
import { createMentionExtension } from "./mention/mentionExtension";
import { createSkillCommandExtension } from "./mention/skillCommandExtension";
import { CREATE_KIND_META, CREATE_STARTER_MESSAGES, type MentionItem, type MentionKind } from "./mention/types";
import { useBranding } from "@/lib/branding";

const CREATE_KIND_ICON: Record<MentionKind, typeof Zap> = { skill: Zap, automation: Clock, lab: FlaskConical };

// ── Skill picker (unchanged behaviour, now colocated with its only consumer) ──

function SkillPicker({
  activeSkillId,
  onSelect,
  onClose,
}: {
  activeSkillId: string | null;
  onSelect: (id: string | null) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = useState("");
  const ref = useRef<HTMLDivElement>(null);
  const { accessibleSlugs, loading: accessLoading } = useSkillAccess();
  const skillList = useSkillList();

  useEffect(() => {
    const handler = (e: globalThis.MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [onClose]);

  const filtered = skillList.filter(
    s => {
      const matchesQuery =
        s.name.toLowerCase().includes(query.toLowerCase()) ||
        s.publisher.toLowerCase().includes(query.toLowerCase()) ||
        s.tags.some(t => t.includes(query.toLowerCase()));
      const hasAccess = accessLoading || accessibleSlugs.has(s.slug);
      return matchesQuery && hasAccess;
    }
  );

  return (
    <div
      ref={ref}
      className="absolute bottom-full left-0 mb-2 w-80 bg-white border border-slate-200 rounded-xl shadow-xl z-50 overflow-hidden"
    >
      <div className="p-2 border-b border-slate-100">
        <div className="flex items-center gap-2 bg-slate-50 border border-slate-200 rounded-lg px-3 py-2">
          <Search className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <input
            autoFocus
            type="text"
            value={query}
            onChange={e => setQuery(e.target.value)}
            placeholder="Search skills..."
            className="flex-1 bg-transparent text-xs text-slate-900 placeholder:text-slate-400 focus:outline-none"
          />
        </div>
      </div>
      <div className="max-h-64 overflow-y-auto py-1">
        <button
          onClick={() => { onSelect(null); onClose(); }}
          className={`w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50 transition-colors ${!activeSkillId ? "bg-slate-50" : ""}`}
        >
          <div className="w-6 h-6 rounded bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0">
            <span className="text-[10px] text-slate-500">—</span>
          </div>
          <div>
            <div className="text-xs font-semibold text-slate-700">No skill</div>
            <div className="text-[10px] text-slate-400">General assistant mode</div>
          </div>
          {!activeSkillId && <div className="ml-auto w-1.5 h-1.5 rounded-full bg-emerald-500" />}
        </button>
        {filtered.map(skill => (
          <button
            key={skill.id}
            onClick={() => { onSelect(skill.id); onClose(); }}
            className={`w-full flex items-center gap-3 px-4 py-2.5 text-left hover:bg-slate-50 transition-colors ${activeSkillId === skill.id ? "bg-slate-50" : ""}`}
          >
            <div className="w-6 h-6 rounded bg-slate-900 flex items-center justify-center shrink-0">
              <Zap className="w-3 h-3 text-white" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-slate-900 truncate">{skill.name}</div>
              <div className="text-[10px] text-slate-400 truncate">@{skill.publisher}</div>
            </div>
            {activeSkillId === skill.id && <div className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0" />}
          </button>
        ))}
      </div>
    </div>
  );
}

// ── ChatComposer ────────────────────────────────────────────────────────────
//
// Rich "@"-mention composer for Workspace chat. Owns its own Tiptap editor
// instance instead of a plain string — mentions of Skills/Automations/Labs
// render as real inline chips (see mention/) rather than plain "@Label" text
// while being edited, though the plain-text form is exactly what's sent to
// the backend (Mention's default renderText — see mention/mentionExtension.ts).

export interface ChatComposerHandle {
  /** Focus the editor (e.g. after picking a quick-action chip). */
  focus: () => void;
  /** Replace the editor's content (quick-action chips, prefilled drafts). */
  setText: (text: string) => void;
  /** Clear the "Creating: X" chip — called once a proposal card actually appears. */
  clearPendingCreate: () => void;
}

export interface ChatComposerProps {
  /** Cosmetic diffs only — empty-state (centered, larger) vs docked (bottom bar). */
  variant: "empty" | "docked";
  /** `createKind` is set only on the first send after picking Create Skill/Automation/Lab. */
  onSend: (text: string, createKind?: MentionKind | null) => void;
  isStreaming: boolean;
  onStop: () => void;
  onAnswerNow: () => void;
  activeSkill: Skill | null;
  activeSkillAuto?: boolean;
  activeSkillId: string | null;
  onSelectSkill: (id: string | null) => void;
  onRemoveSkill: () => void;
  pendingAttachments: PendingAttachment[];
  onRemoveAttachment: (index: number) => void;
  isUploading: boolean;
  onAttachFile: () => void;
  thinkingEnabled: boolean;
  onToggleThinking: () => void;
  isClaudeModel: boolean;
  availableModels: string[];
  defaultModel: string;
  selectedModel: string;
  onSetModel: (model: string) => void;
  isAutoModel: boolean;
  resolvedModel?: string;
}

const QUICK_ACTIONS_WITH_SKILL = [
  "Run a sample query",
  "What tables are available?",
  "Show me a starter SQL template",
  "Explain the key fields",
];
const QUICK_ACTIONS_NO_SKILL = [
  "What skills should I install?",
  "Summarise today's anomalies",
  "Draft an incident response",
  "Show me Q1 performance",
];

export const ChatComposer = forwardRef<ChatComposerHandle, ChatComposerProps>(function ChatComposer(
  {
    variant,
    onSend,
    isStreaming,
    onStop,
    onAnswerNow,
    activeSkill,
    activeSkillAuto,
    activeSkillId,
    onSelectSkill,
    onRemoveSkill,
    pendingAttachments,
    onRemoveAttachment,
    isUploading,
    onAttachFile,
    thinkingEnabled,
    onToggleThinking,
    isClaudeModel,
    availableModels,
    defaultModel,
    selectedModel,
    onSetModel,
    isAutoModel,
    resolvedModel,
  },
  ref,
) {
  const skillList = useSkillList();
  const { accessibleSlugs, loading: accessLoading } = useSkillAccess();
  const branding = useBranding();
  const dockedPlaceholder = `Message ${branding.app_short_name}…`;

  const [skillPickerOpen, setSkillPickerOpen] = useState(false);
  const [plusMenuOpen, setPlusMenuOpen] = useState(false);
  const [isEmpty, setIsEmpty] = useState(true);
  const [pendingCreateKind, setPendingCreateKind] = useState<MentionKind | null>(null);
  const plusMenuRef = useRef<HTMLDivElement>(null);

  // Stable indirection so the (created-once) keymap extension always calls the
  // latest onSend/isStreaming without the editor needing to be recreated.
  const onSendRef = useRef<() => void>(() => {});
  const isStreamingRef = useRef(isStreaming);
  useEffect(() => { isStreamingRef.current = isStreaming; }, [isStreaming]);

  // Live snapshot of Skill mention items, read from a ref by the mention
  // extension's `items()` — see mention/mentionExtension.ts's getSkillItems doc.
  const skillItemsRef = useRef<MentionItem[]>([]);
  useEffect(() => {
    skillItemsRef.current = skillList.map(s => ({
      id: s.id, label: s.name, kind: "skill" as const, sublabel: `@${s.publisher}`,
    }));
  }, [skillList]);
  const getSkillItems = useCallback(() => skillItemsRef.current, []);

  // Same live-snapshot pattern, but filtered to skills the user can actually
  // activate — backs the "/" skill-command trigger (mention/skillCommandExtension.ts).
  const skillCommandItemsRef = useRef<MentionItem[]>([]);
  useEffect(() => {
    skillCommandItemsRef.current = skillList
      .filter(s => accessLoading || accessibleSlugs.has(s.slug))
      .map(s => ({ id: s.id, label: s.name, kind: "skill" as const, sublabel: `@${s.publisher}` }));
  }, [skillList, accessibleSlugs, accessLoading]);
  const getAccessibleSkillItems = useCallback(() => skillCommandItemsRef.current, []);

  // Stable indirection into the always-latest onSelectSkill prop, since the
  // "/" extension is created once with the editor and can't close over props.
  const onSelectSkillRef = useRef(onSelectSkill);
  useEffect(() => { onSelectSkillRef.current = onSelectSkill; }, [onSelectSkill]);

  // Picking "Create new X" (from the "@"-popup or the "+" menu) marks the
  // composer with a removable "Creating: X" chip instead of sending right
  // away, so the user can type whatever details they already have — create
  // intent is sent as metadata on the first send (see handleSend), not
  // prepended into the message text. Focuses via a ref (set once `editor`
  // exists below) since this callback is itself a dependency of the editor's
  // extensions, so it can't close over `editor`.
  const editorRef = useRef<ReturnType<typeof useEditor>>(null);
  // Whether create_kind has already been sent once for the current
  // pendingCreateKind — subsequent follow-up messages (while Claude asks
  // clarifying questions) go out without re-announcing intent.
  const [createIntentAnnounced, setCreateIntentAnnounced] = useState(false);
  const onCreateKind = useCallback((kind: MentionKind) => {
    setPendingCreateKind(kind);
    setCreateIntentAnnounced(false);
    editorRef.current?.commands.focus("end");
  }, []);

  const placeholderRef = useRef(
    activeSkill ? `Ask ${activeSkill.name}…` : variant === "docked" ? dockedPlaceholder : "Ask anything…",
  );

  const editor = useEditor({
    extensions: [
      StarterKit.configure({
        heading: false,
        bold: false,
        italic: false,
        strike: false,
        code: false,
        codeBlock: false,
        blockquote: false,
        bulletList: false,
        orderedList: false,
        listItem: false,
        listKeymap: false,
        link: false,
        underline: false,
        horizontalRule: false,
        dropcursor: false,
        gapcursor: false,
        trailingNode: false,
      }),
      Placeholder.configure({ placeholder: () => placeholderRef.current }),
      createMentionExtension({
        getSkillItems,
        onCreateKind,
      }),
      createSkillCommandExtension({
        getAccessibleSkillItems,
        onSelectSkill: id => onSelectSkillRef.current(id),
      }),
      Extension.create({
        name: "sendOnEnter",
        addKeyboardShortcuts() {
          return {
            Enter: () => {
              onSendRef.current();
              return true;
            },
            "Shift-Enter": () => this.editor.commands.setHardBreak(),
          };
        },
      }),
    ],
    editorProps: {
      attributes: {
        class: "text-sm text-slate-900 leading-relaxed max-h-40 overflow-y-auto outline-none focus:outline-none",
      },
    },
    onUpdate: ({ editor: e }) => setIsEmpty(e.isEmpty),
    immediatelyRender: false,
  }, []);

  useEffect(() => { editorRef.current = editor ?? null; }, [editor]);

  // `useEditor` with `immediatelyRender: false` doesn't create `editor` until
  // after the first render, so an imperative setText() call made on mount
  // (e.g. prefilling a draft prompt carried over from the landing page) can
  // land while editor is still null. Buffer it and apply once editor exists.
  const pendingTextRef = useRef<string | null>(null);
  useEffect(() => {
    if (!editor || pendingTextRef.current === null) return;
    editor.commands.setContent(pendingTextRef.current);
    editor.commands.focus("end");
    pendingTextRef.current = null;
  }, [editor]);

  // Keep the placeholder in sync with activeSkill/variant/pendingCreateKind
  // without recreating the editor; force a decoration refresh via a no-op
  // transaction.
  useEffect(() => {
    placeholderRef.current = pendingCreateKind
      ? `Describe the ${CREATE_KIND_META[pendingCreateKind].label} you want to create…`
      : activeSkill
      ? `Ask ${activeSkill.name}…`
      : variant === "docked" ? dockedPlaceholder : "Ask anything…";
    if (editor && !editor.isDestroyed) {
      editor.view.dispatch(editor.state.tr);
    }
  }, [editor, activeSkill, variant, pendingCreateKind, dockedPlaceholder]);

  const handleSend = useCallback(() => {
    if (!editor || isStreamingRef.current) return;
    const text = editor.getText().trim();
    const announceCreate = Boolean(pendingCreateKind && !createIntentAnnounced);
    // Empty-box send is only allowed on the first message of a create flow
    // (fallback bubble text) — after that, empty is a no-op.
    if (!text && !announceCreate) return;
    // Keep the user's typed text as-is. Create intent travels as createKind
    // metadata so the UI chip choice is authoritative without rewriting the prompt.
    const finalText = text || (pendingCreateKind ? CREATE_STARTER_MESSAGES[pendingCreateKind] : "");
    const createKind = announceCreate ? pendingCreateKind : null;
    if (pendingCreateKind) setCreateIntentAnnounced(true);
    onSend(finalText, createKind);
    editor.commands.clearContent(true);
  }, [editor, onSend, pendingCreateKind, createIntentAnnounced]);
  useEffect(() => { onSendRef.current = handleSend; }, [handleSend]);

  useImperativeHandle(ref, () => ({
    focus: () => editor?.commands.focus("end"),
    setText: (text: string) => {
      if (!editor) {
        pendingTextRef.current = text;
        return;
      }
      editor.commands.setContent(text);
      editor.commands.focus("end");
    },
    clearPendingCreate: () => {
      setPendingCreateKind(null);
      setCreateIntentAnnounced(false);
    },
  }), [editor]);

  const triggerCreate = useCallback((kind: MentionKind) => {
    setPlusMenuOpen(false);
    onCreateKind(kind);
  }, [onCreateKind]);

  useEffect(() => {
    if (!plusMenuOpen) return;
    const handler = (e: globalThis.MouseEvent) => {
      if (plusMenuRef.current && !plusMenuRef.current.contains(e.target as Node)) {
        setPlusMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [plusMenuOpen]);

  const quickActions = useMemo(
    () => (activeSkill ? QUICK_ACTIONS_WITH_SKILL : QUICK_ACTIONS_NO_SKILL),
    [activeSkill],
  );

  const isDocked = variant === "docked";

  return (
    <div>
    <div
      className={
        isDocked
          ? "relative flex flex-col bg-slate-50 border border-slate-200 rounded-2xl px-4 pt-3.5 pb-2.5 focus-within:border-slate-400 focus-within:bg-white transition-all"
          : "relative flex flex-col bg-white border border-slate-200 rounded-2xl px-4 pt-4 pb-3 shadow-sm focus-within:border-slate-400 focus-within:shadow-md transition-all"
      }
    >
      {/* Pending attachment thumbnails */}
      {pendingAttachments.length > 0 && (
        <div className={`flex flex-wrap gap-2 ${isDocked ? "mb-2" : "mb-2.5"}`}>
          {pendingAttachments.map((att, i) => (
            <div key={att.id} className={`relative flex items-center gap-1.5 border border-slate-200 rounded-lg px-2 py-1.5 text-xs text-slate-600 ${isDocked ? "bg-white shadow-sm" : "bg-slate-50"}`}>
              {att.previewUrl ? (
                <img src={att.previewUrl} alt={att.name} className="w-7 h-7 object-cover rounded" />
              ) : (
                <FileText className="w-4 h-4 text-slate-400 shrink-0" />
              )}
              <span className="max-w-[80px] truncate">{att.name}</span>
              <button onClick={() => onRemoveAttachment(i)} className="text-slate-300 hover:text-slate-600 transition-colors ml-0.5">
                <X className="w-3 h-3" />
              </button>
            </div>
          ))}
        </div>
      )}

      <EditorContent editor={editor} className="chat-composer-editor" />

      <div className={`flex items-center justify-between ${isDocked ? "mt-2" : "mt-3"}`}>
        <div className="flex items-center gap-1.5">
          {/* Plus menu */}
          <div className="relative" ref={plusMenuRef}>
            <button
              onClick={() => { setPlusMenuOpen(v => !v); setSkillPickerOpen(false); }}
              title="Add attachment, select a skill, or create something new"
              className="p-1.5 text-slate-400 hover:text-slate-700 hover:bg-slate-100 rounded-lg transition-colors"
            >
              <Plus className="w-4 h-4" />
            </button>
            {plusMenuOpen && (
              <div className="absolute bottom-full left-0 mb-2 w-64 bg-white border border-slate-200 rounded-xl shadow-xl z-50 overflow-hidden py-1">
                <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Send to Claude</div>
                <button
                  onClick={() => { setPlusMenuOpen(false); onAttachFile(); }}
                  disabled={isUploading || pendingAttachments.length >= 3}
                  className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-slate-50 transition-colors disabled:opacity-40"
                >
                  {isUploading ? <Loader2 className="w-4 h-4 animate-spin text-sky-500 mt-0.5 shrink-0" /> : <ImagePlus className="w-4 h-4 text-sky-500 mt-0.5 shrink-0" />}
                  <div>
                    <div className="text-sm text-slate-700 font-medium">Attach file</div>
                    <div className="text-xs text-slate-400">Image or PDF — Claude will read it</div>
                  </div>
                </button>
                <div className="border-t border-slate-100 my-1" />
                <div className="px-3 py-1.5 text-[10px] font-semibold text-slate-400 uppercase tracking-wider">Workspace</div>
                <button
                  onClick={() => { setPlusMenuOpen(false); setSkillPickerOpen(true); }}
                  className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-slate-50 transition-colors"
                >
                  <Zap className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-sm text-slate-700 font-medium">Skills</div>
                    <div className="text-xs text-slate-400">Load a skill context</div>
                  </div>
                </button>
                <button
                  onClick={() => triggerCreate("skill")}
                  className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-slate-50 transition-colors"
                >
                  <Zap className="w-4 h-4 text-amber-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-sm text-slate-700 font-medium">Create Skill</div>
                    <div className="text-xs text-slate-400">Draft a new Skill</div>
                  </div>
                </button>
                <button
                  onClick={() => triggerCreate("automation")}
                  className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-slate-50 transition-colors"
                >
                  <Clock className="w-4 h-4 text-sky-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-sm text-slate-700 font-medium">Automation</div>
                    <div className="text-xs text-slate-400">Create a new scheduled automation</div>
                  </div>
                </button>
                <button
                  onClick={() => triggerCreate("lab")}
                  className="w-full flex items-start gap-3 px-3 py-2.5 text-left hover:bg-slate-50 transition-colors"
                >
                  <FlaskConical className="w-4 h-4 text-violet-500 mt-0.5 shrink-0" />
                  <div>
                    <div className="text-sm text-slate-700 font-medium">Lab</div>
                    <div className="text-xs text-slate-400">Create a new Lab app</div>
                  </div>
                </button>
              </div>
            )}
          </div>
          {/* Model selector */}
          {availableModels.length > 1 && (
            <div className="flex items-center gap-1.5">
              <select
                value={selectedModel || defaultModel}
                onChange={e => onSetModel(e.target.value)}
                disabled={isStreaming}
                title="Select model"
                className="px-2 py-1 rounded-lg text-xs text-slate-600 bg-white border border-slate-200 hover:border-slate-300 focus:outline-none focus:ring-1 focus:ring-slate-400 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed max-w-[150px] truncate"
              >
                {availableModels.map(m => (
                  <option key={m} value={m}>
                    {modelDisplayLabel(m, defaultModel)}
                  </option>
                ))}
              </select>
              {isAutoModel && resolvedModel && (
                <span
                  title={`Auto selected: ${resolvedModel}`}
                  className="text-[10px] px-1.5 py-0.5 rounded bg-amber-50 text-amber-600 border border-amber-200 font-medium truncate max-w-[110px]"
                >
                  {resolvedModel}
                </span>
              )}
            </div>
          )}
          {/* Deep Think toggle — Claude only */}
          {isClaudeModel && (
            <button
              onClick={onToggleThinking}
              title={thinkingEnabled ? "Disable extended thinking" : "Enable extended thinking (Claude reasons step-by-step)"}
              className={`flex items-center gap-1 px-2 py-1 rounded-lg text-xs transition-colors ${
                thinkingEnabled
                  ? "bg-violet-100 text-violet-700 border border-violet-200"
                  : "text-slate-400 hover:text-slate-700 hover:bg-slate-100"
              }`}
            >
              <Brain className="w-3.5 h-3.5 shrink-0" />
              {thinkingEnabled && <span className="font-medium">Think</span>}
            </button>
          )}
          {/* Pending "create new X" chip */}
          {pendingCreateKind && (
            <div className={`flex items-center gap-1 px-2 py-1 rounded-lg border ${CREATE_KIND_META[pendingCreateKind].className}`}>
              {(() => {
                const KindIcon = CREATE_KIND_ICON[pendingCreateKind];
                return <KindIcon className="w-3 h-3 shrink-0" />;
              })()}
              <span className="text-xs font-medium">Creating {CREATE_KIND_META[pendingCreateKind].label}</span>
              <button
                onClick={() => {
                  setPendingCreateKind(null);
                  setCreateIntentAnnounced(false);
                }}
                title="Cancel"
                className="ml-0.5 opacity-60 hover:opacity-100 transition-opacity"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}
          {/* Active skill badge */}
          {activeSkill && (
            <div className="flex items-center gap-1 px-2 py-1 bg-amber-50 border border-amber-200 rounded-lg">
              <Zap className="w-3 h-3 text-amber-500 shrink-0" />
              <span className="text-xs font-medium text-amber-700 max-w-[120px] truncate">{activeSkill.name}</span>
              {activeSkillAuto && (
                <span className="px-1 py-0.5 rounded bg-amber-100 text-amber-600 text-[9px] font-semibold uppercase">
                  Auto
                </span>
              )}
              <button
                onClick={onRemoveSkill}
                title="Remove skill"
                className="ml-0.5 text-amber-400 hover:text-amber-700 transition-colors"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          )}
          {skillPickerOpen && (
            <SkillPicker
              activeSkillId={activeSkillId}
              onSelect={id => onSelectSkill(id)}
              onClose={() => setSkillPickerOpen(false)}
            />
          )}
        </div>
        <div className="flex items-center gap-1.5">
          <button
            onClick={handleSend}
            disabled={(isEmpty && (!pendingCreateKind || createIntentAnnounced)) || isStreaming}
            className="w-8 h-8 rounded-xl bg-slate-900 text-white flex items-center justify-center hover:bg-slate-700 transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
          >
            {isStreaming ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
          </button>
          {isStreaming && <StreamingControls onStop={onStop} onAnswerNow={onAnswerNow} />}
        </div>
      </div>
    </div>

    {isDocked ? (
      <p className="text-center text-[10px] text-slate-300 mt-2 font-mono">↵ Send · ⇧↵ Newline</p>
    ) : (
      <div className="flex flex-wrap justify-center gap-2 mt-4">
        {quickActions.map(s => (
          <button
            key={s}
            onClick={() => { editor?.commands.setContent(s); editor?.commands.focus("end"); }}
            className="text-xs text-slate-500 bg-white border border-slate-200 rounded-full px-4 py-1.5 hover:bg-slate-50 hover:border-slate-300 hover:text-slate-700 transition-all shadow-sm"
          >
            {s}
          </button>
        ))}
      </div>
    )}
    </div>
  );
});
