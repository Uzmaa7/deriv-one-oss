import { useEffect, useRef, useState } from "react";
import { X, Send, Loader2 } from "lucide-react";
import { useFeedback } from "./FeedbackProvider";
import { serializeTarget } from "./serializeTarget";
import { submitFeedback } from "./api";

const CATEGORIES = [
  { value: "bug", label: "🐛 Bug" },
  { value: "feature", label: "✨ Feature request" },
  { value: "ux", label: "🎨 UX / Design" },
  { value: "other", label: "💬 Other" },
] as const;

export default function FeedbackPanel() {
  const { pickedElement, setPicked } = useFeedback();
  const [message, setMessage] = useState("");
  const [category, setCategory] = useState<string>("bug");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const ctx = pickedElement ? serializeTarget(pickedElement) : null;
  const route = window.location.pathname + window.location.hash;

  useEffect(() => {
    setTimeout(() => textareaRef.current?.focus(), 50);
  }, []);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") dismiss();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  });

  function dismiss() {
    setPicked(null);
  }

  async function handleSubmit() {
    if (!message.trim() || !ctx) return;
    setSubmitting(true);
    setError(null);
    try {
      await submitFeedback({ route, element_context: ctx, user_message: message.trim(), category });
      setSubmitted(true);
      setTimeout(dismiss, 1200);
    } catch (e) {
      setError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      {/* Backdrop */}
      <div
        style={{ position: "fixed", inset: 0, zIndex: 10000, backgroundColor: "rgba(0,0,0,0.3)" }}
        onClick={dismiss}
        aria-hidden="true"
      />

      {/* Panel */}
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Submit feedback"
        style={{ position: "fixed", zIndex: 10001, top: "50%", left: "50%", transform: "translate(-50%,-50%)", width: "100%", maxWidth: 480 }}
        className="bg-white rounded-2xl shadow-2xl border border-slate-200 p-5"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-bold text-slate-900">Submit Feedback</h2>
          <button onClick={dismiss} className="w-7 h-7 flex items-center justify-center rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-700 transition-colors">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Element context preview */}
        {ctx && (ctx.label || ctx.selector) && (
          <div className="mb-4 px-3 py-2 bg-slate-50 border border-slate-200 rounded-lg">
            <p className="text-[10px] font-mono text-slate-400 mb-0.5">Selected element</p>
            {ctx.label && <p className="text-xs text-slate-700 font-medium truncate">{ctx.label}</p>}
            {ctx.selector && <p className="text-[10px] font-mono text-slate-400 truncate mt-0.5">{ctx.selector}</p>}
          </div>
        )}

        {submitted ? (
          <div className="py-8 text-center">
            <div className="text-3xl mb-2">✅</div>
            <p className="text-sm font-semibold text-slate-700">Feedback submitted!</p>
          </div>
        ) : (
          <>
            {/* Category */}
            <div className="mb-3">
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">Category</label>
              <div className="flex flex-wrap gap-2">
                {CATEGORIES.map(c => (
                  <button
                    key={c.value}
                    onClick={() => setCategory(c.value)}
                    className={`text-xs px-3 py-1.5 rounded-lg border font-medium transition-colors ${
                      category === c.value
                        ? "bg-slate-900 text-white border-slate-900"
                        : "bg-white text-slate-600 border-slate-200 hover:border-slate-400"
                    }`}
                  >
                    {c.label}
                  </button>
                ))}
              </div>
            </div>

            {/* Message */}
            <div className="mb-4">
              <label className="block text-xs font-semibold text-slate-600 mb-1.5">
                What's the issue or suggestion?
              </label>
              <textarea
                ref={textareaRef}
                value={message}
                onChange={e => setMessage(e.target.value)}
                maxLength={4000}
                rows={4}
                placeholder="Describe what you noticed..."
                className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2.5 resize-none focus:outline-none focus:ring-2 focus:ring-slate-900 text-slate-800 placeholder-slate-400"
              />
              <p className="text-[10px] text-slate-400 mt-1 text-right">{message.length}/4000</p>
            </div>

            {error && (
              <p className="text-xs text-red-600 mb-3">{error}</p>
            )}

            {/* Actions */}
            <div className="flex justify-end gap-2">
              <button
                onClick={dismiss}
                className="text-sm px-4 py-2 rounded-lg text-slate-600 hover:bg-slate-100 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={handleSubmit}
                disabled={submitting || !message.trim()}
                className="flex items-center gap-2 text-sm px-4 py-2 rounded-lg bg-slate-900 text-white font-semibold hover:bg-slate-800 disabled:opacity-50 transition-colors"
              >
                {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
                Submit
              </button>
            </div>
          </>
        )}
      </div>
    </>
  );
}
