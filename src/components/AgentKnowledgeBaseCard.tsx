import { useCallback, useEffect, useRef, useState, type ChangeEvent, type DragEvent } from "react";
import { AlertCircle, CheckCircle2, FileText, Loader2, Trash2, Upload } from "lucide-react";
import { apiFetch } from "@/lib/api";

type KbStatus = "disabled" | "provisioning" | "ready" | "error" | "deprovisioning";
type DocStatus = "pending" | "importing" | "indexed" | "failed";

interface KbLimits {
  max_file_bytes: number;
  max_files_per_request: number;
  max_documents: number;
  max_total_bytes: number;
  allowed_extensions: string[];
}

interface KnowledgeBase {
  enabled: boolean;
  status: KbStatus;
  error_message: string | null;
  limits?: KbLimits;
}

interface KbDocument {
  id: string;
  filename: string;
  size_bytes: number;
  mime_type: string;
  index_status: DocStatus;
  index_error: string | null;
  uploaded_by_email: string;
  created_at: string | null;
  indexed_at: string | null;
}

// Statuses that will change on their own, so the UI keeps polling while any
// row (or the knowledge base itself) is in one of them. Polling only hits our
// own backend — never Vertex — so it costs nothing on the the platform's side.
const KB_TRANSIENT: KbStatus[] = ["provisioning", "deprovisioning"];
const DOC_TRANSIENT: DocStatus[] = ["pending", "importing"];
const POLL_MS = 4000;

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function DocStatusBadge({ doc }: { doc: KbDocument }) {
  if (doc.index_status === "indexed") {
    return (
      <span className="inline-flex items-center gap-1 text-xs font-medium text-emerald-700">
        <CheckCircle2 className="w-3.5 h-3.5" /> Indexed
      </span>
    );
  }
  if (doc.index_status === "failed") {
    return (
      <span
        className="inline-flex items-center gap-1 text-xs font-medium text-red-600"
        title={doc.index_error ?? undefined}
      >
        <AlertCircle className="w-3.5 h-3.5" /> Failed
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-slate-500">
      <Loader2 className="w-3.5 h-3.5 animate-spin" />
      {doc.index_status === "importing" ? "Indexing" : "Queued"}
    </span>
  );
}

export default function AgentKnowledgeBaseCard({
  agentId,
  canEdit,
}: {
  agentId: string;
  canEdit: boolean;
}) {
  const [kb, setKb] = useState<KnowledgeBase | null>(null);
  const [docs, setDocs] = useState<KbDocument[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const load = useCallback(async () => {
    try {
      const kbRes = await apiFetch(`/api/agents/${agentId}/knowledge-base`);
      if (!kbRes.ok) {
        // 403 simply means this viewer may not manage the knowledge base.
        if (kbRes.status === 403) {
          setKb(null);
          return;
        }
        throw new Error(`Could not load knowledge base (HTTP ${kbRes.status}).`);
      }
      const kbData: KnowledgeBase = await kbRes.json();
      setKb(kbData);

      if (kbData.status === "ready") {
        const docRes = await apiFetch(`/api/agents/${agentId}/knowledge-base/documents`);
        if (docRes.ok) setDocs((await docRes.json()).documents ?? []);
      } else {
        setDocs([]);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load knowledge base.");
    } finally {
      setLoading(false);
    }
  }, [agentId]);

  useEffect(() => {
    void load();
  }, [load]);

  // Poll only while something is actually in flight, then stop.
  useEffect(() => {
    const kbBusy = kb ? KB_TRANSIENT.includes(kb.status) : false;
    const docsBusy = docs.some((d) => DOC_TRANSIENT.includes(d.index_status));
    if (!kbBusy && !docsBusy) return;
    const timer = window.setInterval(() => void load(), POLL_MS);
    return () => window.clearInterval(timer);
  }, [kb, docs, load]);

  const setEnabled = async (enable: boolean) => {
    if (!canEdit || busy) return;
    if (!enable) {
      const confirmed = window.confirm(
        "Turning off the knowledge base permanently deletes its document store " +
          "and every uploaded document. This cannot be undone. Continue?",
      );
      if (!confirmed) return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/knowledge-base/${enable ? "enable" : "disable"}`,
        { method: "POST" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          typeof body?.detail === "string" ? body.detail : `Request failed (HTTP ${res.status}).`,
        );
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not update the knowledge base.");
    } finally {
      setBusy(false);
    }
  };

  const uploadFiles = async (files: File[]) => {
    if (!canEdit || uploading || files.length === 0) return;
    const limit = kb?.limits?.max_files_per_request ?? 20;
    if (files.length > limit) {
      setError(`Please upload at most ${limit} files at a time.`);
      return;
    }
    setUploading(true);
    setError(null);
    try {
      const form = new FormData();
      files.forEach((f) => form.append("files", f));
      const res = await apiFetch(`/api/agents/${agentId}/knowledge-base/documents`, {
        method: "POST",
        body: form,
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        const rejected = body?.detail?.rejected as { filename: string; error: string }[] | undefined;
        throw new Error(
          rejected?.length
            ? rejected.map((r) => `${r.filename}: ${r.error}`).join("\n")
            : typeof body?.detail === "string"
              ? body.detail
              : `Upload failed (HTTP ${res.status}).`,
        );
      }
      // A partial success still reports which files were turned away.
      if (body?.rejected?.length) {
        setError(
          (body.rejected as { filename: string; error: string }[])
            .map((r) => `${r.filename}: ${r.error}`)
            .join("\n"),
        );
      }
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Upload failed.");
    } finally {
      setUploading(false);
    }
  };

  const removeDocument = async (doc: KbDocument) => {
    if (!canEdit) return;
    if (!window.confirm(`Remove "${doc.filename}" from the knowledge base?`)) return;
    setError(null);
    try {
      const res = await apiFetch(
        `/api/agents/${agentId}/knowledge-base/documents/${doc.id}`,
        { method: "DELETE" },
      );
      if (!res.ok) throw new Error(`Could not remove the document (HTTP ${res.status}).`);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not remove the document.");
    }
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    void uploadFiles(Array.from(e.dataTransfer.files));
  };

  const onInput = (e: ChangeEvent<HTMLInputElement>) => {
    void uploadFiles(Array.from(e.target.files ?? []));
    e.target.value = "";
  };

  const status = kb?.status ?? "disabled";
  const isOn = !!kb?.enabled && status !== "disabled";
  const accept = (kb?.limits?.allowed_extensions ?? []).join(",");
  const maxFileMb = Math.round((kb?.limits?.max_file_bytes ?? 52428800) / (1024 * 1024));

  return (
    <section className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
      <header>
        <h3 className="text-sm font-bold text-slate-900">Knowledge base</h3>
      </header>

      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <p className="text-sm text-slate-700">
            {isOn
              ? "This agent can search its uploaded documents when answering."
              : "Off — the agent answers without any attached documents."}
          </p>
          <p className="text-xs text-slate-400 mt-1">
            Turning this on creates a private document store for this agent. Nothing is
            created — and nothing is billed — while it stays off.
          </p>
        </div>
        {canEdit ? (
          <button
            type="button"
            role="switch"
            aria-checked={isOn}
            aria-label="Knowledge base"
            disabled={busy || loading || KB_TRANSIENT.includes(status)}
            onClick={() => void setEnabled(!isOn)}
            className={`relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-amber-500 disabled:opacity-50 ${
              isOn ? "bg-amber-500" : "bg-slate-200"
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition ${
                isOn ? "translate-x-5" : "translate-x-0"
              }`}
            />
          </button>
        ) : (
          <span className="text-xs font-medium text-slate-500 shrink-0">
            {isOn ? "Enabled" : "Disabled"}
          </span>
        )}
      </div>

      {status === "provisioning" && (
        <p className="flex items-center gap-2 text-xs text-slate-500">
          <Loader2 className="w-3.5 h-3.5 animate-spin" />
          Setting up the document store — this usually takes a few seconds.
        </p>
      )}
      {status === "deprovisioning" && (
        <p className="flex items-center gap-2 text-xs text-slate-500">
          <Loader2 className="w-3.5 h-3.5 animate-spin" /> Deleting the document store…
        </p>
      )}
      {status === "error" && kb?.error_message && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg p-2">
          {kb.error_message}
        </p>
      )}
      {error && (
        <p className="text-xs text-red-600 bg-red-50 border border-red-100 rounded-lg p-2 whitespace-pre-line">
          {error}
        </p>
      )}

      {status === "ready" && (
        <>
          {canEdit && (
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDragging(true);
              }}
              onDragLeave={() => setDragging(false)}
              onDrop={onDrop}
              onClick={() => inputRef.current?.click()}
              className={`border-2 border-dashed rounded-lg py-6 px-4 flex flex-col items-center justify-center text-center cursor-pointer transition-colors ${
                dragging ? "border-slate-400 bg-slate-100" : "border-slate-200 bg-[#F9FAFB] hover:bg-slate-50"
              } ${uploading ? "opacity-60 pointer-events-none" : ""}`}
            >
              {uploading ? (
                <Loader2 className="w-5 h-5 text-slate-400 mb-1.5 animate-spin" />
              ) : (
                <Upload className="w-5 h-5 text-slate-400 mb-1.5" />
              )}
              <p className="text-sm font-semibold text-slate-600">
                {uploading ? "Uploading…" : "Click to upload or drag & drop"}
              </p>
              <p className="text-xs text-slate-400 mt-0.5">
                {(kb?.limits?.allowed_extensions ?? []).join(", ")} — up to {maxFileMb}MB each
              </p>
              <input
                ref={inputRef}
                type="file"
                multiple
                accept={accept}
                onChange={onInput}
                className="hidden"
              />
            </div>
          )}

          {docs.length === 0 ? (
            <p className="text-xs text-slate-400">
              No documents yet. Uploaded documents become searchable within a few minutes.
            </p>
          ) : (
            <ul className="divide-y divide-slate-100 border border-slate-200 rounded-lg">
              {docs.map((doc) => (
                <li key={doc.id} className="flex items-center gap-3 p-3">
                  <FileText className="w-5 h-5 text-slate-400 shrink-0" />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">{doc.filename}</p>
                    <p className="text-xs text-slate-400">{formatBytes(doc.size_bytes)}</p>
                  </div>
                  <DocStatusBadge doc={doc} />
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => void removeDocument(doc)}
                      className="text-slate-400 hover:text-red-500 transition-colors shrink-0"
                      aria-label={`Remove ${doc.filename}`}
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}

          {kb?.limits && (
            <p className="text-xs text-slate-400">
              {docs.length} of {kb.limits.max_documents} documents used.
            </p>
          )}
        </>
      )}
    </section>
  );
}
