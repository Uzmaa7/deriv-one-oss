import { useState, useRef, useEffect, type ChangeEvent, type DragEvent, type FormEvent } from "react";
import { useNavigate } from "react-router-dom";
import {
  Wand2, Upload, X, FileText, Link, Loader2, CheckCircle2,
  XCircle, Copy, Check, ArrowRight, AlertTriangle, RotateCcw,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

type JobStatus = "pending" | "fetching" | "generating" | "done" | "error";
type CreatorStep = "form" | "generating" | "result";

interface Job {
  job_id: string;
  status: JobStatus;
  status_detail: string | null;
  result_content: string | null;
  error_message: string | null;
}

// ── Status helpers ────────────────────────────────────────────────────────────

const STATUS_MESSAGES: Record<JobStatus, string> = {
  pending: "Queuing job…",
  fetching: "Fetching API reference from URL…",
  generating: "AI is writing your skill…",
  done: "Done!",
  error: "Something went wrong.",
};

const TERMINAL: JobStatus[] = ["done", "error"];

const ALLOWED_API_REF_EXTENSIONS = [".md", ".txt", ".json", ".html"];

function isAllowedApiRefFile(file: File): boolean {
  const lower = file.name.toLowerCase();
  return ALLOWED_API_REF_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

// ── File drop zone for API reference ─────────────────────────────────────────

function ApiRefDropzone({
  file,
  onFile,
}: {
  file: File | null;
  onFile: (f: File | null) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const accept = (f: File) => {
    if (!isAllowedApiRefFile(f)) {
      alert("Only .md, .txt, .json, or .html files are allowed.");
      return;
    }
    if (f.size > 512 * 1024) {
      alert("File must be smaller than 512 KB.");
      return;
    }
    onFile(f);
  };

  const onDrop = (e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragging(false);
    const dropped = e.dataTransfer.files[0];
    if (dropped) accept(dropped);
  };

  const onInput = (e: ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files?.[0];
    if (selected) accept(selected);
    e.target.value = "";
  };

  if (file) {
    return (
      <div className="flex items-center gap-3 border border-slate-200 rounded-lg p-3 bg-[#F9FAFB]">
        <FileText className="w-6 h-6 text-slate-400 flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <p className="text-sm font-semibold text-slate-800 truncate">{file.name}</p>
          <p className="text-xs text-slate-500">{(file.size / 1024).toFixed(1)} KB</p>
        </div>
        <button
          type="button"
          onClick={() => onFile(null)}
          className="text-slate-400 hover:text-red-500 transition-colors"
          aria-label="Remove file"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
    );
  }

  return (
    <div
      onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={onDrop}
      onClick={() => inputRef.current?.click()}
      className={`border-2 border-dashed rounded-lg py-6 px-4 flex flex-col items-center justify-center text-center cursor-pointer transition-colors ${
        dragging ? "border-slate-400 bg-slate-100" : "border-slate-200 bg-[#F9FAFB] hover:bg-slate-50"
      }`}
    >
      <Upload className="w-5 h-5 text-slate-400 mb-1.5" />
      <p className="text-sm font-semibold text-slate-600">Click to upload or drag &amp; drop</p>
      <p className="text-xs text-slate-400 mt-0.5">Markdown, text, JSON, HTML — up to 512 KB</p>
      <input
        ref={inputRef}
        type="file"
        accept=".md,.txt,.json,.html,text/markdown,text/plain,application/json,text/html"
        onChange={onInput}
        className="hidden"
      />
    </div>
  );
}

// ── Generating progress view ──────────────────────────────────────────────────

function GeneratingView({ job }: { job: Job | null }) {
  const steps: { status: JobStatus; label: string }[] = [
    { status: "fetching", label: "Fetching API reference" },
    { status: "generating", label: "Generating SKILL.md with AI" },
    { status: "done", label: "Skill ready" },
  ];

  const currentIdx = job
    ? steps.findIndex((s) => s.status === job.status || (job.status === "done" && s.status === "done"))
    : -1;

  return (
    <div className="flex flex-col items-center justify-center py-16 px-8 text-center">
      <div className="w-16 h-16 rounded-2xl bg-slate-900 flex items-center justify-center mb-6">
        <Wand2 className="w-7 h-7 text-white animate-pulse" />
      </div>
      <h2 className="text-xl font-bold text-slate-900 mb-1">Creating your skill…</h2>
      <p className="text-sm text-slate-500 mb-8 max-w-sm">
        {job?.status_detail || "Hang tight while the AI writes your SKILL.md."}
      </p>

      <div className="w-full max-w-sm space-y-3">
        {steps.map((step, i) => {
          const isDone = job?.status === "done" || i < currentIdx;
          const isCurrent = step.status === job?.status;
          const isPending = i > currentIdx;

          return (
            <div
              key={step.status}
              className={`flex items-center gap-3 rounded-xl px-4 py-3 border transition-colors ${
                isDone
                  ? "bg-emerald-50 border-emerald-200"
                  : isCurrent
                  ? "bg-blue-50 border-blue-200"
                  : "bg-[#F9FAFB] border-slate-200"
              }`}
            >
              {isDone ? (
                <CheckCircle2 className="w-4 h-4 text-emerald-600 flex-shrink-0" />
              ) : isCurrent ? (
                <Loader2 className="w-4 h-4 text-blue-500 animate-spin flex-shrink-0" />
              ) : (
                <div className="w-4 h-4 rounded-full border-2 border-slate-300 flex-shrink-0" />
              )}
              <span
                className={`text-sm font-medium ${
                  isDone ? "text-emerald-800" : isCurrent ? "text-blue-800" : "text-slate-400"
                }`}
              >
                {step.label}
              </span>
            </div>
          );
        })}
      </div>

      {job?.status === "error" && (
        <div className="mt-6 flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-xl px-4 py-3 max-w-sm">
          <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
          <span>{job.error_message || "An unexpected error occurred."}</span>
        </div>
      )}
    </div>
  );
}

// ── Result view ───────────────────────────────────────────────────────────────

function ResultView({
  content,
  onChange,
  onPublish,
  onReset,
}: {
  content: string;
  onChange: (v: string) => void;
  onPublish: () => void;
  onReset: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="flex flex-col h-full min-h-0">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-6 py-3 bg-white border-b border-slate-200 shrink-0 gap-3 flex-wrap">
        <div className="flex items-center gap-2">
          <CheckCircle2 className="w-4 h-4 text-emerald-600" />
          <span className="text-sm font-semibold text-slate-800">Skill generated — review &amp; edit below</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={onReset}
            className="flex items-center gap-1.5 text-xs font-semibold text-slate-500 hover:text-slate-900 transition-colors px-3 py-1.5 rounded-md hover:bg-slate-100"
          >
            <RotateCcw className="w-3.5 h-3.5" />
            Start over
          </button>
          <button
            type="button"
            onClick={copy}
            className="flex items-center gap-1.5 text-xs font-semibold text-slate-600 hover:text-slate-900 transition-colors px-3 py-1.5 rounded-md border border-slate-200 hover:bg-slate-50"
          >
            {copied ? <Check className="w-3.5 h-3.5 text-emerald-600" /> : <Copy className="w-3.5 h-3.5" />}
            {copied ? "Copied!" : "Copy"}
          </button>
          <button
            type="button"
            onClick={onPublish}
            className="flex items-center gap-1.5 text-xs font-bold bg-slate-900 text-white px-3 py-1.5 rounded-md hover:bg-slate-800 transition-colors"
          >
            Publish Skill
            <ArrowRight className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Editable textarea */}
      <div className="flex-1 min-h-0 p-4">
        <textarea
          value={content}
          onChange={(e) => onChange(e.target.value)}
          spellCheck={false}
          className="w-full h-full resize-none bg-[#F9FAFB] border border-slate-200 rounded-xl p-4 text-sm font-mono text-slate-800 focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors leading-relaxed"
          placeholder="Your SKILL.md will appear here…"
        />
      </div>

      <div className="px-6 pb-4 text-xs text-slate-400 shrink-0">
        Edit freely. When satisfied, click <strong>Publish Skill</strong> — your file will be
        auto-uploaded to the publish form.
      </div>
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function CreateSkillPage() {
  const navigate = useNavigate();

  // ── Form state ─────────────────────────────────────────────────────────────
  const [skillName, setSkillName] = useState("");
  const [description, setDescription] = useState("");
  const [apiRefUrl, setApiRefUrl] = useState("");
  const [apiRefFile, setApiRefFile] = useState<File | null>(null);
  const [activeRefTab, setActiveRefTab] = useState<"url" | "file">("url");

  // ── Job state ──────────────────────────────────────────────────────────────
  const [step, setStep] = useState<CreatorStep>("form");
  const [job, setJob] = useState<Job | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // ── Result state ───────────────────────────────────────────────────────────
  const [resultContent, setResultContent] = useState("");

  // ── Poll for job completion ────────────────────────────────────────────────
  useEffect(() => {
    if (!job || TERMINAL.includes(job.status)) return;

    const timer = setInterval(async () => {
      try {
        const res = await apiFetch(`/api/skills/create-job/${job.job_id}`);
        if (!res.ok) return;
        const data: Job = await res.json();
        setJob(data);
        if (TERMINAL.includes(data.status)) {
          clearInterval(timer);
          if (data.status === "done" && data.result_content) {
            setResultContent(data.result_content);
            setStep("result");
          }
        }
      } catch {
        // silently retry
      }
    }, 2500);

    return () => clearInterval(timer);
  }, [job]);

  // ── Submit ─────────────────────────────────────────────────────────────────
  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitError(null);

    if (!skillName.trim()) {
      setSubmitError("Skill name is required.");
      return;
    }
    if (activeRefTab === "url" && apiRefUrl.trim() && !/^https?:\/\//i.test(apiRefUrl.trim())) {
      setSubmitError("API reference URL must start with http:// or https://");
      return;
    }

    setSubmitting(true);
    try {
      const form = new FormData();
      form.append("skill_name", skillName.trim());
      form.append("description", description.trim());
      form.append("api_ref_url", activeRefTab === "url" ? apiRefUrl.trim() : "");
      if (activeRefTab === "file" && apiRefFile) {
        form.append("api_ref_file", apiRefFile, apiRefFile.name);
      }

      const res = await apiFetch("/api/skills/create-job", { method: "POST", body: form });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data?.detail || `Request failed (${res.status})`);
      }

      const data: Job = await res.json();
      setJob(data);
      setStep("generating");
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : "An unexpected error occurred.");
    } finally {
      setSubmitting(false);
    }
  };

  // ── Publish: navigate to /publish with skill content in state ─────────────
  const handlePublish = () => {
    navigate("/publish", {
      state: {
        preloadedSkillContent: resultContent,
        preloadedSkillName: skillName.trim(),
      },
    });
  };

  const handleReset = () => {
    setStep("form");
    setJob(null);
    setResultContent("");
    setSubmitError(null);
  };

  // ── Render ─────────────────────────────────────────────────────────────────

  if (step === "generating") {
    return (
      <div className="flex-1 bg-[#F9FAFB] overflow-y-auto">
        <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8">
          <GeneratingView job={job} />
          {job?.status === "error" && (
            <div className="flex justify-center pb-10">
              <button
                type="button"
                onClick={handleReset}
                className="flex items-center gap-2 text-sm font-semibold text-slate-600 hover:text-slate-900 transition-colors"
              >
                <RotateCcw className="w-4 h-4" />
                Try again
              </button>
            </div>
          )}
        </div>
      </div>
    );
  }

  if (step === "result") {
    return (
      <div className="flex-1 flex flex-col overflow-hidden bg-[#F9FAFB]">
        <ResultView
          content={resultContent}
          onChange={setResultContent}
          onPublish={handlePublish}
          onReset={handleReset}
        />
      </div>
    );
  }

  // ── Form step ──────────────────────────────────────────────────────────────
  return (
    <div className="flex-1 bg-[#F9FAFB] overflow-y-auto py-12">
      <div className="max-w-2xl mx-auto px-4 sm:px-6 lg:px-8 space-y-8">

        {/* Header */}
        <div>
          <div className="flex items-center gap-3 mb-2">
            <div className="w-9 h-9 rounded-xl bg-slate-900 flex items-center justify-center">
              <Wand2 className="w-4 h-4 text-white" />
            </div>
            <h1 className="text-2xl font-bold text-slate-900">AI Skill Creator</h1>
          </div>
          <p className="text-slate-500 text-sm">
            Describe the skill you want to build. Provide an API reference and the AI will write
            a complete, production-ready SKILL.md you can edit and publish.
          </p>
        </div>

        {/* Form card */}
        <form
          onSubmit={handleSubmit}
          className="bg-white border border-slate-200 rounded-xl shadow-sm divide-y divide-slate-100"
        >
          {/* Skill name + description */}
          <section className="p-6 space-y-4">
            <h2 className="text-base font-bold text-slate-900">Skill details</h2>

            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">
                Skill Name <span className="text-red-500">*</span>
              </label>
              <input
                type="text"
                required
                maxLength={200}
                value={skillName}
                onChange={(e) => setSkillName(e.target.value)}
                placeholder="e.g. IAM BackOffice API, Datadog Metrics, GitHub Actions"
                className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
              />
            </div>

            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">
                Description{" "}
                <span className="font-normal text-slate-400">(optional)</span>
              </label>
              <textarea
                maxLength={2000}
                rows={2}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="What should this skill do? What data or actions does it expose?"
                className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400 resize-none"
              />
            </div>
          </section>

          {/* API reference */}
          <section className="p-6 space-y-3">
            <div>
              <h2 className="text-base font-bold text-slate-900">API Reference</h2>
              <p className="text-xs text-slate-500 mt-1">
                Give the AI documentation to work from. Providing this significantly improves
                accuracy. You can paste a URL or upload a file. Both are optional.
              </p>
            </div>

            {/* Tab toggle */}
            <div className="inline-flex rounded-lg border border-slate-200 overflow-hidden">
              <button
                type="button"
                onClick={() => setActiveRefTab("url")}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold transition-colors ${
                  activeRefTab === "url"
                    ? "bg-slate-900 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                <Link className="w-3 h-3" />
                URL
              </button>
              <button
                type="button"
                onClick={() => setActiveRefTab("file")}
                className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold transition-colors ${
                  activeRefTab === "file"
                    ? "bg-slate-900 text-white"
                    : "bg-white text-slate-600 hover:bg-slate-50"
                }`}
              >
                <FileText className="w-3 h-3" />
                File
              </button>
            </div>

            {activeRefTab === "url" ? (
              <input
                type="url"
                value={apiRefUrl}
                onChange={(e) => setApiRefUrl(e.target.value)}
                placeholder="https://docs.example.com/api-reference"
                className="w-full bg-[#F9FAFB] border border-slate-200 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-slate-900 focus:bg-white transition-colors placeholder:text-slate-400"
              />
            ) : (
              <ApiRefDropzone file={apiRefFile} onFile={setApiRefFile} />
            )}

            <p className="text-xs text-slate-400 flex items-center gap-1">
              <AlertTriangle className="w-3 h-3 flex-shrink-0" />
              If both are provided (URL + file), both are passed as context.
            </p>
          </section>

          {/* Submit */}
          <section className="p-6 space-y-3">
            {submitError && (
              <div className="flex items-start gap-2 text-sm text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2.5">
                <XCircle className="w-4 h-4 mt-0.5 flex-shrink-0" />
                <span>{submitError}</span>
              </div>
            )}
            <button
              type="submit"
              disabled={submitting || !skillName.trim()}
              className="w-full flex items-center justify-center gap-2 bg-slate-900 hover:bg-slate-800 disabled:opacity-50 disabled:cursor-not-allowed text-white font-bold py-2.5 rounded-md transition-colors text-sm"
            >
              {submitting ? (
                <>
                  <Loader2 className="w-4 h-4 animate-spin" />
                  Starting…
                </>
              ) : (
                <>
                  <Wand2 className="w-4 h-4" />
                  Generate Skill
                </>
              )}
            </button>
          </section>
        </form>

        {/* How it works */}
        <div className="bg-white border border-slate-200 rounded-xl p-5 space-y-3">
          <h3 className="text-sm font-bold text-slate-800">How it works</h3>
          <ol className="space-y-2 text-sm text-slate-600">
            {[
              "Provide a skill name and optionally a description and API reference.",
              "The AI fetches the API reference (if a URL) and reads any uploaded file.",
              "Claude generates a complete SKILL.md with frontmatter, usage patterns, and code examples.",
              "Review and edit the generated skill in the built-in editor.",
              "Click Publish Skill to go straight to the publish flow — your file is auto-uploaded.",
            ].map((step, i) => (
              <li key={i} className="flex items-start gap-2.5">
                <span className="flex-shrink-0 w-5 h-5 rounded-full bg-slate-100 text-slate-600 text-xs font-bold flex items-center justify-center mt-0.5">
                  {i + 1}
                </span>
                {step}
              </li>
            ))}
          </ol>
        </div>

      </div>
    </div>
  );
}
