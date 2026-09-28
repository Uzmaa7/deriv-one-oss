import { Crosshair } from "lucide-react";
import { useFeedback } from "./FeedbackProvider";

export default function AnnotateToggle() {
  const { annotateActive, toggleAnnotate } = useFeedback();

  const isMac = typeof navigator !== "undefined" && /mac/i.test(navigator.platform);
  const shortcut = isMac ? "⌘⇧F" : "Ctrl+⇧F";

  return (
    <button
      onClick={toggleAnnotate}
      title={`Annotate Mode (${shortcut})`}
      aria-pressed={annotateActive}
      style={{ position: "fixed", bottom: 24, right: 24, zIndex: 9998 }}
      className={`w-11 h-11 rounded-full shadow-lg flex items-center justify-center transition-all group ${
        annotateActive
          ? "bg-amber-400 text-slate-900 shadow-amber-200"
          : "bg-slate-900 text-white hover:bg-slate-700"
      }`}
    >
      <Crosshair className="w-5 h-5" />
      <span className="absolute right-full mr-3 px-2 py-1 bg-slate-900 text-white text-xs font-medium rounded-md whitespace-nowrap opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none">
        Annotate Mode ({shortcut})
      </span>
    </button>
  );
}
