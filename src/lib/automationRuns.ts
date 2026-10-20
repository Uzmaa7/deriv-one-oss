// Shared automation-run rendering helpers, extracted out of AutomationsPage.tsx
// so ProposalCard.tsx (inline "Test run" in the chat proposal card) can reuse
// the exact same output/error presentation without duplicating it.

export interface Run {
  id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  output: string | null;
  error: string | null;
  duration_secs: number | null;
}

export function fmtDuration(secs: number | null) {
  if (secs === null) return "—";
  if (secs < 60) return `${secs.toFixed(1)}s`;
  return `${(secs / 60).toFixed(1)}m`;
}

export function renderRunMarkdown(content: string): string {
  // Extract fenced code blocks first (escaped independently, swapped for a
  // placeholder token) so the rest of the string can be HTML-escaped below
  // without corrupting the <pre> markup we just built for the code itself.
  const codeBlocks: string[] = [];
  const withPlaceholders = content.replace(/```(\w*)\n([\s\S]*?)```/g, (_, _lang, code) => {
    const escapedCode = code.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    codeBlocks.push(
      `<pre class="bg-slate-900 text-slate-200 rounded-lg p-4 overflow-x-auto my-3 text-xs font-mono leading-relaxed"><code>${escapedCode}</code></pre>`
    );
    return `\x02CODE${codeBlocks.length - 1}\x03`;
  });

  // Escape everything else — run output/error text and automation names can
  // come from untrusted sources (LLM-generated reports, calendar/skill/tool
  // data), and this is injected via dangerouslySetInnerHTML. Only `<`/`&` need
  // escaping — `>` alone can't open a tag, and leaving it as-is keeps the
  // blockquote (`> `) regex below working unmodified.
  const escaped = withPlaceholders.replace(/&/g, "&amp;").replace(/</g, "&lt;");

  return escaped
    .replace(/(^\|.+\|$\n^\|[-| :]+\|$(\n^\|.+\|$)*)/gm, (table) => {
      const rows = table.trim().split("\n");
      const header = rows[0].split("|").filter(Boolean).map(c =>
        `<th class="px-4 py-2 text-left text-xs font-bold text-slate-500 uppercase tracking-wider border-b border-slate-200 bg-slate-50">${c.trim()}</th>`
      ).join("");
      const body = rows.slice(2).map(r => {
        const cells = r.split("|").filter(Boolean).map(c =>
          `<td class="px-4 py-2 text-sm text-slate-700 border-b border-slate-100">${c.trim()}</td>`
        ).join("");
        return `<tr class="hover:bg-slate-50">${cells}</tr>`;
      }).join("");
      return `<div class="overflow-x-auto my-3 rounded-lg border border-slate-200"><table class="w-full"><thead><tr>${header}</tr></thead><tbody>${body}</tbody></table></div>`;
    })
    .replace(/^> (.+)$/gm, '<blockquote class="border-l-4 border-amber-400 bg-amber-50 pl-4 py-1.5 pr-3 my-2 text-sm text-slate-700 rounded-r">$1</blockquote>')
    .replace(/^### (.+)$/gm, '<h3 class="text-base font-bold text-slate-900 mt-4 mb-2">$1</h3>')
    .replace(/^## (.+)$/gm, '<h2 class="text-lg font-bold text-slate-900 mt-5 mb-2">$1</h2>')
    .replace(/^# (.+)$/gm, '<h1 class="text-xl font-bold text-slate-900 mt-5 mb-3">$1</h1>')
    .replace(/\*\*(.+?)\*\*/g, '<strong class="font-bold text-slate-900">$1</strong>')
    .replace(/\*(.+?)\*/g, '<em>$1</em>')
    .replace(/`([^`]+)`/g, '<code class="bg-slate-100 text-slate-800 px-1.5 py-0.5 rounded text-xs font-mono">$1</code>')
    .replace(/^- (.+)$/gm, '<li class="ml-4 text-slate-700 text-sm mb-1">• $1</li>')
    .replace(/\n\n/g, '<br/><br/>')
    .replace(/\n/g, '<br/>')
    .replace(/\x02CODE(\d+)\x03/g, (_, idx: string) => codeBlocks[Number(idx)]);
}

export function extractFinalOutput(raw: string): string {
  // Walk lines backwards to find the last standalone closing ``` fence
  const lines = raw.split("\n");
  let lastClosingIdx = -1;
  for (let i = lines.length - 1; i >= 0; i--) {
    if (lines[i].trim() === "```") {
      lastClosingIdx = i;
      break;
    }
  }
  if (lastClosingIdx !== -1) {
    const remaining = lines.slice(lastClosingIdx + 1).join("\n").trim();
    if (remaining.length > 20) return remaining;
  }
  // Fallback: strip all fenced code blocks and return prose
  return raw.replace(/```[\w\s]*\n[\s\S]*?```/g, "").replace(/\n{3,}/g, "\n\n").trim();
}

export function humanizeRunError(raw: string | null | undefined): string {
  const text = (raw || "").toLowerCase();
  if (!raw) return "This run failed. Open the details below for more context.";
  if (text.includes("not_in_channel") || text.includes("not a member")) {
    return "The bot is not in that Slack channel. Invite it with /invite, then run again.";
  }
  if (text.includes("channel_not_found")) {
    return "That Slack channel was not found. Check the name and try again.";
  }
  if (text.includes("http") && /\b(401|403)\b/.test(text)) {
    return "A connected service rejected the request. Check that your connections are still valid.";
  }
  if (text.includes("timeout") || text.includes("timed out")) {
    return "The run timed out. Try a smaller task, or run it again.";
  }
  const cleaned = raw.replace(/\s+/g, " ").trim();
  return cleaned.length > 280 ? `${cleaned.slice(0, 277)}…` : cleaned;
}
