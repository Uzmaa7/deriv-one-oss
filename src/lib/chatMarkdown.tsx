import { useRef, useEffect } from "react";
import type { MentionKind } from "@/src/components/composer/mention/types";

// Shared chat-message rendering helpers, extracted out of WorkspacePage.tsx so
// components outside the Workspace page (e.g. ProposalCard) can reuse them
// without importing a 2000+ line page module.

export interface GeneratedFile {
  filename: string;
  url: string;
  mime: string;
  sizeBytes: number;
}

export interface ProposalMarker {
  id: string;
  kind: MentionKind;
  name: string;
}

export interface ToolStep {
  id: string;
  label: string;
  status: "running" | "done" | "error";
  durationMs?: number;
  output?: string;
}

// ── Generated-file marker parser ──────────────────────────────────────────────
// The backend appends <!-- GENFILE:{...} --> markers to message content so file
// cards survive page reloads (the DB only stores the raw text).

const _GENFILE_RE = /<!-- GENFILE:(\{[^}]+\}) -->/g;

export function parseGenFiles(content: string): { cleaned: string; files: GeneratedFile[] } {
  const files: GeneratedFile[] = [];
  const cleaned = content.replace(_GENFILE_RE, (_match, json) => {
    try {
      const d = JSON.parse(json);
      files.push({
        filename: d.f ?? "",
        url: d.u ?? "#",
        mime: d.m ?? "application/octet-stream",
        sizeBytes: d.s ?? 0,
      });
    } catch {
      // ignore malformed markers
    }
    return "";
  });
  return { cleaned: cleaned.trim(), files };
}

// ── Proposal marker parser ────────────────────────────────────────────────────
// Mirrors GENFILE above: the backend appends <!-- PROPOSAL:{...} --> markers so
// a drafted Skill/Automation/Lab proposal card survives page reloads.

const _PROPOSAL_RE = /<!-- PROPOSAL:(\{[^}]+\}) -->/g;

const MENTION_KINDS: readonly MentionKind[] = ["skill", "automation", "lab"];

function isMentionKind(v: unknown): v is MentionKind {
  return typeof v === "string" && (MENTION_KINDS as readonly string[]).includes(v);
}

export function parseProposalMarker(content: string): { cleaned: string; proposal: ProposalMarker | null } {
  let proposal: ProposalMarker | null = null;
  const cleaned = content.replace(_PROPOSAL_RE, (_match, json) => {
    try {
      const d = JSON.parse(json);
      // Two payload shapes exist in persisted chat history: the original
      // {"id","kind"} form and the current short {"i","k","n"} form. Messages
      // written before the rename are still in the DB, so read both — and only
      // emit a marker when id/kind are actually valid, since `kind` drives a
      // lookup into fixed metadata tables downstream.
      const id: unknown = d?.i ?? d?.id;
      const rawKind: unknown = d?.k ?? d?.kind;
      const rawName: unknown = d?.n ?? d?.name;
      if (typeof id === "string" && id && isMentionKind(rawKind)) {
        proposal = {
          id,
          kind: rawKind,
          name: typeof rawName === "string" ? rawName : "",
        };
      }
    } catch {
      // ignore malformed markers
    }
    return "";
  });
  return { cleaned: cleaned.trim(), proposal };
}

// ── Tool-step marker parser ───────────────────────────────────────────────────
// Backend appends <!-- TOOLSTEPS:[...] --> so tool cascades survive reloads.

const _TOOLSTEPS_RE = /<!-- TOOLSTEPS:(\[[\s\S]*?\]) -->/g;

function _stepStatus(raw: unknown): ToolStep["status"] {
  if (raw === "running" || raw === "done" || raw === "error") return raw;
  return "done";
}

export function parseToolSteps(content: string): { cleaned: string; steps: ToolStep[] } {
  const steps: ToolStep[] = [];
  const cleaned = content.replace(_TOOLSTEPS_RE, (_match, json) => {
    try {
      const parsed = JSON.parse(json) as unknown;
      if (!Array.isArray(parsed)) return "";
      for (const item of parsed) {
        if (!item || typeof item !== "object") continue;
        const row = item as Record<string, unknown>;
        const id = String(row.i ?? row.id ?? "");
        if (!id) continue;
        steps.push({
          id,
          label: String(row.l ?? row.label ?? "Running…"),
          status: _stepStatus(row.s ?? row.status),
          durationMs: typeof row.d === "number" ? row.d : typeof row.duration_ms === "number" ? row.duration_ms : undefined,
          output: typeof row.o === "string" ? row.o : typeof row.output === "string" ? row.output : undefined,
        });
      }
    } catch {
      // ignore malformed markers
    }
    return "";
  });
  return { cleaned: cleaned.trim(), steps };
}

