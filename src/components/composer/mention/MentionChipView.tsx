import { NodeViewWrapper, type NodeViewProps } from "@tiptap/react";
import { Zap, Clock, FlaskConical } from "lucide-react";
import type { MentionKind } from "./types";

const KIND_STYLES: Record<MentionKind, { icon: typeof Zap; className: string }> = {
  skill: { icon: Zap, className: "bg-amber-50 text-amber-700 border-amber-200" },
  automation: { icon: Clock, className: "bg-sky-50 text-sky-700 border-sky-200" },
  lab: { icon: FlaskConical, className: "bg-violet-50 text-violet-700 border-violet-200" },
};

/** Renders a mention as an atomic, non-editable inline chip/pill. */
export default function MentionChipView({ node }: NodeViewProps) {
  const kind = (node.attrs.kind as MentionKind) ?? "skill";
  const { icon: Icon, className } = KIND_STYLES[kind];

  return (
    <NodeViewWrapper
      as="span"
      className={`inline-flex items-center gap-1 px-1.5 py-0.5 mx-0.5 rounded-md border text-xs font-medium align-baseline select-none ${className}`}
      contentEditable={false}
    >
      <Icon className="w-3 h-3 shrink-0" />
      <span className="max-w-[160px] truncate">{node.attrs.label}</span>
    </NodeViewWrapper>
  );
}
