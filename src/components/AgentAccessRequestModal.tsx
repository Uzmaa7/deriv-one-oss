import { useState } from "react";
import { X, Loader2, Bot } from "lucide-react";
import { apiFetch } from "@/lib/api";

interface Props {
  agentId: string;
  agentName: string;
  agentDescription: string | null;
  onClose: () => void;
  onSuccess: () => void;
}

export default function AgentAccessRequestModal({
  agentId,
  agentName,
  agentDescription,
  onClose,
  onSuccess,
}: Props) {
  const [justification, setJustification] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/agents/${agentId}/access/request`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ justification: justification.trim() || null }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || `Request failed (${res.status})`);
      }
      onSuccess();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong.");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 bg-black/40" onClick={onClose} />
      <div className="relative bg-white rounded-2xl shadow-2xl border border-slate-200 w-full max-w-md p-6 flex flex-col gap-5">
        {/* Header */}
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-slate-100 border border-slate-200 flex items-center justify-center shrink-0">
              <Bot className="w-5 h-5 text-slate-500" />
            </div>
            <div>
              <h2 className="text-base font-bold text-slate-900">Request Access</h2>
              <p className="text-xs text-slate-500 mt-0.5">{agentName}</p>
            </div>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-700 transition-colors mt-0.5">
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Agent description */}
        {agentDescription && (
          <p className="text-sm text-slate-600 bg-slate-50 rounded-lg px-3 py-2.5 border border-slate-100">
            {agentDescription}
          </p>
        )}

        {/* Justification */}
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-slate-700">
            Justification <span className="font-normal text-slate-400">(optional)</span>
          </label>
          <textarea
            value={justification}
            onChange={e => setJustification(e.target.value)}
            maxLength={1000}
            rows={3}
            placeholder="Briefly explain why you need access to this agent..."
            className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-slate-900/10 resize-none placeholder:text-slate-400"
          />
          <p className="text-[11px] text-slate-400 text-right">{justification.length}/1000</p>
        </div>

        {error && (
          <p className="text-xs text-red-600 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </p>
        )}

        {/* Actions */}
        <div className="flex justify-end gap-2">
          <button
            onClick={onClose}
            disabled={submitting}
            className="px-4 py-2 text-sm text-slate-600 hover:text-slate-900 border border-slate-200 rounded-lg hover:bg-slate-50 transition-colors disabled:opacity-50"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting}
            className="px-4 py-2 text-sm font-semibold bg-slate-900 text-white rounded-lg hover:bg-slate-700 transition-colors disabled:opacity-50 flex items-center gap-2"
          >
            {submitting && <Loader2 className="w-3.5 h-3.5 animate-spin" />}
            Submit Request
          </button>
        </div>
      </div>
    </div>
  );
}