export function hydrateMessageContent(content: string): {
  content: string;
  files: GeneratedFile[];
  proposal: ProposalMarker | null;
  steps: ToolStep[];
} {
  const { cleaned: afterSteps, steps } = parseToolSteps(content);
  const { cleaned: afterFiles, files } = parseGenFiles(afterSteps);
  const { cleaned, proposal } = parseProposalMarker(afterFiles);
  return { content: cleaned, files, proposal, steps };
}

// ── Inline markdown transformer ──────────────────────────────────────────────
export function applyInline(rawText: string): string {
  // Escape raw HTML BEFORE applying any markdown formatting below. This text
  // can originate from untrusted sources (chat messages, fetched pages, tool
  // output, or data an LLM echoes back verbatim — e.g. a calendar event title)
  // and is injected via dangerouslySetInnerHTML, so a literal `<img onerror=...>`
  // or `<a href="javascript:...">` must never reach the DOM unescaped. Only `<`
  // and `&` need escaping — `>` alone cannot open a tag/attribute, and leaving
  // it as-is keeps this drop-in compatible with every regex below.
  const text = rawText.replace(/&/g, "&amp;").replace(/</g, "&lt;");
  // Use [^*\n]+ instead of .+? to prevent catastrophic backtracking on unmatched asterisks
  return text
    .replace(/\[([^\]]+)\]\((\/api\/files\/[^)]+)\)/g, (_, t, url) => {
      const name = url.split("/").pop() ?? "document.pdf";
      return `<a href="${url}" data-pdf-url="${url}" data-pdf-name="${name}" class="inline-flex items-center gap-1.5 text-[#FF444F] hover:text-[#e63d48] underline underline-offset-2 font-medium cursor-pointer">📄 ${t}</a>`;
    })
    .replace(/\*\*([^*\n]+)\*\*/g, '<strong class="font-semibold text-[#0E0E0E]">$1</strong>')
    .replace(/\*([^*\n]+)\*/g, '<em class="italic text-slate-500">$1</em>')
    .replace(/`([^`\n]+)`/g, '<code class="bg-[#FFF0F0] text-[#FF444F] px-1 py-0.5 rounded text-[0.85em] font-mono border border-[#FF444F]/15">$1</code>');
}

export function isBlockLine(line: string): boolean {
  const t = line.trim();
  return /^#{1,6} /.test(t) || /^[-*] /.test(t) || /^\d+[.)]\s/.test(t) ||
    t.startsWith("> ") || t.startsWith("|") || /^---+$/.test(t) ||
    t.startsWith("```") || /^<(pre|div|h[1-6]|blockquote|hr|ul|ol|table)[\s/>]/.test(t);
}

// ── Markdown renderer ─────────────────────────────────────────────────────────

