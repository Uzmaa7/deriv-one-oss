import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type RefObject,
} from "react";
import React from "react";
import { NavLink, useNavigate, useParams } from "react-router-dom";
import {
  AlertCircle,
  ArrowLeft,
  Check,
  Code2,
  Download,
  ChevronLeft,
  ChevronRight,
  ExternalLink,
  Eye,
  EyeOff,
  FileCode2,
  FlaskConical,
  Globe,
  KeyRound,
  Loader2,
  Lock,
  Monitor,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings2,
  Share2,
  Sparkles,
  Table2,
  Trash2,
  Users,
  X,
  Zap,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

// ── Types ─────────────────────────────────────────────────────────────────────

interface AppRecord {
  id: string;
  slug: string;
  name: string;
  description: string | null;
  visibility: "private" | "shared" | "workspace";
  allowed_users: string[];
  admin_users?: string[];
  status: "draft" | "deployed" | "error" | "archived";
  mode?: "standard" | "overnight";
  tech_stack: "static" | "python" | "react";
  lab_mode?: "plan" | "agent";
  plan_ready?: boolean;
  plan_implemented?: boolean;
  build_plan?: {
    summary?: string;
    features?: Array<{ title: string; description: string; files?: string[] }>;
    open_questions?: string[];
  };
  classification_confidence?: "low" | "medium" | "high";
  classification_meta?: { gws_mode?: boolean; [key: string]: unknown };
  file_manifest: string[];
  chat_history: Array<{
    role: "user" | "assistant";
    content: string;
    file_edits?: FileEdit[];
    content_blocks?: ContentBlockApi[];
  }>;
  skill_ids: string[];
  env_var_keys: string[];
  skill_env_keys: string[];
  creator_email: string;
  updated_at: string;
  build_phase?: "building" | "completed" | "failed" | null;
  build_updated_at?: string | null;
}

interface FileEditLine {
  type: "add" | "del";
  text: string;
}

interface FileEdit {
  path: string;
  kind: "write" | "patch";
  additions: number;
  deletions: number;
  preview?: FileEditLine[];
}

type ChatBlock =
  | { type: "text"; content: string }
  | { type: "file_edit"; edit: FileEdit }
  | { type: "warning"; content: string }
  | { type: "error"; content: string };

interface ContentBlockApi {
  type: string;
  content?: string;
  path?: string;
  kind?: string;
  additions?: number;
  deletions?: number;
  preview?: FileEditLine[];
}

interface BuildTimelineEntry {
  event: string;
  path?: string;
  kind?: string;
  additions?: number;
  deletions?: number;
  preview?: FileEditLine[];
  delta?: string;
  message?: string;
}

interface BuildStatus {
  phase: "building" | "completed" | "failed" | null;
  assistant_preview: string;
  files_written: string[];
  error_message?: string | null;
  chat_history: Array<{
    role: "user" | "assistant";
    content: string;
    file_edits?: FileEdit[];
    content_blocks?: ContentBlockApi[];
  }>;
  lab_mode?: "plan" | "agent";
  plan_ready?: boolean;
  timeline?: BuildTimelineEntry[];
}

interface SkillOption {
  slug: string;
  tier: string;
}

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  filesWritten?: string[];
  fileEdits?: FileEdit[];
  blocks?: ChatBlock[];
}

// ── Helpers ───────────────────────────────────────────────────────────────────

function mapContentBlocks(raw: ContentBlockApi[] | undefined): ChatBlock[] {
  if (!raw?.length) return [];
  const blocks: ChatBlock[] = [];
  for (const b of raw) {
    if (b.type === "text" && b.content?.trim()) {
      blocks.push({ type: "text", content: b.content });
    } else if (b.type === "file_edit" && b.path) {
      blocks.push({
        type: "file_edit",
        edit: {
          path: b.path,
          kind: (b.kind as FileEdit["kind"]) || "write",
          additions: b.additions ?? 0,
          deletions: b.deletions ?? 0,
          preview: b.preview,
        },
      });
    } else if (b.type === "warning" && b.content) {
      blocks.push({ type: "warning", content: b.content });
    } else if (b.type === "error" && b.content) {
      blocks.push({ type: "error", content: b.content });
    }
  }
  return blocks;
}

function blocksFromTimeline(timeline: BuildTimelineEntry[] | undefined): ChatBlock[] {
  if (!timeline?.length) return [];
  const blocks: ChatBlock[] = [];
  let textBuf = "";

  const flushText = () => {
    const combined = textBuf.trim();
    textBuf = "";
    if (combined) blocks.push({ type: "text", content: combined });
  };

  for (const entry of timeline) {
    if (entry.event === "text" && entry.delta) {
      textBuf += entry.delta;
    } else if (entry.event === "file_edit" && entry.path) {
      flushText();
      blocks.push({
        type: "file_edit",
        edit: {
          path: entry.path,
          kind: (entry.kind as FileEdit["kind"]) || "write",
          additions: entry.additions ?? 0,
          deletions: entry.deletions ?? 0,
          preview: entry.preview,
        },
      });
    } else if (entry.event === "verify_fail") {
      flushText();
      const errors = (entry as { errors?: string[] }).errors;
      if (errors?.length) {
        blocks.push({
          type: "warning",
          content: `Verification failed after build. Fix these issues:\n${errors.map(e => `- ${e}`).join("\n")}`,
        });
      }
    } else if (entry.event === "warning" && entry.message) {
      flushText();
      blocks.push({ type: "warning", content: entry.message });
    } else if (entry.event === "error" && entry.message) {
      flushText();
      blocks.push({ type: "error", content: entry.message });
    }
  }
  flushText();
  return blocks;
}

function blocksForMessage(msg: ChatMessage): ChatBlock[] {
  if (msg.blocks?.length) return msg.blocks;
  const fallback: ChatBlock[] = [];
  if (msg.content.trim()) fallback.push({ type: "text", content: msg.content });
  if (msg.fileEdits?.length) {
    for (const edit of msg.fileEdits) fallback.push({ type: "file_edit", edit });
  }
  return fallback;
}

function mapChatHistory(
  history: Array<{
    role: "user" | "assistant";
    content: string;
    file_edits?: FileEdit[];
    content_blocks?: ContentBlockApi[];
  }>,
): ChatMessage[] {
  return history.map(m => ({
    role: m.role,
    content: m.content,
    fileEdits: m.file_edits,
    blocks: mapContentBlocks(m.content_blocks),
  }));
}

function basename(path: string): string {
  const parts = path.split("/");
  return parts[parts.length - 1] || path;
}

function buildFileTree(paths: string[]): Record<string, string | Record<string, unknown>> {
  const root: Record<string, string | Record<string, unknown>> = {};
  for (const p of [...paths].sort()) {
    const parts = p.split("/");
    let node = root;
    parts.forEach((part, idx) => {
      if (idx === parts.length - 1) {
        node[part] = p;
      } else {
        if (typeof node[part] !== "object" || node[part] === null || typeof node[part] === "string") {
          node[part] = {};
        }
        node = node[part] as Record<string, string | Record<string, unknown>>;
      }
    });
  }
  return root;
}

function getLanguage(filename: string): string {
  const ext = filename.split(".").pop()?.toLowerCase() ?? "";
  const map: Record<string, string> = {
    html: "html", htm: "html", css: "css", js: "javascript",
    mjs: "javascript", ts: "typescript", py: "python",
    json: "json", svg: "svg", md: "markdown",
  };
  return map[ext] ?? "text";
}

