'use strict';

import { diffLines, diffStats, type DiffLine } from "@/lib/lineDiff";

interface Props {
  current: string;
  proposed: string;
  summary?: string;
  verdict?: 'pass' | 'fail' | 'modify' | 'reviewing';
  variant?: 'proposal' | 'history';
  collapseUnchanged?: boolean;
}

const CONTEXT_LINES = 3;

function collapseDiffContext(lines: DiffLine[], contextLines = CONTEXT_LINES): DiffLine[] {
  const changeIndices = lines
    .map((line, index) => (line.type !== 'equal' ? index : -1))
    .filter(index => index >= 0);

  if (changeIndices.length === 0) return lines;

  const keep = new Set<number>();
  for (const index of changeIndices) {
    const start = Math.max(0, index - contextLines);
    const end = Math.min(lines.length - 1, index + contextLines);
    for (let i = start; i <= end; i++) keep.add(i);
  }

  const sorted = [...keep].sort((a, b) => a - b);
  const result: DiffLine[] = [];
  let lastIndex = -1;

  for (const index of sorted) {
    if (lastIndex >= 0 && index > lastIndex + 1) {
      result.push({ type: 'equal', line: '···' });
    }
    result.push(lines[index]);
    lastIndex = index;
  }

  return result;
}

export function SkillMarkdownDiff({
  current,
  proposed,
  summary,
  verdict,
  variant = 'proposal',
  collapseUnchanged = false,
}: Props) {
  const rawLines = diffLines(current, proposed);
  const lines = collapseUnchanged ? collapseDiffContext(rawLines) : rawLines;
  const { added, removed } = diffStats(rawLines);

  const verdictLabel =
    variant === 'history'
      ? 'Changes'
      : verdict === 'pass'
        ? 'Security passed'
        : verdict === 'fail'
          ? 'Security rejected'
          : verdict === 'modify'
            ? 'Needs changes'
            : 'Preview';

  const verdictClass =
    variant === 'history'
      ? 'bg-white text-slate-800 border-slate-200'
      : verdict === 'pass'
        ? 'bg-emerald-50 text-emerald-800 border-emerald-200'
        : verdict === 'fail'
          ? 'bg-red-50 text-red-800 border-red-200'
          : verdict === 'modify'
            ? 'bg-amber-50 text-amber-800 border-amber-200'
            : 'bg-slate-50 text-slate-700 border-slate-200';

  return (
    <div className="space-y-3">
      <div className={`flex flex-wrap items-center gap-x-2 gap-y-1 px-3 py-2.5 rounded-lg border text-xs ${verdictClass}`}>
        <span className="font-semibold text-slate-900">{verdictLabel}</span>
        <span className="text-slate-300">|</span>
        <span className="font-mono font-medium text-emerald-700">+{added}</span>
        <span className="font-mono font-medium text-red-600">−{removed}</span>
        {summary && (
          <>
            <span className="text-slate-300">|</span>
            <span className="text-slate-600">{summary}</span>
          </>
        )}
        {collapseUnchanged && (added > 0 || removed > 0) && (
          <span className="text-slate-400 ml-auto">Unchanged lines hidden</span>
        )}
        {variant === 'proposal' && (
          <span className="ml-auto text-[10px] font-medium text-slate-500">Not live until Apply</span>
        )}
      </div>

      <div className="border border-slate-200 rounded-xl overflow-hidden bg-slate-50">
        <pre className="text-xs font-mono leading-relaxed max-h-[28rem] overflow-y-auto">
          {lines.map((row, idx) => {
            const isEllipsis = row.type === 'equal' && row.line === '···';
            const prefix = isEllipsis ? ' ' : row.type === 'add' ? '+' : row.type === 'remove' ? '−' : ' ';
            const rowClass = isEllipsis
              ? 'bg-slate-100 text-slate-400 italic'
              : row.type === 'add'
                ? 'bg-emerald-50 text-emerald-900'
                : row.type === 'remove'
                  ? 'bg-red-50 text-red-900'
                  : 'bg-white text-slate-600';

            return (
              <div key={idx} className={`flex border-b border-slate-100 last:border-b-0 ${rowClass}`}>
                <span className="select-none w-8 shrink-0 text-center text-slate-400 border-r border-slate-200/80 py-1">
                  {prefix}
                </span>
                <span className="px-3 py-1 whitespace-pre-wrap break-words flex-1 min-w-0">
                  {isEllipsis ? '··· unchanged lines hidden ···' : row.line || '\u00a0'}
                </span>
              </div>
            );
          })}
        </pre>
      </div>
    </div>
  );
}