export function renderMarkdown(content: string): string {
  try {
    // First pass: extract CLOSED fenced code blocks so they're never line-parsed
    const codeBlocks: string[] = [];
    const withPlaceholders = content.replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) => {
      const esc = code.replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const badge = lang
        ? `<span class="block text-[10px] font-mono font-bold text-[#FF444F] uppercase tracking-wider mb-1.5 select-none">${lang}</span>`
        : "";
      codeBlocks.push(
        `<pre class="bg-[#F8F8F9] border border-slate-200 rounded-lg px-4 py-3 overflow-x-auto text-xs font-mono text-slate-800 leading-relaxed whitespace-pre my-2">${badge}${esc.trimEnd()}</pre>`
      );
      return `\x02CODE${codeBlocks.length - 1}\x03`;
    });

    const lines = withPlaceholders.split("\n");
    const out: string[] = [];
    let i = 0;

    while (i < lines.length) {
      const raw = lines[i];
      const line = raw.trim();

      // Code block placeholder (from closed fences extracted above)
      if (line.startsWith("\x02CODE") && line.endsWith("\x03")) {
        const idx = parseInt(line.slice(5, -1), 10);
        if (!isNaN(idx) && codeBlocks[idx] !== undefined) out.push(codeBlocks[idx]);
        i++; continue;
      }

      // Orphaned opening fence (unclosed code block — common during streaming)
      if (line.startsWith("```")) {
        const lang = line.slice(3).trim();
        const codeLines: string[] = [];
        i++;
        while (i < lines.length && !lines[i].trim().startsWith("```")) {
          codeLines.push(lines[i].replace(/</g, "&lt;").replace(/>/g, "&gt;"));
          i++;
        }
        if (lines[i]?.trim().startsWith("```")) i++;
        if (codeLines.length > 0) {
          const badge = lang
            ? `<span class="block text-[10px] font-mono font-bold text-[#FF444F] uppercase tracking-wider mb-1.5 select-none">${lang}</span>`
            : "";
          out.push(`<pre class="bg-[#F8F8F9] border border-slate-200 rounded-lg px-4 py-3 overflow-x-auto text-xs font-mono text-slate-800 leading-relaxed whitespace-pre my-2">${badge}${codeLines.join("\n").trimEnd()}</pre>`);
        }
        continue;
      }

      // Blank line — skip
      if (!line) { i++; continue; }

      // Horizontal rule
      if (/^---+$/.test(line)) {
        out.push('<hr class="border-[#FF444F]/20 my-2" />');
        i++; continue;
      }

      // Heading
      const hm = line.match(/^(#{1,4})\s+(.*)/);
      if (hm) {
        const lvl = hm[1].length;
        const cls = [
          "text-base font-bold text-[#0E0E0E] mt-3 mb-1",
          "text-sm font-bold text-[#0E0E0E] mt-3 mb-1 pb-1 border-b border-[#FF444F]/20",
          "text-sm font-semibold text-[#0E0E0E] mt-2 mb-0.5",
          "text-xs font-bold text-slate-600 mt-2 mb-0.5 uppercase tracking-wide",
        ][lvl - 1];
        out.push(`<h${lvl} class="${cls}">${applyInline(hm[2])}</h${lvl}>`);
        i++; continue;
      }

      // Blockquote
      if (line.startsWith("> ")) {
        out.push(`<blockquote class="border-l-4 border-[#FF444F] bg-[#FFF0F0]/50 pl-3 py-1 pr-3 my-1.5 text-sm text-slate-700 rounded-r italic">${applyInline(line.slice(2))}</blockquote>`);
        i++; continue;
      }

      // Table — collect all consecutive pipe lines
      if (line.startsWith("|")) {
        const tLines: string[] = [];
        while (i < lines.length && lines[i].trim().startsWith("|")) { tLines.push(lines[i]); i++; }
        const rows = tLines.filter(r => !/^\s*\|[\s|:-]+\|\s*$/.test(r));
        if (rows.length >= 1) {
          const parseRow = (r: string) => r.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
          const header = parseRow(rows[0]);
          const body = rows.slice(1);
          const ths = header.map(h => `<th class="px-3 py-2 text-left text-[11px] font-bold text-[#FF444F] uppercase tracking-wider border-b-2 border-[#FF444F]/20 bg-[#FFF0F0]/60 whitespace-nowrap">${applyInline(h)}</th>`).join("");
          const trs = body.map((r, ri) =>
            `<tr class="${ri % 2 === 0 ? "bg-white" : "bg-slate-50/40"} hover:bg-[#FFF8F8] border-b border-slate-100">${
              parseRow(r).map(c => `<td class="px-3 py-2 text-sm text-slate-700 align-top">${applyInline(c)}</td>`).join("")
            }</tr>`
          ).join("");
          out.push(`<div class="overflow-x-auto my-2 rounded-xl border border-slate-200 shadow-sm"><table class="w-full border-collapse"><thead><tr>${ths}</tr></thead><tbody>${trs}</tbody></table></div>`);
        }
        continue;
      }

      // Unordered list — collect consecutive items
      if (/^[-*] /.test(line)) {
        const items: string[] = [];
        while (i < lines.length && /^[-*] /.test(lines[i].trim())) {
          const m = lines[i].trim().match(/^[-*] (.*)/);
          items.push(`<li class="flex items-start gap-1.5"><span class="mt-[5px] w-1.5 h-1.5 rounded-full bg-[#FF444F]/60 shrink-0"></span><span>${applyInline(m![1])}</span></li>`);
          i++;
        }
        out.push(`<ul class="my-1.5 space-y-0.5 text-sm text-slate-700">${items.join("")}</ul>`);
        continue;
      }

      // Ordered list — collect consecutive items
      if (/^\d+[.)]\s/.test(line)) {
        const items: string[] = [];
        while (i < lines.length && /^\d+[.)]\s/.test(lines[i].trim())) {
          const m = lines[i].trim().match(/^(\d+)[.)]\s(.*)/);
          items.push(`<li class="flex items-start gap-1.5"><span class="text-[11px] font-bold text-[#FF444F] shrink-0 mt-0.5 w-4 text-right">${m![1]}.</span><span>${applyInline(m![2])}</span></li>`);
          i++;
        }
        out.push(`<ol class="my-1.5 space-y-0.5 text-sm text-slate-700">${items.join("")}</ol>`);
        continue;
      }

      // Plain text paragraph — collect consecutive non-block lines
      const paraLines: string[] = [];
      while (i < lines.length && lines[i].trim() && !isBlockLine(lines[i])) {
        paraLines.push(lines[i].trim());
        i++;
      }
      if (paraLines.length) {
        out.push(`<p class="text-sm text-slate-700 leading-snug my-0.5">${paraLines.map(applyInline).join("<br/>")}</p>`);
        continue;
      }

      // Safety fallback: force increment to prevent infinite loop
      i++;
    }

    return out.join("\n");
  } catch {
    return `<p class="text-sm text-slate-700 leading-snug whitespace-pre-wrap">${content.replace(/</g, "&lt;").replace(/>/g, "&gt;")}</p>`;
  }
}

