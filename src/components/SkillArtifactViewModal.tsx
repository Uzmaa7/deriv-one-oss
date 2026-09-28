import { useEffect, useState } from "react";
import { Loader2, Wand2, X } from "lucide-react";
import { apiFetch } from "@/lib/api";
import {
  apiErrorDetail,
  editPublishedArtifact,
  getSkillArtifact,
  parseApiJson,
  type ArtifactDetail,
} from "@/lib/skillArtifactsApi";

export function SkillArtifactViewModal({
  slug,
  name,
  version,
  canManage,
  onClose,
  onChanged,
  onStartAiEdit,
}: {
  slug: string;
  name: string;
  version: number;
  canManage: boolean;
  onClose: () => void;
  onChanged: () => void;
  onStartAiEdit?: (jobId: string) => void;
}) {
  const [artifact, setArtifact] = useState<ArtifactDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  const [showEditPanel, setShowEditPanel] = useState(false);
  const [editPrompt, setEditPrompt] = useState("");
  const [editBusy, setEditBusy] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    getSkillArtifact(slug, name, version)
      .then(a => {
        if (!cancelled) {
          setArtifact(a);
          setError(null);
        }
      })
      .catch(e => {
        if (!cancelled) setError(e instanceof Error ? e.message : "Load failed");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [slug, name, version]);

  async function approve() {
    const res = await apiFetch(
      `/api/skills/${slug}/artifacts/${name}/versions/${version}/approve`,
      { method: "POST" },
    );
    const data = await parseApiJson(res);
    if (res.ok) {
      onChanged();
      setActionMsg("Approved.");
    } else {
      setActionMsg(apiErrorDetail(data, "Approve failed"));
    }
  }

  async function disable() {
    if (!confirm(`Disable all versions of ${name}?`)) return;
    const res = await apiFetch(`/api/skills/${slug}/artifacts/${name}/disable`, {
      method: "POST",
    });
    if (res.ok) {
      onChanged();
      onClose();
    }
  }

  async function startAiEdit() {
    const prompt = editPrompt.trim();
    if (!prompt || !onStartAiEdit) return;
    setEditBusy(true);
    setActionMsg(null);
    setError(null);
    try {
      const jobId = await editPublishedArtifact(slug, name, version, prompt);
      onStartAiEdit(jobId);
    } catch (e: unknown) {
      setActionMsg(e instanceof Error ? e.message : "AI edit failed");
    } finally {
      setEditBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-4xl max-h-[90vh] flex flex-col border border-slate-200">
        <div className="flex items-start justify-between px-5 py-4 border-b border-slate-100 gap-4">
          <div>
            <h2 className="text-lg font-bold text-slate-900 font-mono">
              {name}
              <span className="text-slate-400 font-sans font-normal"> v{version}</span>
            </h2>
            {artifact?.api_summary && (
              <p className="text-sm text-slate-600 mt-1">{artifact.api_summary}</p>
            )}
          </div>
          <button type="button" onClick={onClose} className="p-2 text-slate-400 hover:text-slate-700 rounded-lg shrink-0">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 overflow-y-auto flex-1 space-y-4">
          {loading ? (
            <div className="flex items-center gap-2 text-sm text-slate-500">
              <Loader2 className="w-4 h-4 animate-spin" /> Loading…
            </div>
          ) : error ? (
            <p className="text-sm text-red-600">{error}</p>
          ) : artifact ? (
            <>
              <div className="flex flex-wrap gap-2 text-[10px] uppercase tracking-wide text-slate-500">
                <span>{artifact.status}</span>
                <span>{artifact.effect_class}</span>
                {artifact.disabled && <span>disabled</span>}
                <span>{artifact.run_count} runs</span>
              </div>
              {artifact.declared_secrets?.length > 0 && (
                <p className="text-xs text-slate-500">
                  Secrets: {artifact.declared_secrets.join(", ")}
                </p>
              )}
              <pre className="text-xs font-mono bg-slate-50 border border-slate-200 rounded-xl p-4 max-h-[55vh] overflow-auto leading-relaxed">
                {artifact.code}
              </pre>
              {canManage && onStartAiEdit && (
                <div className="border border-slate-200 rounded-xl p-4 space-y-2">
                  <button
                    type="button"
                    onClick={() => setShowEditPanel(v => !v)}
                    className="inline-flex items-center gap-1.5 text-xs font-semibold text-slate-800"
                  >
                    <Wand2 className="w-3.5 h-3.5" />
                    {showEditPanel ? "Hide AI edit" : "Edit with AI"}
                  </button>
                  {showEditPanel && (
                    <>
                      <textarea
                        value={editPrompt}
                        onChange={e => setEditPrompt(e.target.value)}
                        placeholder="Describe what to change. Publishing creates a new version."
                        rows={3}
                        maxLength={4000}
                        disabled={editBusy}
                        className="w-full text-sm border border-slate-200 rounded-lg px-3 py-2 resize-y"
                      />
                      <button
                        type="button"
                        disabled={editBusy || !editPrompt.trim()}
                        onClick={startAiEdit}
                        className="inline-flex items-center gap-1.5 text-xs font-semibold px-3 py-1.5 rounded-lg bg-slate-900 text-white disabled:opacity-50"
                      >
                        {editBusy ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin" />
                            Starting edit…
                          </>
                        ) : (
                          "Start AI edit"
                        )}
                      </button>
                    </>
                  )}
                </div>
              )}
            </>
          ) : null}
          {actionMsg && <p className="text-xs text-slate-600">{actionMsg}</p>}
        </div>

        {canManage && artifact && (
          <div className="px-5 py-4 border-t border-slate-100 flex gap-3">
            {artifact.status === "pending_approval" && artifact.effect_class === "write" && (
              <button
                type="button"
                onClick={approve}
                className="text-sm font-semibold text-emerald-700 hover:underline"
              >
                Approve write artifact
              </button>
            )}
            {!artifact.disabled && (
              <button
                type="button"
                onClick={disable}
                className="text-sm font-semibold text-red-600 hover:underline"
              >
                Disable artifact
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
