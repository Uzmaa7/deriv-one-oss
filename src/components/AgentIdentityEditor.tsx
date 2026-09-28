import { useEffect, useRef, useState, type ReactNode } from "react";
import { Bot, Camera, Loader2, Pencil } from "lucide-react";
import { apiFetch } from "@/lib/api";
import { AgentRecord } from "../data/agents";

function avatarSrc(agent: AgentRecord, cacheBust?: number): string | null {
  if (!agent.avatar_url) return null;
  if (cacheBust) return `${agent.avatar_url}${agent.avatar_url.includes("?") ? "&" : "?"}t=${cacheBust}`;
  return agent.avatar_url;
}

export default function AgentIdentityEditor({
  agent,
  onUpdated,
  onError,
  disabled = false,
  children,
}: {
  agent: AgentRecord;
  onUpdated: (updated: AgentRecord) => void;
  onError: (message: string) => void;
  disabled?: boolean;
  children?: ReactNode;
}) {
  const fileRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [savingName, setSavingName] = useState(false);
  const [editingName, setEditingName] = useState(false);
  const [nameDraft, setNameDraft] = useState(agent.name);
  const [cacheBust, setCacheBust] = useState(0);

  useEffect(() => {
    if (!editingName) setNameDraft(agent.name);
  }, [agent.name, editingName]);

  const src = avatarSrc(agent, cacheBust);

  const uploadAvatar = async (file: File) => {
    setUploading(true);
    onError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const res = await apiFetch(`/api/agents/${agent.id}/avatar`, {
        method: "POST",
        body: form,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || `Upload failed (HTTP ${res.status}).`);
      }
      const updated: AgentRecord = await res.json();
      setCacheBust(Date.now());
      onUpdated(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not upload image.");
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const saveName = async () => {
    const trimmed = nameDraft.trim();
    if (!trimmed) {
      onError("Name is required.");
      setNameDraft(agent.name);
      setEditingName(false);
      return;
    }
    if (trimmed === agent.name) {
      setEditingName(false);
      return;
    }
    setSavingName(true);
    onError("");
    try {
      const res = await apiFetch(`/api/agents/${agent.id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: trimmed }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail?.detail || `Update failed (HTTP ${res.status}).`);
      }
      const updated: AgentRecord = await res.json();
      setNameDraft(updated.name);
      setEditingName(false);
      onUpdated(updated);
    } catch (err) {
      onError(err instanceof Error ? err.message : "Could not update name.");
      setNameDraft(agent.name);
    } finally {
      setSavingName(false);
    }
  };

  return (
    <div className="flex items-start gap-3 min-w-0 flex-1">
      <div className="relative shrink-0">
        <button
          type="button"
          disabled={disabled || uploading}
          onClick={() => fileRef.current?.click()}
          className="group relative w-12 h-12 rounded-xl bg-gradient-to-br from-slate-100 to-slate-200 border border-slate-200 flex items-center justify-center overflow-hidden disabled:opacity-60"
          title="Change profile picture"
        >
          {src
            ? <img src={src} alt={agent.name} className="w-12 h-12 object-cover" />
            : <Bot className="w-6 h-6 text-slate-400" />
          }
          <span className="absolute inset-0 bg-slate-900/0 group-hover:bg-slate-900/40 transition-colors flex items-center justify-center">
            {uploading
              ? <Loader2 className="w-4 h-4 text-white animate-spin" />
              : <Camera className="w-4 h-4 text-white opacity-0 group-hover:opacity-100 transition-opacity" />
            }
          </span>
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/jpeg,image/png,image/gif,image/webp"
          className="hidden"
          onChange={e => {
            const file = e.target.files?.[0];
            if (file) void uploadAvatar(file);
          }}
        />
      </div>

      <div className="min-w-0 flex-1">
        {editingName ? (
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={nameDraft}
              onChange={e => setNameDraft(e.target.value)}
              onKeyDown={e => {
                if (e.key === "Enter") void saveName();
                if (e.key === "Escape") {
                  setNameDraft(agent.name);
                  setEditingName(false);
                }
              }}
              onBlur={() => void saveName()}
              maxLength={120}
              autoFocus
              disabled={savingName || disabled}
              className="flex-1 min-w-0 text-xl font-bold text-slate-900 px-2 py-1 border border-slate-300 rounded-lg focus:outline-none focus:border-slate-500"
            />
            {savingName && <Loader2 className="w-4 h-4 animate-spin text-slate-400 shrink-0" />}
          </div>
        ) : (
          <div className="flex items-center gap-1.5 min-w-0">
            <h1 className="text-xl font-bold text-slate-900 truncate">{agent.name}</h1>
            {!disabled && (
              <button
                type="button"
                onClick={() => {
                  setNameDraft(agent.name);
                  setEditingName(true);
                }}
                className="shrink-0 p-1 text-slate-400 hover:text-slate-700 rounded"
                title="Edit name"
              >
                <Pencil className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        )}
        {children}
      </div>
    </div>
  );
}