// ── Code block (display-only; execution happens server-side via tool calls) ───

type ContentPart =
  | { type: "text"; content: string }
  | { type: "code"; lang: string; code: string };

export function parseContentParts(content: string): ContentPart[] {
  const parts: ContentPart[] = [];
  const regex = /```(\w*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  while ((match = regex.exec(content)) !== null) {
    if (match.index > lastIndex) parts.push({ type: "text", content: content.slice(lastIndex, match.index) });
    parts.push({ type: "code", lang: match[1], code: match[2] });
    lastIndex = match.index + match[0].length;
  }
  if (lastIndex < content.length) parts.push({ type: "text", content: content.slice(lastIndex) });
  return parts;
}

export function CodeBlock({ lang, code }: { lang: string; code: string }) {
  return (
    <div className="my-3 rounded-lg overflow-hidden border border-slate-200">
      <div className="flex items-center justify-between bg-[#F8F8F9] px-4 py-1.5 border-b border-slate-200">
        <span className="text-[10px] text-[#FF444F] font-mono font-semibold uppercase tracking-wider">{lang || "code"}</span>
      </div>
      <pre className="bg-[#F8F8F9] text-slate-800 px-4 py-3 overflow-x-auto text-xs font-mono leading-relaxed whitespace-pre">
        <code>{code}</code>
      </pre>
    </div>
  );
}

// ── Assistant message ─────────────────────────────────────────────────────────

export function AssistantMessage({ content, onOpenPdf }: {
  content: string;
  onOpenPdf: (url: string, name: string, docType?: "pdf" | "docx") => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const parts = parseContentParts(content);

  // Intercept clicks on rendered /api/files/ links
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const handler = (e: Event) => {
      const target = (e.target as HTMLElement).closest("[data-pdf-url]") as HTMLElement | null;
      if (!target) return;
      e.preventDefault();
      const url = target.dataset.pdfUrl!;
      const name = target.dataset.pdfName ?? url.split("/").pop() ?? "document.pdf";
      onOpenPdf(url, name);
    };
    el.addEventListener("click", handler);
    return () => el.removeEventListener("click", handler);
  }, [onOpenPdf]);

  return (
    <div ref={containerRef}>
      {/* Render prose + code inline — code in the answer is not "steps" */}
      {parts.map((part, i) =>
        part.type === "text"
          ? (
            part.content.trim()
              ? <div key={i} dangerouslySetInnerHTML={{ __html: renderMarkdown(part.content) }} className="prose-sm" />
              : null
          )
          : <div key={i}><CodeBlock lang={part.lang} code={part.code} /></div>
      )}
    </div>
  );
}
