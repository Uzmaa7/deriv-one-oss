import { MessageSquare, Square } from "lucide-react";

/** A file already uploaded to `/api/chat/upload`, queued to send with the next message. */
export interface PendingAttachment {
  id: string;          // stored object name returned by /api/chat/upload
  mime: string;
  name: string;
  previewUrl?: string; // objectURL for image preview, revoked after send
}

export function modelDisplayLabel(slug: string, defaultModel?: string): string {
  if (defaultModel && slug === defaultModel) return "Default";
  if (slug === "auto") return "Auto";
  return slug;
}

/** @deprecated Use modelDisplayLabel */
export function canonicalModelLabel(slug: string, defaultModel?: string): string {
  return modelDisplayLabel(slug, defaultModel);
}

export function StreamingControls({
  onStop,
  onAnswerNow,
}: {
  onStop: () => void;
  onAnswerNow: () => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <button
        type="button"
        onClick={onAnswerNow}
        title="Stop tools and answer with data collected so far"
        className="h-8 px-2.5 rounded-xl border border-slate-200 bg-white text-slate-600 text-xs font-medium hover:bg-slate-50 transition-colors flex items-center gap-1"
      >
        <MessageSquare className="w-3.5 h-3.5" />
        Answer now
      </button>
      <button
        type="button"
        onClick={onStop}
        title="Stop the agent"
        className="w-8 h-8 rounded-xl bg-red-600 text-white flex items-center justify-center hover:bg-red-700 transition-colors"
      >
        <Square className="w-3.5 h-3.5 fill-current" />
      </button>
    </div>
  );
}