function renderInline(text: string): React.ReactNode[] {
  const parts: React.ReactNode[] = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*]+\*|_[^_]+_)/g;
  let last = 0, m: RegExpExecArray | null;
  let idx = 0;
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) parts.push(text.slice(last, m.index));
    const tok = m[0];
    if (tok.startsWith("`")) {
      parts.push(<code key={idx++} className="bg-slate-100 text-[#FF444F] font-mono text-[11px] px-1 py-0.5 rounded break-all">{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith("**") || tok.startsWith("__")) {
      parts.push(<strong key={idx++} className="font-semibold text-slate-800">{tok.slice(2, -2)}</strong>);
    } else {
      parts.push(<em key={idx++} className="italic">{tok.slice(1, -1)}</em>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

function MarkdownMessage({ content }: { content: string }) {
  const lines = content.split("\n");
  const nodes: React.ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Fenced code block
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      nodes.push(
        <pre key={i} className="bg-[#0d1117] text-[#e6edf3] text-[11px] font-mono rounded-lg p-3 overflow-x-auto my-2 leading-relaxed whitespace-pre">
          {lang && <span className="block text-[10px] text-slate-500 mb-1 font-sans">{lang}</span>}
          {codeLines.join("\n")}
        </pre>
      );
      i++;
      continue;
    }

    // Horizontal rule
    if (/^[-*_]{3,}$/.test(line.trim())) {
      nodes.push(<hr key={i} className="border-slate-200 my-2" />);
      i++;
      continue;
    }

    // Headings
    const hm = line.match(/^(#{1,4})\s+(.*)/);
    if (hm) {
      const level = hm[1].length;
      const cls = [
        "text-base font-bold text-slate-900 mt-3 mb-1",
        "text-sm font-bold text-slate-800 mt-2 mb-1",
        "text-xs font-bold text-slate-700 mt-2 mb-0.5 uppercase tracking-wide",
        "text-xs font-semibold text-slate-600 mt-1.5",
      ][level - 1];
      nodes.push(<p key={i} className={cls}>{renderInline(hm[2])}</p>);
      i++;
      continue;
    }

    // Table — collect all consecutive pipe lines
    if (line.includes("|")) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].includes("|")) {
        tableLines.push(lines[i]);
        i++;
      }
      // parse header / separator / rows
      const rows = tableLines.filter(l => !/^\s*\|?[-:| ]+\|?\s*$/.test(l));
      if (rows.length >= 1) {
        const parseRow = (r: string) =>
          r.split("|").map(c => c.trim()).filter((_, ci, arr) => ci > 0 && ci < arr.length - 1 || (arr[0] !== "" || arr[arr.length - 1] !== ""));
        const header = parseRow(rows[0]);
        const body = rows.slice(1);
        nodes.push(
          <div key={i} className="overflow-x-auto my-2">
            <table className="text-[11px] w-full border-collapse">
              <thead>
                <tr className="bg-slate-100">
                  {header.map((h, ci) => (
                    <th key={ci} className="border border-slate-200 px-2 py-1 text-left font-semibold text-slate-700 whitespace-nowrap">{renderInline(h)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((r, ri) => (
                  <tr key={ri} className={ri % 2 === 0 ? "bg-surface" : "bg-slate-50"}>
                    {parseRow(r).map((cell, ci) => (
                      <td key={ci} className="border border-slate-200 px-2 py-1 text-slate-600">{renderInline(cell)}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }
      continue;
    }

    // Unordered list item
    const ulm = line.match(/^(\s*)[-*+]\s+(.*)/);
    if (ulm) {
      const indent = Math.floor(ulm[1].length / 2);
      nodes.push(
        <div key={i} className="flex items-start gap-1.5 leading-relaxed" style={{ paddingLeft: `${indent * 12}px` }}>
          <span className="mt-1.5 w-1.5 h-1.5 rounded-full bg-[#FF444F]/60 shrink-0" />
          <span className="text-xs text-slate-700">{renderInline(ulm[2])}</span>
        </div>
      );
      i++;
      continue;
    }

    // Ordered list item
    const olm = line.match(/^(\s*)\d+[.)]\s+(.*)/);
    if (olm) {
      const num = line.match(/\d+/)?.[0] ?? "1";
      nodes.push(
        <div key={i} className="flex items-start gap-1.5 leading-relaxed">
          <span className="text-[10px] font-bold text-[#FF444F] shrink-0 mt-0.5 w-4 text-right">{num}.</span>
          <span className="text-xs text-slate-700">{renderInline(olm[2])}</span>
        </div>
      );
      i++;
      continue;
    }

    // Blank line
    if (line.trim() === "") {
      nodes.push(<div key={i} className="h-2" />);
      i++;
      continue;
    }

    // Normal paragraph
    nodes.push(
      <p key={i} className="text-xs text-slate-700 leading-relaxed break-words [overflow-wrap:anywhere]">{renderInline(line)}</p>
    );
    i++;
  }

  return <div className="space-y-0.5 break-words [overflow-wrap:anywhere]">{nodes}</div>;
}

function CodeView({ content, filename }: { content: string; filename?: string }) {
  const lines = content.split("\n");
  return (
    <div className="flex flex-col h-full min-h-0">
      {filename && (
        <div className="px-3 py-1.5 border-b border-slate-700/80 bg-[#161b22] shrink-0 flex items-center gap-2">
          <FileCode2 className="w-3.5 h-3.5 text-slate-400 shrink-0" />
          <span className="text-[11px] font-mono text-slate-300 truncate">{filename}</span>
          <span className="text-[10px] text-slate-500 ml-auto">{lines.length} lines</span>
        </div>
      )}
      <div className="flex-1 overflow-auto min-h-0">
        <table className="w-full border-collapse text-xs font-mono">
          <tbody>
            {lines.map((line, i) => (
              <tr key={i} className="hover:bg-white/[0.03]">
                <td className="select-none text-right pr-3 pl-2 py-0 text-slate-600 w-10 align-top border-r border-slate-800/80">
                  {i + 1}
                </td>
                <td className="pl-3 pr-4 py-0 text-[#e6edf3] whitespace-pre-wrap break-words align-top">
                  {line || " "}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function FileEditSummary({ edit }: { edit: FileEdit }) {
  const [open, setOpen] = useState(true);
  return (
    <div className="rounded-lg border border-slate-200 bg-white overflow-hidden text-left w-full">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 bg-slate-50 hover:bg-slate-100 transition-colors border-b border-slate-100"
      >
        <FileCode2 className="w-3 h-3 text-slate-400 shrink-0" />
        <span className="text-[11px] font-mono text-slate-700 truncate flex-1 text-left">
          {basename(edit.path)}
        </span>
        {edit.additions > 0 && (
          <span className="text-[10px] font-semibold text-emerald-600">+{edit.additions}</span>
        )}
        {edit.deletions > 0 && (
          <span className="text-[10px] font-semibold text-red-500">-{edit.deletions}</span>
        )}
        <span className="text-[9px] text-slate-400 uppercase">{edit.kind}</span>
      </button>
      {open && edit.preview && edit.preview.length > 0 && (
        <div className="max-h-40 overflow-auto bg-[#0d1117] text-[10px] font-mono leading-relaxed">
          {edit.preview.map((line, i) => (
            <div
              key={i}
              className={`px-2 py-0.5 whitespace-pre-wrap break-all ${
                line.type === "add"
                  ? "bg-emerald-950/50 text-emerald-300"
                  : "bg-red-950/40 text-red-300"
              }`}
            >
              <span className="opacity-60 mr-1">{line.type === "add" ? "+" : "−"}</span>
              {line.text}
            </div>
          ))}
        </div>
      )}
      <div className="px-2.5 py-1 text-[9px] text-slate-400 font-mono truncate border-t border-slate-100">
        {edit.path}
      </div>
    </div>
  );
}

function AssistantTurnBlocks({
  blocks,
  showCursor,
}: {
  blocks: ChatBlock[];
  showCursor?: boolean;
}) {
  if (!blocks.length) return null;
  return (
    <div className="flex flex-col gap-1.5 max-w-[95%] w-full">
      {blocks.map((block, i) => {
        const isLast = i === blocks.length - 1;
        if (block.type === "text") {
          return (
            <div
              key={`text-${i}`}
              className="bg-slate-50 border border-slate-200 rounded-xl px-3 py-2.5 w-full break-words [overflow-wrap:anywhere]"
            >
              <MarkdownMessage content={block.content} />
              {showCursor && isLast && (
                <span className="inline-block w-1.5 h-3 bg-[#FF444F] animate-pulse ml-0.5 rounded-sm align-middle" />
              )}
            </div>
          );
        }
        if (block.type === "file_edit") {
          return (
            <div key={`edit-${i}-${block.edit.path}`} className="w-full">
              <FileEditSummary edit={block.edit} />
            </div>
          );
        }
        if (block.type === "warning") {
          return (
            <div
              key={`warn-${i}`}
              className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 text-xs text-amber-900 whitespace-pre-wrap break-words [overflow-wrap:anywhere] w-full"
            >
              {block.content}
            </div>
          );
        }
        return (
          <div
            key={`err-${i}`}
            className="bg-red-50 border border-red-200 rounded-xl px-3 py-2.5 text-xs text-red-800 whitespace-pre-wrap break-words [overflow-wrap:anywhere] w-full"
          >
            {block.content}
          </div>
        );
      })}
    </div>
  );
}

function StatusChip({ status }: { status: AppRecord["status"] }) {
  const map: Record<string, string> = {
    draft: "bg-slate-100 text-slate-500",
    deployed: "bg-emerald-50 text-emerald-700",
    error: "bg-red-50 text-red-700",
    archived: "bg-slate-100 text-slate-400",
  };
  return (
    <span className={`inline-flex items-center text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full ${map[status] ?? map.draft}`}>
      {status}
    </span>
  );
}

function StackBadge({ stack }: { stack: AppRecord["tech_stack"] }) {
  const isReact = stack === "react";
  return (
    <span
      title={isReact ? "React + Vite multi-page app" : "Vanilla HTML/JS + Python backend"}
      className={`inline-flex items-center text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded-full border ${
        isReact
          ? "bg-blue-50 text-blue-700 border-blue-200"
          : "bg-slate-100 text-slate-600 border-slate-200"
      }`}
    >
      {isReact ? "React" : "Vanilla"}
    </span>
  );
}

function LabModeToggle({
  labMode,
  planReady,
  disabled,
  onChange,
}: {
  labMode: "plan" | "agent";
  planReady?: boolean;
  disabled?: boolean;
  onChange: (mode: "plan" | "agent") => void;
}) {
  return (
    <div className="flex items-center bg-slate-100 rounded-lg p-0.5 gap-0.5">
      <button
        type="button"
        disabled={disabled}
        onClick={() => onChange("plan")}
        className={`text-[10px] px-2 py-1 rounded-md font-semibold transition-colors ${
          labMode === "plan"
            ? "bg-white text-slate-900 shadow-sm"
            : "text-slate-500 hover:text-slate-700"
        }`}
      >
        Plan
      </button>
      <button
        type="button"
        disabled={disabled || (!planReady && labMode !== "agent")}
        title={!planReady ? "Complete the plan before switching to Agent mode" : "Agent mode — writes code"}
        onClick={() => onChange("agent")}
        className={`text-[10px] px-2 py-1 rounded-md font-semibold transition-colors ${
          labMode === "agent"
            ? "bg-white text-[#FF444F] shadow-sm"
            : planReady
              ? "text-slate-500 hover:text-slate-700"
              : "text-slate-300 cursor-not-allowed"
        }`}
      >
        Agent
      </button>
    </div>
  );
}

function BuildConfigChips({ app }: { app: AppRecord }) {
  const confidence = app.classification_confidence;
  const confidenceTitle =
    confidence === "low"
      ? "Classifier confidence: low — safe defaults were used"
      : confidence === "medium"
        ? "Classifier confidence: medium"
        : confidence === "high"
          ? "Classifier confidence: high"
          : undefined;

  return (
    <div
      className="flex items-center gap-1.5 flex-wrap"
      title="AI-inferred build settings"
    >
      <StackBadge stack={app.tech_stack} />
      {confidence && (
        <span
          title={confidenceTitle}
          className={`inline-flex items-center text-[10px] font-medium rounded-full px-2 py-0.5 border ${
            confidence === "high"
              ? "bg-emerald-50 text-emerald-700 border-emerald-200"
              : confidence === "medium"
                ? "bg-amber-50 text-amber-700 border-amber-200"
                : "bg-slate-100 text-slate-500 border-slate-200"
          }`}
        >
          {confidence} confidence
        </span>
      )}
    </div>
  );
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function LabPage() {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [app, setApp] = useState<AppRecord | null>(null);
  const [loading, setLoading] = useState(true);
  const [appError, setAppError] = useState<string | null>(null);

  // Chat
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [streamingBlocks, setStreamingBlocks] = useState<ChatBlock[]>([]);
  const [streamingFiles, setStreamingFiles] = useState<string[]>([]);
  const [buildPhase, setBuildPhase] = useState<BuildStatus["phase"]>(null);
  const chatEndRef = useRef<HTMLDivElement>(null);
  const autoStartedRef = useRef<string | null>(null);
  const prevBuildPhaseRef = useRef<BuildStatus["phase"]>(null);

  const buildPromptFromDescription = (description: string) =>
    app?.lab_mode === "agent"
      ? `Build this app: ${description.trim()}`
      : `Help me plan this app:\n\n${description.trim()}`;

  // Files panel
  const [files, setFiles] = useState<string[]>([]);
  const [selectedFile, setSelectedFile] = useState<string | null>(null);
  const [fileContent, setFileContent] = useState<string>("");
  const [loadingFile, setLoadingFile] = useState(false);
  const [filesExpanded, setFilesExpanded] = useState(true);

  // Right-panel toggle
  const [rightPanel, setRightPanel] = useState<"code" | "preview" | "secrets" | "data">("preview");

  // Skill management popover
  const [showSkillsPopover, setShowSkillsPopover] = useState(false);
  const [showAddSkills, setShowAddSkills] = useState(false);
  const [skillSearch, setSkillSearch] = useState("");
  const [availableSkills, setAvailableSkills] = useState<SkillOption[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [skillsError, setSkillsError] = useState<string | null>(null);

  // Preview
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [previewKey, setPreviewKey] = useState(0);

  // ── Loaders ──

  const refreshFiles = async () => {
    const res = await apiFetch(`/api/apps/${id}/files`).catch(() => null);
    if (!res?.ok) return;
    const data = await res.json();
    const list: string[] = data.files ?? [];
    setFiles(list);
    setApp(prev => prev ? { ...prev, file_manifest: list, status: "deployed" } : prev);
    if (list.length > 0) {
      setSelectedFile(prev => prev ?? (list.find(f => f === "index.html") ?? list[0]));
    }
  };

  const applyBuildStatus = useCallback((data: BuildStatus) => {
    setBuildPhase(data.phase);
    const turnBlocks = blocksFromTimeline(data.timeline);
    if (data.chat_history?.length) {
      setMessages(mapChatHistory(data.chat_history));
    }
    if (data.phase === "building") {
      setStreaming(true);
      setStreamingBlocks(turnBlocks);
      setStreamingFiles(data.files_written ?? []);
      return;
    }
    if (data.phase === "completed" || data.phase === "failed") {
      setStreaming(false);
      setStreamingBlocks([]);
      setStreamingFiles([]);
      if (data.phase === "failed" && data.error_message) {
        const history = data.chat_history ?? [];
        const last = history[history.length - 1];
        if (!last || last.role !== "assistant") {
          const preview = data.assistant_preview?.trim() ?? "";
          const err = `⚠️ ${data.error_message}`;
          setMessages([
            ...history.map(m => ({ role: m.role, content: m.content })),
            { role: "assistant", content: preview ? `${preview}\n\n${err}` : err },
          ]);
        }
      }
    }
    setApp(prev => prev ? {
      ...prev,
      build_phase: data.phase,
      chat_history: data.chat_history ?? prev.chat_history,
      lab_mode: data.lab_mode ?? prev.lab_mode,
      plan_ready: data.plan_ready ?? prev.plan_ready,
    } : prev);
  }, []);

  const loadBuildStatus = useCallback(async () => {
    if (!id) return null;
    try {
      const res = await apiFetch(`/api/apps/${id}/build`);
      if (!res.ok) return null;
      const data: BuildStatus = await res.json();
      const prevPhase = prevBuildPhaseRef.current;
      applyBuildStatus(data);
      prevBuildPhaseRef.current = data.phase;
      if (data.phase === "completed" || data.phase === "failed") {
        const appRes = await apiFetch(`/api/apps/${id}`);
        if (appRes?.ok) {
          const refreshed: AppRecord = await appRes.json();
          setApp(refreshed);
          if (refreshed.build_phase === "building") {
            setStreaming(true);
            setBuildPhase("building");
            prevBuildPhaseRef.current = "building";
            return data;
          }
        }
      }
      if (
        prevPhase === "building"
        && data.phase === "completed"
      ) {
        await refreshFiles();
        setPreviewKey(k => k + 1);
      }
      return data;
    } catch {
      return null;
    }
  }, [id, applyBuildStatus]);

  const loadApp = async () => {
    setLoading(true);
    setAppError(null);
    try {
      const [appRes, buildRes] = await Promise.all([
        apiFetch(`/api/apps/${id}`),
        apiFetch(`/api/apps/${id}/build`),
      ]);
      if (!appRes.ok) {
        setAppError(appRes.status === 404 ? "This app couldn't be found. It may have been deleted." : "Something went wrong loading this app. Please refresh the page.");
        return;
      }
      const a: AppRecord = await appRes.json();
      setApp(a);
      setBuildPhase(a.build_phase ?? null);
      const manifest = a.file_manifest ?? [];
      setFiles(manifest);
      if (manifest.length > 0 && !selectedFile) {
        const preferred = manifest.find(f => f === "index.html") ?? manifest[0];
        setSelectedFile(preferred);
      }
      const history = a.chat_history ?? [];
      if (history.length > 0) {
        setMessages(mapChatHistory(history));
      }
      if (buildRes.ok) {
        const buildData: BuildStatus = await buildRes.json();
        prevBuildPhaseRef.current = buildData.phase;
        applyBuildStatus(buildData);
      } else if (a.build_phase === "building") {
        prevBuildPhaseRef.current = "building";
        setStreaming(true);
      }
    } catch {
      setAppError("Couldn't load this app. Please check your connection and refresh the page.");
    } finally {
      setLoading(false);
    }
  };

  const loadFileContent = async (path: string) => {
    setLoadingFile(true);
    setFileContent("");
    try {
      const res = await apiFetch(`/api/apps/${id}/files/${path}`);
      if (res.ok) {
        const data = await res.json();
        setFileContent(data.content ?? "");
      }
    } finally {
      setLoadingFile(false);
    }
  };

  useEffect(() => { if (id) loadApp(); }, [id]);

  useEffect(() => {
    if (buildPhase !== "building") return;
    const t = setInterval(() => { void loadBuildStatus(); }, 3500);
    return () => clearInterval(t);
  }, [buildPhase, loadBuildStatus]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, streamingBlocks]);

  useEffect(() => {
    if (selectedFile) loadFileContent(selectedFile);
  }, [selectedFile]);

  // ── Send message ──

  const sendMessage = useCallback(async (overrideText?: string) => {
    const text = (overrideText ?? input).trim();
    if (!text || streaming || buildPhase === "building") return;

    if (!overrideText) setInput("");
    setMessages(prev => [...prev, { role: "user", content: text }]);
    setStreaming(true);
    setStreamingBlocks([]);
    setStreamingFiles([]);
    setBuildPhase("building");
    prevBuildPhaseRef.current = "building";

    try {
      const res = await apiFetch(`/api/apps/${id}/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text }),
      });

      if (res.status === 409) {
        const err = await res.json().catch(() => ({}));
        setMessages(prev => [
          ...prev,
          {
            role: "assistant",
            content: `⚠️ ${(err as { detail?: string }).detail ?? "A build is already in progress."}`,
          },
        ]);
        setStreaming(false);
        setBuildPhase("building");
        void loadBuildStatus();
        return;
      }

      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      void loadBuildStatus();
    } catch {
      setMessages(prev => [
        ...prev,
        { role: "assistant", content: "_Something went wrong. Please refresh the page and try again._" },
      ]);
      setStreaming(false);
      setBuildPhase(null);
    }
  }, [id, input, streaming, buildPhase, loadBuildStatus]);

  // New apps: kick off plan or agent flow from the create-time description.
  useEffect(() => {
    if (loading || !app || streaming) return;
    if (autoStartedRef.current === app.id) return;
    if (app.classification_meta?.gws_mode) return;

    const hasHistory = (app.chat_history?.length ?? 0) > 0;
    const desc = app.description?.trim();
    if (hasHistory || !desc) return;

    autoStartedRef.current = app.id;
    void sendMessage(buildPromptFromDescription(desc));
  }, [loading, app, streaming, sendMessage]);

  const handleSendFromDescription = () => {
    if (!app?.description?.trim()) return;
    void sendMessage(buildPromptFromDescription(app.description));
  };

  const setLabMode = async (mode: "plan" | "agent") => {
    if (!app || app.lab_mode === mode) return;
    const res = await apiFetch(`/api/apps/${app.id}/lab-mode`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lab_mode: mode }),
    });
    if (!res.ok) return;
    const data = await res.json();
    setApp(prev => prev ? { ...prev, lab_mode: mode, plan_ready: data.plan_ready ?? prev.plan_ready } : prev);
    if (mode === "agent" && data.plan_ready) {
      setBuildPhase("building");
      setStreaming(true);
      prevBuildPhaseRef.current = "building";
      void loadBuildStatus();
    }
  };

  const startBuildFromPlan = async () => {
    if (!app) return;
    setBuildPhase("building");
    setStreaming(true);
    prevBuildPhaseRef.current = "building";
    const res = await apiFetch(`/api/apps/${app.id}/start-build`, { method: "POST" });
    if (!res.ok) {
      setStreaming(false);
      setBuildPhase(null);
      return;
    }
    setApp(prev => prev ? { ...prev, lab_mode: "agent", plan_ready: true } : prev);
    void loadBuildStatus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  };

  const toggleVisibility = async () => {
    if (!app) return;
    const next = app.visibility === "private" ? "workspace" : "private";
    const res = await apiFetch(`/api/apps/${app.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visibility: next }),
    });
    if (res.ok) setApp(await res.json());
  };

  // ── Sharing panel state ──────────────────────────────────────────────────
  const [showSharePanel, setShowSharePanel] = useState(false);
  const [shareVisibility, setShareVisibility] = useState<"private" | "shared" | "workspace">("private");
  const [allowedUsers, setAllowedUsers] = useState<string[]>([]);
  const [adminUsers, setAdminUsers] = useState<string[]>([]);
  const [employeeSearch, setEmployeeSearch] = useState("");
  const [employeeResults, setEmployeeResults] = useState<Array<{ name: string; email: string; job_title: string; department: string }>>([]);
  const [employeeSearching, setEmployeeSearching] = useState(false);
  const [shareSaving, setShareSaving] = useState(false);
  const [shareError, setShareError] = useState<string | null>(null);

  const openSharePanel = async () => {
    if (!app) return;
    setShareVisibility(app.visibility as "private" | "shared" | "workspace");
    setAllowedUsers(app.allowed_users ?? []);
    setAdminUsers(app.admin_users ?? []);
    setEmployeeSearch("");
    setEmployeeResults([]);
    setShowSharePanel(true);
  };

  const searchEmployees = async (q: string) => {
    setEmployeeSearch(q);
    if (q.trim().length < 2) { setEmployeeResults([]); return; }
    setEmployeeSearching(true);
    try {
      const res = await apiFetch(`/api/employees/search?q=${encodeURIComponent(q)}&limit=8`);
      if (res.ok) {
        const data = await res.json();
        setEmployeeResults(data.employees ?? []);
      }
    } catch { /* ignore */ }
    finally { setEmployeeSearching(false); }
  };

  const addSharedUser = (email: string) => {
    if (!email || allowedUsers.includes(email)) return;
    setAllowedUsers(prev => [...prev, email]);
    setEmployeeSearch("");
    setEmployeeResults([]);
  };

  const removeSharedUser = (email: string) => {
    setAllowedUsers(prev => prev.filter(e => e !== email));
    setAdminUsers(prev => prev.filter(e => e !== email));
  };

  const setUserRole = (email: string, role: "member" | "admin") => {
    if (role === "admin") {
      setAdminUsers(prev => (prev.includes(email) ? prev : [...prev, email]));
    } else {
      setAdminUsers(prev => prev.filter(e => e !== email));
    }
  };

  const addWorkspaceAdmin = (email: string) => {
    if (!email || adminUsers.includes(email)) return;
    setAdminUsers(prev => [...prev, email]);
    setEmployeeSearch("");
    setEmployeeResults([]);
  };

  const removeWorkspaceAdmin = (email: string) => {
    setAdminUsers(prev => prev.filter(e => e !== email));
  };

  const saveSharing = async () => {
    if (!app) return;
    setShareSaving(true);
    try {
      const res = await apiFetch(`/api/apps/${app.id}/sharing`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          visibility: shareVisibility,
          allowed_users: allowedUsers,
          admin_users: adminUsers,
        }),
      });
      const data = await res.json();
      if (res.ok) {
        setApp(prev => prev ? {
          ...prev,
          visibility: data.visibility,
          allowed_users: data.allowed_users,
          admin_users: data.admin_users,
        } : prev);
        setShowSharePanel(false);
      } else {
        setShareError("Couldn't save sharing settings. Please try again.");
      }
    } catch {
      setShareError("Couldn't save sharing settings. Please try again.");
    } finally {
      setShareSaving(false);
    }
  };

  const closeSkillsPopover = () => {
    setShowSkillsPopover(false);
    setShowAddSkills(false);
    setSkillSearch("");
  };

  const loadAvailableSkills = async () => {
    if (availableSkills.length > 0) return;
    setSkillsLoading(true);
    setSkillsError(null);
    try {
      const res = await apiFetch("/api/agents/available-skills");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setAvailableSkills(Array.isArray(data) ? data : []);
    } catch {
      setSkillsError("Could not load skills.");
    } finally {
      setSkillsLoading(false);
    }
  };

  const openSkillsPopover = () => {
    setShowSkillsPopover(true);
  };

  const openAddSkills = async () => {
    setShowAddSkills(true);
    await loadAvailableSkills();
  };

  const toggleSkill = async (slug: string) => {
    if (!app) return;
    const current = app.skill_ids ?? [];
    const next = current.includes(slug)
      ? current.filter(s => s !== slug)
      : [...current, slug];
    const res = await apiFetch(`/api/apps/${app.id}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ skill_ids: next }),
    });
    if (res.ok) setApp(await res.json());
  };

  // ── Render ──

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="w-5 h-5 animate-spin text-slate-400" />
      </div>
    );
  }

  if (!app || appError) {
    return (
      <div className="flex flex-col items-center justify-center h-full text-center px-6">
        <FlaskConical className="w-10 h-10 text-slate-200 mb-3" />
        <p className="text-sm font-bold text-slate-900">{appError ?? "App not found"}</p>
        <NavLink to="/lab" className="mt-4 text-xs text-slate-500 hover:text-slate-900 flex items-center gap-1.5">
          <ArrowLeft className="w-3 h-3" /> Back to Lab
        </NavLink>
      </div>
    );
  }

  return (
    <div className="flex-1 w-full h-full flex flex-col overflow-hidden bg-slate-50">
      {/* ── Top bar ── */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-surface border-b border-slate-200 shrink-0 gap-3">
        <div className="flex items-center gap-2.5 min-w-0 flex-wrap">
          <NavLink to="/lab" className="text-slate-400 hover:text-slate-700 transition-colors shrink-0" title="Back to Lab">
            <ArrowLeft className="w-4 h-4" />
          </NavLink>
          <FlaskConical className="w-4 h-4 text-indigo-500 shrink-0" />
          <span className="text-sm font-bold text-slate-900 truncate">{app.name}</span>
          <StatusChip status={app.status} />
          <BuildConfigChips app={app} />
          <LabModeToggle
            labMode={app.lab_mode ?? "plan"}
            planReady={app.plan_ready}
            disabled={streaming || buildPhase === "building"}
            onChange={setLabMode}
          />
          {streaming && (
            <span className="flex items-center gap-1 text-[10px] text-indigo-500 font-medium animate-pulse">
              <Loader2 className="w-3 h-3 animate-spin" />
              {app.lab_mode === "plan" ? "Planning…" : "Building…"}
            </span>
          )}
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {/* Skill chips */}
          {(app.skill_ids ?? []).length > 0 && (
            <div className="flex items-center gap-1 shrink-0">
              {(app.skill_ids ?? []).slice(0, 3).map(slug => (
                <span key={slug} className="flex items-center gap-1 text-[10px] bg-indigo-50 text-indigo-700 border border-indigo-200 rounded-full px-2 py-0.5 font-medium">
                  <Zap className="w-2.5 h-2.5" />{slug}
                </span>
              ))}
              {(app.skill_ids ?? []).length > 3 && (
                <span className="text-[10px] text-slate-400">+{(app.skill_ids ?? []).length - 3}</span>
              )}
            </div>
          )}

          {/* Manage Skills button + popover */}
          <div className="relative">
            <button
              onClick={showSkillsPopover ? closeSkillsPopover : openSkillsPopover}
              className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-900 border border-slate-200 px-2 py-1.5 rounded-lg transition-colors"
              title="Manage skills"
            >
              <Settings2 className="w-3.5 h-3.5" />
              Skills
              {(app.skill_ids ?? []).length > 0 && (
                <span className="text-[10px] bg-indigo-100 text-indigo-700 rounded-full px-1.5 py-0.5 font-semibold">
                  {(app.skill_ids ?? []).length}
                </span>
              )}
            </button>
            {showSkillsPopover && (
              <div className="absolute right-0 top-full mt-1.5 w-72 bg-surface border border-slate-200 rounded-xl shadow-xl z-50">
                <div className="flex items-center justify-between px-3 py-2 border-b border-slate-100">
                  <span className="text-[11px] font-semibold text-slate-600 uppercase tracking-wider flex items-center gap-1.5">
                    <Zap className="w-3 h-3 text-indigo-500" /> Manage Skills
                  </span>
                  <button onClick={closeSkillsPopover} className="text-slate-400 hover:text-slate-700">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
                <div className="p-2 max-h-72 overflow-y-auto">
                  <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-2 pt-1 pb-1.5">
                    Attached
                  </p>
                  {(app.skill_ids ?? []).length === 0 ? (
                    <p className="text-xs text-slate-400 px-2 py-2">
                      No skills attached. Simple apps like this usually don&apos;t need any.
                    </p>
                  ) : (
                    (app.skill_ids ?? []).map(slug => (
                      <button
                        key={slug}
                        onClick={() => toggleSkill(slug)}
                        className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-left transition-colors text-xs bg-indigo-50 text-indigo-700 hover:bg-indigo-100"
                      >
                        <Zap className="w-3 h-3 shrink-0" />
                        <span className="flex-1 font-medium">{slug}</span>
                        <X className="w-3 h-3 text-indigo-400" />
                      </button>
                    ))
                  )}

                  {!showAddSkills ? (
                    <button
                      onClick={() => void openAddSkills()}
                      className="mt-2 w-full flex items-center justify-center gap-1.5 px-2.5 py-2 rounded-lg text-xs text-slate-600 border border-dashed border-slate-200 hover:border-indigo-300 hover:text-indigo-700 transition-colors"
                    >
                      <Plus className="w-3 h-3" />
                      Add skills
                    </button>
                  ) : (
                    <>
                      <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider px-2 pt-3 pb-1.5">
                        Add from catalog
                      </p>
                      <div className="relative px-2 pb-2">
                        <Search className="absolute left-4 top-1/2 -translate-y-1/2 w-3 h-3 text-slate-400" />
                        <input
                          type="text"
                          value={skillSearch}
                          onChange={e => setSkillSearch(e.target.value)}
                          placeholder="Search skills…"
                          className="w-full pl-7 pr-2 py-1.5 text-xs border border-slate-200 rounded-lg focus:outline-none focus:ring-1 focus:ring-indigo-300"
                        />
                      </div>
                      {skillsLoading && (
                        <div className="flex items-center justify-center py-4">
                          <Loader2 className="w-4 h-4 animate-spin text-slate-400" />
                        </div>
                      )}
                      {skillsError && (
                        <p className="text-xs text-red-500 px-2 py-1">{skillsError}</p>
                      )}
                      {!skillsLoading && !skillsError && availableSkills
                        .filter(skill => !(app.skill_ids ?? []).includes(skill.slug))
                        .filter(skill => !skillSearch || skill.slug.toLowerCase().includes(skillSearch.toLowerCase()))
                        .map(skill => (
                          <button
                            key={skill.slug}
                            onClick={() => toggleSkill(skill.slug)}
                            className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-left transition-colors text-xs text-slate-600 hover:bg-slate-50"
                          >
                            <Plus className="w-3 h-3 shrink-0 text-slate-400" />
                            <span className="flex-1 font-medium">{skill.slug}</span>
                          </button>
                        ))}
                      {!skillsLoading && !skillsError && availableSkills
                        .filter(skill => !(app.skill_ids ?? []).includes(skill.slug))
                        .filter(skill => !skillSearch || skill.slug.toLowerCase().includes(skillSearch.toLowerCase()))
                        .length === 0 && (
                        <p className="text-xs text-slate-400 px-2 py-2 text-center">No matching skills.</p>
                      )}
                    </>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Code / Preview / Secrets toggle */}
          <div className="flex items-center bg-slate-100 rounded-lg p-0.5 gap-0.5">
            <button
              onClick={() => setRightPanel("code")}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${rightPanel === "code"
                  ? "bg-surface text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
                }`}
            >
              <Code2 className="w-3.5 h-3.5" />
              Code
            </button>
            <button
              onClick={() => setRightPanel("preview")}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${rightPanel === "preview"
                  ? "bg-surface text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
                }`}
            >
              <Monitor className="w-3.5 h-3.5" />
              Preview
            </button>
            <button
              onClick={() => setRightPanel("data")}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${
                rightPanel === "data"
                  ? "bg-surface text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
              }`}
            >
              <Table2 className="w-3.5 h-3.5" />
              Data
            </button>
            <button
              onClick={() => setRightPanel("secrets")}
              className={`flex items-center gap-1.5 text-xs px-2.5 py-1 rounded-md font-medium transition-colors ${rightPanel === "secrets"
                  ? "bg-surface text-slate-900 shadow-sm"
                  : "text-slate-500 hover:text-slate-700"
                }`}
            >
              <KeyRound className="w-3.5 h-3.5" />
              Secrets
            </button>
          </div>

          <button
            onClick={openSharePanel}
            className="flex items-center gap-1.5 text-xs text-slate-500 hover:text-slate-900 border border-slate-200 px-2.5 py-1.5 rounded-lg transition-colors"
          >
            {app.visibility === "workspace"
              ? <><Globe className="w-3.5 h-3.5 text-indigo-500" /> Workspace</>
              : app.visibility === "shared"
                ? <><Users className="w-3.5 h-3.5 text-amber-500" /> {app.allowed_users?.length ?? 0} people</>
                : <><Lock className="w-3.5 h-3.5" /> Private</>}
          </button>

          {app.status === "deployed" && (
            <a
              href={`/apps/${app.slug}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-1.5 text-xs font-bold bg-emerald-600 text-white px-3 py-1.5 rounded-lg hover:bg-emerald-700 transition-colors"
            >
              <ExternalLink className="w-3.5 h-3.5" />
              Open App
            </a>
          )}
        </div>
      </div>

      {/* ── Two-panel body ── */}
      <div className="flex-1 flex overflow-hidden min-h-0">

        {/* Panel 1 — Chat (fixed width) */}
        <ChatPanel
          messages={messages}
          streamingBlocks={streamingBlocks}
          streamingFiles={streamingFiles}
          streaming={streaming}
          input={input}
          appDescription={app.description}
          labMode={app.lab_mode ?? "plan"}
          planReady={app.plan_ready}
          buildPlan={app.build_plan}
          onStartBuild={startBuildFromPlan}
          onInputChange={setInput}
          onKeyDown={handleKeyDown}
          onSend={() => { void sendMessage(); }}
          onStartFromDescription={handleSendFromDescription}
          chatEndRef={chatEndRef}
        />

        {/* Panel 2 — Code / Preview / Secrets (toggled) */}
        <div className="flex-1 flex overflow-hidden min-h-0 min-w-0">
          {rightPanel === "code" ? (
            <FilesPanel
              appId={id}
              appSlug={app.slug}
              files={files}
              selectedFile={selectedFile}
              fileContent={fileContent}
              loadingFile={loadingFile}
              expanded={filesExpanded}
              onSelectFile={setSelectedFile}
              onToggle={() => setFilesExpanded(v => !v)}
            />
          ) : rightPanel === "secrets" ? (
            <SecretsPanel
              app={app}
              onAppUpdated={setApp}
            />
          ) : rightPanel === "data" ? (
            <DataPanel
              appId={id}
              onSourcesChanged={refreshFiles}
            />
          ) : (
            <PreviewPanel
              app={app}
              previewKey={previewKey}
              iframeRef={iframeRef}
              onRefresh={() => setPreviewKey(k => k + 1)}
            />
          )}
        </div>
      </div>

      {/* ── Share panel modal ── */}
      {showSharePanel && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm"
          onClick={e => { if (e.target === e.currentTarget) setShowSharePanel(false); }}
        >
          <div className="bg-surface rounded-2xl shadow-2xl w-full max-w-md mx-4 overflow-hidden">
            {/* Header */}
            <div className="flex items-center justify-between px-5 py-4 border-b border-slate-100">
              <div className="flex items-center gap-2">
                <Share2 className="w-4 h-4 text-indigo-500" />
                <span className="font-semibold text-slate-900 text-sm">Share "{app.name}"</span>
              </div>
              <button onClick={() => setShowSharePanel(false)} className="text-slate-400 hover:text-slate-700">
                <X className="w-4 h-4" />
              </button>
            </div>

            <div className="p-5 space-y-5">
              {/* Visibility selector */}
              <div className="grid grid-cols-3 gap-2">
                {(["private", "shared", "workspace"] as const).map(v => (
                  <button
                    key={v}
                    onClick={() => setShareVisibility(v)}
                    className={`flex flex-col items-center gap-1.5 py-3 px-2 rounded-xl border-2 text-xs font-medium transition-all ${shareVisibility === v
                        ? "border-indigo-500 bg-indigo-50 text-indigo-700"
                        : "border-slate-200 text-slate-500 hover:border-slate-300"
                      }`}
                  >
                    {v === "private" && <Lock className="w-4 h-4" />}
                    {v === "shared" && <Users className="w-4 h-4" />}
                    {v === "workspace" && <Globe className="w-4 h-4" />}
                    {v === "private" ? "Private" : v === "shared" ? "Specific people" : "All workspace"}
                  </button>
                ))}
              </div>

              <p className="text-xs text-slate-500 -mt-1">
                {shareVisibility === "private" && "Only you can access this app."}
                {shareVisibility === "workspace" && "Everyone in the organisation can view this app."}
                {shareVisibility === "shared" && "Only the people you add below can view this app."}
              </p>

              {/* People picker — only shown for "shared" */}
              {shareVisibility === "shared" && (
                <div className="space-y-3">
                  <label className="text-xs font-semibold text-slate-700">Add people</label>

                  {/* Search box */}
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search by name or email…"
                      value={employeeSearch}
                      onChange={e => searchEmployees(e.target.value)}
                      className="w-full pl-8 pr-3 py-2 border border-slate-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-indigo-300"
                    />
                    {employeeSearching && (
                      <Loader2 className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 animate-spin" />
                    )}
                  </div>

                  {/* Search results dropdown */}
                  {employeeResults.length > 0 && (
                    <div className="border border-slate-200 rounded-lg overflow-hidden divide-y divide-slate-100 max-h-44 overflow-y-auto">
                      {employeeResults.map(emp => (
                        <button
                          key={emp.email}
                          onClick={() => addSharedUser(emp.email)}
                          disabled={allowedUsers.includes(emp.email)}
                          className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50 disabled:opacity-40 disabled:cursor-default transition-colors"
                        >
                          <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-xs font-bold shrink-0">
                            {emp.name.charAt(0).toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-slate-800 truncate">{emp.name}</p>
                            <p className="text-[11px] text-slate-400 truncate">{emp.email} · {emp.department}</p>
                          </div>
                          {allowedUsers.includes(emp.email)
                            ? <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 ml-auto" />
                            : <Plus className="w-3.5 h-3.5 text-slate-400 shrink-0 ml-auto" />}
                        </button>
                      ))}
                    </div>
                  )}

                  {/* Current allowed users */}
                  {allowedUsers.length > 0 && (
                    <div className="space-y-1.5">
                      <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Has access</p>
                      {allowedUsers.map(email => (
                        <div key={email} className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg">
                          <div className="w-6 h-6 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-xs font-bold shrink-0">
                            {email.charAt(0).toUpperCase()}
                          </div>
                          <span className="flex-1 text-xs text-slate-700 truncate">{email}</span>
                          <select
                            value={adminUsers.includes(email) ? "admin" : "member"}
                            onChange={e => setUserRole(email, e.target.value as "member" | "admin")}
                            className="text-[11px] border border-slate-200 rounded-md px-1.5 py-1 bg-white text-slate-600"
                          >
                            <option value="member">Can submit</option>
                            <option value="admin">Admin (sees all)</option>
                          </select>
                          <button
                            onClick={() => removeSharedUser(email)}
                            className="text-slate-400 hover:text-red-500 transition-colors"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {shareVisibility === "workspace" && (
                <div className="space-y-3">
                  <label className="text-xs font-semibold text-slate-700">App admins</label>
                  <p className="text-[11px] text-slate-500">
                    Admins can view all submissions. Everyone else in the workspace can submit their own.
                  </p>
                  <div className="relative">
                    <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400" />
                    <input
                      type="text"
                      placeholder="Search by name or email…"
                      value={employeeSearch}
                      onChange={e => searchEmployees(e.target.value)}
                      className="w-full pl-8 pr-3 py-2 border border-slate-200 rounded-lg text-xs focus:outline-none focus:ring-2 focus:ring-indigo-300"
                    />
                    {employeeSearching && (
                      <Loader2 className="absolute right-2.5 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-slate-400 animate-spin" />
                    )}
                  </div>
                  {employeeResults.length > 0 && (
                    <div className="border border-slate-200 rounded-lg overflow-hidden divide-y divide-slate-100 max-h-44 overflow-y-auto">
                      {employeeResults.map(emp => (
                        <button
                          key={emp.email}
                          onClick={() => addWorkspaceAdmin(emp.email)}
                          disabled={adminUsers.includes(emp.email)}
                          className="w-full flex items-center gap-3 px-3 py-2.5 text-left hover:bg-slate-50 disabled:opacity-40 disabled:cursor-default transition-colors"
                        >
                          <div className="w-7 h-7 rounded-full bg-indigo-100 flex items-center justify-center text-indigo-700 text-xs font-bold shrink-0">
                            {emp.name.charAt(0).toUpperCase()}
                          </div>
                          <div className="min-w-0">
                            <p className="text-xs font-medium text-slate-800 truncate">{emp.name}</p>
                            <p className="text-[11px] text-slate-400 truncate">{emp.email} · {emp.department}</p>
                          </div>
                          {adminUsers.includes(emp.email)
                            ? <Check className="w-3.5 h-3.5 text-emerald-500 shrink-0 ml-auto" />
                            : <Plus className="w-3.5 h-3.5 text-slate-400 shrink-0 ml-auto" />}
                        </button>
                      ))}
                    </div>
                  )}
                  {adminUsers.length > 0 && (
                    <div className="space-y-1.5">
                      <p className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Admins</p>
                      {adminUsers.map(email => (
                        <div key={email} className="flex items-center gap-2 px-3 py-2 bg-slate-50 rounded-lg">
                          <div className="w-6 h-6 rounded-full bg-amber-100 flex items-center justify-center text-amber-700 text-xs font-bold shrink-0">
                            {email.charAt(0).toUpperCase()}
                          </div>
                          <span className="flex-1 text-xs text-slate-700 truncate">{email}</span>
                          <button
                            onClick={() => removeWorkspaceAdmin(email)}
                            className="text-slate-400 hover:text-red-500 transition-colors"
                          >
                            <X className="w-3.5 h-3.5" />
                          </button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* Footer */}
            <div className="px-5 py-4 bg-slate-50 border-t border-slate-100 space-y-2">
              {shareError && (
                <p className="text-xs text-red-700">{shareError}</p>
              )}
              <div className="flex items-center justify-end gap-2">
                <button
                  onClick={() => { setShowSharePanel(false); setShareError(null); }}
                  className="text-xs text-slate-500 hover:text-slate-800 px-3 py-1.5 rounded-lg border border-slate-200 hover:border-slate-300 transition-colors"
                >
                  Cancel
                </button>
                <button
                  onClick={saveSharing}
                  disabled={shareSaving}
                  className="flex items-center gap-1.5 text-xs font-semibold bg-indigo-600 text-white px-4 py-1.5 rounded-lg hover:bg-indigo-700 disabled:opacity-50 transition-colors"
                >
                  {shareSaving ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Check className="w-3.5 h-3.5" />}
                  Save
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Chat panel ────────────────────────────────────────────────────────────────

function ChatPanel({
  messages,
  streamingBlocks,
  streamingFiles,
  streaming,
  input,
  appDescription,
  labMode,
  planReady,
  buildPlan,
  onStartBuild,
  onInputChange,
  onKeyDown,
  onSend,
  onStartFromDescription,
  chatEndRef,
}: {
  messages: ChatMessage[];
  streamingBlocks: ChatBlock[];
  streamingFiles: string[];
  streaming: boolean;
  input: string;
  appDescription: string | null;
  labMode: "plan" | "agent";
  planReady?: boolean;
  buildPlan?: AppRecord["build_plan"];
  onStartBuild: () => void;
  onInputChange: (v: string) => void;
  onKeyDown: (e: KeyboardEvent<HTMLTextAreaElement>) => void;
  onSend: () => void;
  onStartFromDescription: () => void;
  chatEndRef: RefObject<HTMLDivElement>;
}) {
  return (
    <div className="w-96 shrink-0 flex flex-col border-r border-slate-200 bg-surface overflow-hidden">
      {/* Panel header */}
      <div className="px-3 py-2 border-b border-slate-100 shrink-0 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5 min-w-0">
          <Sparkles className="w-3.5 h-3.5 text-[#FF444F] shrink-0" />
          <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider truncate">
            {labMode === "plan" ? "Plan" : "Agent"}
          </span>
        </div>
        {labMode === "plan" && planReady && (
          <button
            type="button"
            onClick={onStartBuild}
            disabled={streaming}
            className="text-[10px] font-bold text-white bg-[#FF444F] hover:bg-[#e63d48] px-2 py-1 rounded-md transition-colors shrink-0"
          >
            Start building
          </button>
        )}
      </div>

      {buildPlan?.features && buildPlan.features.length > 0 && labMode === "plan" && (
        <div className="px-3 py-2 border-b border-slate-100 bg-slate-50/80 shrink-0 max-h-36 overflow-y-auto">
          {buildPlan.summary && (
            <p className="text-[10px] text-slate-500 mb-2 leading-relaxed">{buildPlan.summary}</p>
          )}
          <ol className="space-y-1">
            {buildPlan.features.map((f, i) => (
              <li key={i} className="text-[10px] text-slate-600">
                <span className="font-semibold text-slate-800">{i + 1}. {f.title}</span>
                {f.description ? ` — ${f.description}` : ""}
              </li>
            ))}
          </ol>
        </div>
      )}

      {/* Messages */}
      <div className="flex-1 overflow-y-auto px-3 py-3 space-y-3 min-h-0">
        {messages.length === 0 && !streaming && (
          <div className="text-center py-8">
            <p className="text-xs text-slate-400 leading-relaxed">
              {labMode === "plan" ? (
                <>
                  Plan mode: I&apos;ll draft a build plan and ask clarifying questions.<br />
                  When the plan is ready, we&apos;ll switch to Agent mode and write code.
                </>
              ) : (
                <>
                  Agent mode: describe changes and I&apos;ll update the app.<br />
                  Attach Google Sheet data in the <strong className="font-medium text-slate-500">Data</strong> panel if needed.
                </>
              )}
            </p>
            {appDescription && (
              <button
                onClick={onStartFromDescription}
                className="mt-3 text-xs text-[#FF444F] hover:underline"
              >
                Start from description →
              </button>
            )}
          </div>
        )}

        {messages.map((msg, i) => (
          <div key={i} className={`flex flex-col gap-1 ${msg.role === "user" ? "items-end" : "items-start"}`}>
            {msg.role === "user" ? (
              <div className="max-w-[95%] rounded-xl px-3 py-2.5 bg-slate-900 text-slate-50 text-xs leading-relaxed whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
                {msg.content}
              </div>
            ) : (
              <AssistantTurnBlocks blocks={blocksForMessage(msg)} />
            )}
            {msg.filesWritten && msg.filesWritten.length > 0 && (
              <div className="flex flex-wrap gap-1 max-w-[95%]">
                {msg.filesWritten.map(f => (
                  <span key={f} className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5 font-mono">
                    {f}
                  </span>
                ))}
              </div>
            )}
          </div>
        ))}

        {/* Streaming assistant reply */}
        {streaming && (streamingBlocks.length > 0 || streamingFiles.length > 0) && (
          <div className="flex flex-col gap-1.5 items-start w-full">
            <AssistantTurnBlocks blocks={streamingBlocks} showCursor />
            {streamingFiles.length > 0 && (
              <div className="flex flex-wrap gap-1">
                {streamingFiles.map(f => (
                  <span key={f} className="text-[10px] bg-emerald-50 text-emerald-700 border border-emerald-200 rounded px-1.5 py-0.5 font-mono flex items-center gap-1">
                    <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse shrink-0" />
                    {f}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}

        {streaming && streamingBlocks.length === 0 && streamingFiles.length === 0 && (
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <Loader2 className="w-3 h-3 animate-spin" /> Thinking…
          </div>
        )}

        <div ref={chatEndRef} />
      </div>

      {/* Input */}
      <div className="p-3 border-t border-slate-100 shrink-0">
        <div className="flex items-end gap-2">
          <textarea
            value={input}
            onChange={e => onInputChange(e.target.value)}
            onKeyDown={onKeyDown}
            placeholder={streaming ? "Building…" : "Describe a change or feature…"}
            disabled={streaming}
            rows={2}
            className="flex-1 text-xs px-3 py-2 border border-slate-200 rounded-lg resize-none focus:outline-none focus:border-[#FF444F]/50 focus:ring-1 focus:ring-[#FF444F]/20 disabled:opacity-50 leading-relaxed"
          />
          <button
            onClick={onSend}
            disabled={!input.trim() || streaming}
            className="p-2 bg-[#FF444F] hover:bg-[#e63d48] text-white rounded-lg disabled:opacity-40 transition-colors shrink-0"
          >
            <Send className="w-3.5 h-3.5" />
          </button>
        </div>
        <p className="text-[10px] text-slate-400 mt-1.5">↵ Send &nbsp;·&nbsp; Shift+↵ New line</p>
      </div>
    </div>
  );
}

// ── Files panel ───────────────────────────────────────────────────────────────

function FileTreeNode({
  name,
  node,
  depth,
  selectedFile,
  onSelect,
}: {
  name: string;
  node: string | Record<string, unknown>;
  depth: number;
  selectedFile: string | null;
  onSelect: (path: string) => void;
}) {
  const [open, setOpen] = useState(true);

  if (typeof node === "string") {
    const selected = selectedFile === node;
    return (
      <button
        type="button"
        onClick={() => onSelect(node)}
        title={node}
        className={`w-full flex items-center gap-1.5 py-1 pr-2 text-left transition-colors ${
          selected
            ? "bg-indigo-50 text-indigo-700 border-r-2 border-indigo-400"
            : "text-slate-600 hover:bg-slate-50 hover:text-slate-900"
        }`}
        style={{ paddingLeft: `${8 + depth * 12}px` }}
      >
        <FileCode2 className="w-3 h-3 shrink-0 opacity-50" />
        <span className="text-[11px] font-mono truncate">{name}</span>
      </button>
    );
  }

  const children = Object.entries(node).sort(([a], [b]) => a.localeCompare(b));

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center gap-1 py-1 pr-2 text-left text-slate-500 hover:bg-slate-50 hover:text-slate-800 transition-colors"
        style={{ paddingLeft: `${8 + depth * 12}px` }}
      >
        {open ? (
          <ChevronRight className="w-3 h-3 shrink-0 rotate-90 opacity-60" />
        ) : (
          <ChevronRight className="w-3 h-3 shrink-0 opacity-60" />
        )}
        <span className="text-[11px] font-mono font-medium truncate">{name}/</span>
      </button>
      {open && children.map(([childName, childNode]) => (
        <FileTreeNode
          key={childName}
          name={childName}
          node={childNode as string | Record<string, unknown>}
          depth={depth + 1}
          selectedFile={selectedFile}
          onSelect={onSelect}
        />
      ))}
    </div>
  );
}

function FilesPanel({
  appId,
  appSlug,
  files,
  selectedFile,
  fileContent,
  loadingFile,
  expanded,
  onSelectFile,
  onToggle,
}: {
  appId: string;
  appSlug: string;
  files: string[];
  selectedFile: string | null;
  fileContent: string;
  loadingFile: boolean;
  expanded: boolean;
  onSelectFile: (f: string) => void;
  onToggle: () => void;
}) {
  const [downloading, setDownloading] = useState(false);
  const tree = React.useMemo(() => buildFileTree(files), [files]);

  const handleDownloadZip = async () => {
    setDownloading(true);
    try {
      const res = await apiFetch(`/api/apps/${appId}/export.zip`);
      if (!res.ok) throw new Error("Export failed");
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${appSlug || appId}.zip`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch {
      // user can retry
    } finally {
      setDownloading(false);
    }
  };

  if (!expanded) {
    return (
      <div className="w-8 shrink-0 flex flex-col border-r border-slate-200 bg-surface">
        <button
          onClick={onToggle}
          className="flex flex-col items-center justify-center gap-1 py-3 text-slate-400 hover:text-slate-700 hover:bg-slate-50 transition-colors h-full"
          title="Show files"
        >
          <ChevronRight className="w-3.5 h-3.5" />
          <span className="text-[9px] font-semibold uppercase tracking-widest text-slate-400 [writing-mode:vertical-rl] rotate-180">
            Files {files.length > 0 ? `(${files.length})` : ""}
          </span>
        </button>
      </div>
    );
  }

  return (
    <div className="flex-1 flex flex-col border-r border-slate-200 overflow-hidden">
      {/* Header */}
      <div className="px-3 py-2 border-b border-slate-200 bg-surface shrink-0 flex items-center gap-1.5">
        <Code2 className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Code</span>
        {files.length > 0 && (
          <span className="text-[10px] bg-slate-100 text-slate-500 rounded-full px-2">{files.length}</span>
        )}
        <div className="ml-auto flex items-center gap-0.5">
          <button
            type="button"
            onClick={() => { void handleDownloadZip(); }}
            disabled={downloading || files.length === 0}
            title="Download all files as ZIP"
            className="p-1 rounded text-slate-400 hover:text-slate-700 hover:bg-slate-100 disabled:opacity-40 transition-colors"
          >
            {downloading ? (
              <Loader2 className="w-3.5 h-3.5 animate-spin" />
            ) : (
              <Download className="w-3.5 h-3.5" />
            )}
          </button>
          <button
            type="button"
            onClick={onToggle}
            className="text-slate-400 hover:text-slate-700 transition-colors p-0.5"
            title="Collapse files"
          >
            <ChevronLeft className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {files.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-center p-8 bg-slate-50">
          <div>
            <FileCode2 className="w-10 h-10 text-slate-200 mx-auto mb-3" />
            <p className="text-xs text-slate-400 leading-relaxed">
              No files yet.<br />Send a message to generate your app.
            </p>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex overflow-hidden min-h-0">
          {/* File tree */}
          <div className="w-44 shrink-0 border-r border-slate-200 overflow-y-auto bg-slate-50/80 py-1">
            {Object.entries(tree)
              .sort(([a], [b]) => a.localeCompare(b))
              .map(([name, node]) => (
                <FileTreeNode
                  key={name}
                  name={name}
                  node={node}
                  depth={0}
                  selectedFile={selectedFile}
                  onSelect={onSelectFile}
                />
              ))}
          </div>

          {/* File content */}
          <div className="flex-1 overflow-hidden min-w-0 bg-[#0d1117]">
            {loadingFile ? (
              <div className="flex items-center justify-center h-full">
                <Loader2 className="w-4 h-4 animate-spin text-slate-500" />
              </div>
            ) : selectedFile ? (
              <CodeView content={fileContent} filename={selectedFile} />
            ) : (
              <div className="flex items-center justify-center h-full text-xs text-slate-500">
                Select a file
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Data panel (Google Drive sources) ─────────────────────────────────────────

interface DataSourceEntry {
  file_id: string;
  name: string;
  source_type: string;
  path: string;
  fetched_at: string;
  row_estimate?: number | null;
  truncated?: boolean;
  columns_preview?: string[] | null;
}

function DataPanel({
  appId,
  onSourcesChanged,
}: {
  appId: string;
  onSourcesChanged: () => void;
}) {
  const [gwsConnected, setGwsConnected] = useState<boolean | null>(null);
  const [sources, setSources] = useState<DataSourceEntry[]>([]);
  const [urlInput, setUrlInput] = useState("");
  const [loading, setLoading] = useState(true);
  const [attaching, setAttaching] = useState(false);
  const [removing, setRemoving] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    setError(null);
    try {
      const [statusRes, sourcesRes] = await Promise.all([
        apiFetch(`/api/apps/${appId}/google-workspace/status`),
        apiFetch(`/api/apps/${appId}/data-sources`),
      ]);
      if (statusRes.ok) {
        const status = await statusRes.json();
        setGwsConnected(!!status.connected);
      }
      if (sourcesRes.ok) {
        const data = await sourcesRes.json();
        setSources(data.sources ?? []);
      }
    } catch {
      setError("Could not load data sources.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [appId]);

  const attach = async () => {
    const url = urlInput.trim();
    if (!url) return;
    setAttaching(true);
    setError(null);
    setSuccess(null);
    try {
      const res = await apiFetch(`/api/apps/${appId}/data-sources/google-drive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url_or_id: url }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(
          typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`,
        );
      }
      setUrlInput("");
      setSuccess(`Attached "${body.source?.name ?? "file"}".`);
      await load();
      onSourcesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Attach failed.");
    } finally {
      setAttaching(false);
    }
  };

  const remove = async (fileId: string) => {
    setRemoving(fileId);
    setError(null);
    try {
      const res = await apiFetch(
        `/api/apps/${appId}/data-sources/google-drive/${encodeURIComponent(fileId)}`,
        { method: "DELETE" },
      );
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(
          typeof body.detail === "string" ? body.detail : `HTTP ${res.status}`,
        );
      }
      await load();
      onSourcesChanged();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Remove failed.");
    } finally {
      setRemoving(null);
    }
  };

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-surface">
      <div className="px-4 py-3 border-b border-slate-100 flex items-center justify-between shrink-0">
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">
          Data sources
        </span>
        <button
          onClick={load}
          disabled={loading}
          className="text-slate-400 hover:text-slate-600 transition-colors"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {gwsConnected === false && (
          <div className="text-xs bg-amber-50 border border-amber-200 text-amber-800 rounded-lg px-3 py-2.5">
            Google Workspace is not connected.{" "}
            <a href="/integrations" className="font-semibold underline hover:text-amber-900">
              Connect on Integrations
            </a>{" "}
            to attach Sheets or Docs.
          </div>
        )}

        {gwsConnected === true && (
          <div className="flex items-center gap-1.5 text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
            Google Workspace connected
          </div>
        )}

        <div>
          <label className="block text-xs font-semibold text-slate-500 uppercase tracking-wide mb-1.5">
            Google Sheet or Doc URL
          </label>
          <div className="flex gap-2">
            <input
              type="url"
              value={urlInput}
              onChange={(e) => setUrlInput(e.target.value)}
              placeholder="https://docs.google.com/spreadsheets/d/..."
              className="flex-1 text-sm border border-slate-200 rounded-lg px-3 py-2 focus:outline-none focus:ring-2 focus:ring-[#FF444F]/30 focus:border-[#FF444F]"
              disabled={attaching}
            />
            <button
              onClick={attach}
              disabled={attaching || !urlInput.trim()}
              className="shrink-0 px-3 py-2 text-sm font-semibold text-white bg-[#FF444F] hover:bg-[#e63d48] rounded-lg disabled:opacity-50 transition-colors"
            >
              {attaching ? <Loader2 className="w-4 h-4 animate-spin" /> : "Attach"}
            </button>
          </div>
          <p className="text-[11px] text-slate-400 mt-1.5">
            Downloads a snapshot (CSV or text). The AI probes it before building your dashboard.
          </p>
        </div>

        {error && (
          <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded-lg px-3 py-2">
            {error}
          </div>
        )}
        {success && (
          <div className="text-xs text-emerald-700 bg-emerald-50 border border-emerald-200 rounded-lg px-3 py-2">
            {success}
          </div>
        )}

        <div>
          <span className="text-xs font-semibold text-slate-500 uppercase tracking-wide">
            Attached ({sources.length})
          </span>
          {sources.length === 0 ? (
            <p className="text-xs text-slate-400 mt-2">No data sources yet.</p>
          ) : (
            <ul className="mt-2 space-y-2">
              {sources.map((s) => (
                <li
                  key={s.file_id}
                  className="border border-slate-200 rounded-lg px-3 py-2.5 flex items-start justify-between gap-2"
                >
                  <div className="min-w-0">
                    <p className="text-sm font-medium text-slate-800 truncate">{s.name}</p>
                    <p className="text-[11px] text-slate-400 font-mono truncate">{s.path}</p>
                    <p className="text-[11px] text-slate-500 mt-0.5">
                      {s.source_type}
                      {s.row_estimate != null ? ` · ~${s.row_estimate} rows` : ""}
                      {s.truncated ? " · truncated" : ""}
                    </p>
                    {s.columns_preview && s.columns_preview.length > 0 && (
                      <p className="text-[11px] text-slate-400 mt-0.5 truncate">
                        Columns: {s.columns_preview.join(", ")}
                      </p>
                    )}
                  </div>
                  <button
                    onClick={() => remove(s.file_id)}
                    disabled={removing === s.file_id}
                    className="shrink-0 text-slate-400 hover:text-red-700 p-1"
                    title="Remove"
                  >
                    {removing === s.file_id ? (
                      <Loader2 className="w-3.5 h-3.5 animate-spin" />
                    ) : (
                      <Trash2 className="w-3.5 h-3.5" />
                    )}
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Secrets panel ─────────────────────────────────────────────────────────────

function SecretsPanel({
  app,
  onAppUpdated,
}: {
  app: AppRecord;
  onAppUpdated: (a: AppRecord) => void;
}) {
  const [newKey, setNewKey] = useState("");
  const [newValue, setNewValue] = useState("");
  const [showValue, setShowValue] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const addSecret = async () => {
    const key = newKey.trim().toUpperCase();
    const value = newValue;
    if (!key || !value) { setError("Both key and value are required."); return; }
    if (!/^[A-Z][A-Z0-9_]{0,127}$/.test(key)) {
      setError("Key must be UPPER_SNAKE_CASE (starts with A-Z, only A-Z0-9_).");
      return;
    }
    setSaving(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/apps/${app.id}/secrets`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ secrets: { [key]: value } }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      onAppUpdated({ ...app, env_var_keys: data.env_var_keys });
      setNewKey("");
      setNewValue("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save secret.");
    } finally {
      setSaving(false);
    }
  };

  const removeSecret = async (key: string) => {
    setDeleting(key);
    setError(null);
    try {
      const res = await apiFetch(`/api/apps/${app.id}/secrets/${encodeURIComponent(key)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body?.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      onAppUpdated({ ...app, env_var_keys: data.env_var_keys });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete secret.");
    } finally {
      setDeleting(null);
    }
  };

  const skillEnvKeys: string[] = app.skill_env_keys ?? [];

  return (
    <div className="flex-1 flex flex-col overflow-hidden bg-surface">
      {/* Header */}
      <div className="px-4 py-2.5 border-b border-slate-200 shrink-0 flex items-center gap-1.5">
        <KeyRound className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Secrets</span>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-5">
        {/* From attached skills */}
        <div>
          <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Zap className="w-3 h-3 text-indigo-500" /> From attached skills
          </h3>
          {(app.skill_ids ?? []).length === 0 ? (
            <p className="text-xs text-slate-400">No skills attached. Add skills via the Skills menu in the top bar.</p>
          ) : skillEnvKeys.length === 0 ? (
            <p className="text-xs text-slate-400">
              Skills attached: {(app.skill_ids ?? []).join(", ")}. No secrets registered for these skills.
            </p>
          ) : (
            <>
              <p className="text-xs text-slate-400 mb-2">
                Automatically injected at runtime from {(app.skill_ids ?? []).join(", ")}:
              </p>
              <div className="space-y-1">
                {skillEnvKeys.map(k => (
                  <div key={k} className="flex items-center gap-2 text-xs font-mono bg-indigo-50 border border-indigo-100 rounded px-2.5 py-1.5">
                    <Zap className="w-2.5 h-2.5 text-indigo-400 shrink-0" />
                    <span className="text-indigo-800 flex-1">{k}</span>
                    <span className="text-indigo-400 text-[10px]">auto</span>
                  </div>
                ))}
              </div>
            </>
          )}
        </div>

        {/* App secrets */}
        <div>
          <h3 className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider mb-2 flex items-center gap-1.5">
            <Lock className="w-3 h-3" /> App secrets
          </h3>
          {(app.env_var_keys ?? []).length === 0 ? (
            <p className="text-xs text-slate-400 mb-3">No secrets saved yet.</p>
          ) : (
            <div className="space-y-1 mb-3">
              {(app.env_var_keys ?? []).map(k => (
                <div key={k} className="flex items-center gap-2 text-xs font-mono bg-slate-50 border border-slate-200 rounded px-2.5 py-1.5">
                  <span className="text-slate-700 flex-1">{k}</span>
                  <span className="text-slate-400 text-[10px]">••••••••</span>
                  <button
                    onClick={() => removeSecret(k)}
                    disabled={deleting === k}
                    className="text-slate-300 hover:text-red-500 transition-colors disabled:opacity-50"
                    title={`Delete ${k}`}
                  >
                    {deleting === k ? (
                      <Loader2 className="w-3 h-3 animate-spin" />
                    ) : (
                      <Trash2 className="w-3 h-3" />
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}

          {/* Add secret form */}
          <div className="space-y-2">
            <input
              type="text"
              value={newKey}
              onChange={e => setNewKey(e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, ""))}
              placeholder="KEY_NAME"
              className="w-full text-xs font-mono px-2.5 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-400"
            />
            <div className="relative">
              <input
                type={showValue ? "text" : "password"}
                value={newValue}
                onChange={e => setNewValue(e.target.value)}
                placeholder="secret value"
                className="w-full text-xs pr-8 px-2.5 py-1.5 border border-slate-200 rounded-lg focus:outline-none focus:border-indigo-400"
              />
              <button
                type="button"
                onClick={() => setShowValue(v => !v)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-700"
              >
                {showValue ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              </button>
            </div>

            {error && (
              <div className="flex items-center gap-1.5 text-xs text-red-700">
                <AlertCircle className="w-3 h-3 shrink-0" />
                <span>{error}</span>
              </div>
            )}

            <button
              onClick={addSecret}
              disabled={saving || !newKey.trim() || !newValue}
              className="w-full flex items-center justify-center gap-1.5 text-xs font-bold bg-slate-900 text-slate-50 px-3 py-1.5 rounded-lg hover:bg-slate-700 disabled:opacity-50 transition-colors"
            >
              {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Plus className="w-3 h-3" />}
              Add Secret
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Preview panel ─────────────────────────────────────────────────────────────

function PreviewPanel({
  app,
  previewKey,
  iframeRef,
  onRefresh,
}: {
  app: AppRecord;
  previewKey: number;
  iframeRef: RefObject<HTMLIFrameElement>;
  onRefresh: () => void;
}) {
  return (
    <div className="flex-1 flex flex-col overflow-hidden h-full min-w-0">
      {/* Header */}
      <div className="px-3 py-2 border-b border-slate-200 bg-surface shrink-0 flex items-center gap-2">
        <Monitor className="w-3.5 h-3.5 text-slate-400" />
        <span className="text-[11px] font-semibold text-slate-500 uppercase tracking-wider">Preview</span>
        {app.status === "deployed" && (
          <>
            <div className="flex-1 bg-slate-50 border border-slate-200 rounded px-2 py-0.5 text-[10px] text-slate-500 font-mono truncate ml-1">
              /apps/{app.slug}/
            </div>
            <button
              onClick={onRefresh}
              className="text-slate-400 hover:text-slate-700 p-1 rounded transition-colors shrink-0"
              title="Reload preview"
            >
              <RefreshCw className="w-3.5 h-3.5" />
            </button>
            <a
              href={`/apps/${app.slug}/`}
              target="_blank"
              rel="noopener noreferrer"
              className="text-slate-400 hover:text-slate-700 transition-colors shrink-0"
              title="Open in new tab"
            >
              <ExternalLink className="w-3.5 h-3.5" />
            </a>
          </>
        )}
      </div>

      {app.status !== "deployed" ? (
        <div className="flex-1 flex items-center justify-center text-center p-8 bg-slate-50">
          <div>
            <Monitor className="w-10 h-10 text-slate-200 mx-auto mb-3" />
            <p className="text-sm font-semibold text-slate-600 mb-1">Nothing deployed yet</p>
            <p className="text-xs text-slate-400 leading-relaxed">
              Describe your app in the chat and it will appear here.
            </p>
          </div>
        </div>
      ) : (
        <iframe
          key={previewKey}
          ref={iframeRef}
          src={`/apps/${app.slug}/`}
          className="flex-1 w-full border-0 bg-surface h-0"
          title={`Preview: ${app.name}`}
          sandbox="allow-scripts allow-same-origin allow-forms allow-modals"
        />
      )}
    </div>
  );
}
