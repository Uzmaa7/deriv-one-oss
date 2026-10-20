'use strict';

import { useState, useRef, useEffect, type FormEvent, type KeyboardEvent } from "react";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  Loader2,
  Send,
  ShieldCheck,
  Sparkles,
  XCircle,
} from "lucide-react";
import { apiFetch } from "@/lib/api";

type Verdict = "pass" | "fail" | "modify" | "reviewing";

interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  verdict?: Verdict;
}

interface PendingProposal {
  proposalId: string;
  summary: string;
  linesAdded: number;
  linesRemoved: number;
}

export interface AiEditPreview {
  proposedContent: string;
  verdict: Verdict;
  summary: string;
  canApply: boolean;
}

interface Props {
  slug: string;
  onApplied: () => void;
  onPreviewChange?: (preview: AiEditPreview | null) => void;
}

function verdictIcon(verdict?: Verdict) {
  if (verdict === "pass") return <ShieldCheck className="w-3.5 h-3.5 text-emerald-600 shrink-0" />;
  if (verdict === "fail") return <XCircle className="w-3.5 h-3.5 text-red-500 shrink-0" />;
  if (verdict === "modify") return <AlertCircle className="w-3.5 h-3.5 text-amber-500 shrink-0" />;
  return null;
}

export function SkillAiEditPanel({ slug, onApplied, onPreviewChange }: Props) {
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      id: "welcome",
      role: "assistant",
      content:
        "Describe the change you want in plain English — e.g. “add a troubleshooting section for timeouts”. I'll draft the edit and run quick validation before you apply.",
    },
  ]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingProposal | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, pending, busy]);

  const sendMessage = async (e?: FormEvent) => {
    e?.preventDefault();
    const text = input.trim();
    if (!text || busy) return;

    setInput("");
    setError(null);
    setPending(null);
    onPreviewChange?.(null);

    const userMsg: ChatMessage = { id: `u-${Date.now()}`, role: "user", content: text };
    setMessages(prev => [...prev, userMsg]);
    setBusy(true);

    const history = [...messages, userMsg]
      .filter(m => m.id !== "welcome")
      .slice(-12)
      .map(m => ({ role: m.role, content: m.content }));

    try {
      const res = await apiFetch(`/api/skills/${slug}/ai-edit`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: history.slice(0, -1) }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error((data as { detail?: string }).detail || `Request failed (${res.status})`);
      }

      const verdict = data.verdict as Verdict;
      const assistantMsg: ChatMessage = {
        id: `a-${Date.now()}`,
        role: "assistant",
        content: data.assistant_message || data.reason || "Done.",
        verdict,
      };
      setMessages(prev => [...prev, assistantMsg]);

      const proposed = typeof data.proposed_content === 'string' ? data.proposed_content : null;
      if (proposed && data.changed !== false) {
        onPreviewChange?.({
          proposedContent: proposed,
          verdict,
          summary: data.summary || 'Updated skill',
          canApply: !!(data.proposal_id && verdict === 'pass'),
        });
      } else {
        onPreviewChange?.(null);
      }

      if (data.proposal_id && verdict === "pass" && data.changed !== false) {
        setPending({
          proposalId: data.proposal_id,
          summary: data.summary || "Updated skill",
          linesAdded: data.lines_added ?? 0,
          linesRemoved: data.lines_removed ?? 0,
        });
      }
    } catch (err) {
      const msg = err instanceof Error ? err.message : "AI edit failed.";
      setError(msg);
      setMessages(prev => [
        ...prev,
        { id: `e-${Date.now()}`, role: "assistant", content: msg, verdict: "fail" },
      ]);
    } finally {
      setBusy(false);
      inputRef.current?.focus();
    }
  };

  const handleApply = async () => {
    if (!pending || applying) return;
    setApplying(true);
    setError(null);
    try {
      const res = await apiFetch(`/api/skills/${slug}/ai-edit/apply`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ proposal_id: pending.proposalId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const detail = (data as { detail?: unknown }).detail;
        if (res.status === 409 && detail && typeof detail === "object" && (detail as { code?: string }).code === "skill_conflict") {
          setPending(null);
          onPreviewChange?.(null);
          const msg = (detail as { message?: string }).message
            || "Skill was updated by someone else. Ask for a new edit and apply again.";
          throw new Error(msg);
        }
        const fallback = typeof detail === "string" ? detail : (detail as { message?: string })?.message;
        throw new Error(fallback || `Apply failed (${res.status})`);
      }
      setPending(null);
      onPreviewChange?.(null);
      setMessages(prev => [
        ...prev,
        {
          id: `ok-${Date.now()}`,
          role: "assistant",
          content: `Applied: ${data.summary || pending.summary}. The live skill file has been updated.`,
          verdict: "pass",
        },
      ]);
      onApplied();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not apply changes.");
    } finally {
      setApplying(false);
    }
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void sendMessage();
    }
  };

  return (
    <section className="border border-slate-200 rounded-xl bg-white overflow-hidden flex flex-col min-h-[22rem]">
      <div className="px-3 py-3 border-b border-slate-100 flex items-center gap-2 bg-gradient-to-r from-indigo-50/80 to-white shrink-0">
        <Sparkles className="w-4 h-4 text-indigo-600 shrink-0" />
        <div className="min-w-0 flex-1">
          <h3 className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">AI Edit</h3>
        </div>
      </div>

      <div ref={scrollRef} className="flex-1 min-h-[14rem] max-h-[22rem] overflow-y-auto px-3 py-3 space-y-3">
        {messages.map(m => (
          <div
            key={m.id}
            className={`flex gap-2 text-sm leading-relaxed ${
              m.role === "user" ? "justify-end" : "justify-start"
            }`}
          >
            {m.role === "assistant" && (
              <Bot className="w-4 h-4 text-indigo-400 shrink-0 mt-0.5" />
            )}
            <div
              className={`rounded-lg px-3 py-2 max-w-[94%] whitespace-pre-wrap ${
                m.role === "user"
                  ? "bg-slate-900 text-white"
                  : "bg-slate-50 text-slate-700 border border-slate-100"
              }`}
            >
              <div className="flex items-start gap-1">
                {m.role === "assistant" && verdictIcon(m.verdict)}
                <span>{m.content}</span>
              </div>
            </div>
          </div>
        ))}
        {busy && (
          <div className="flex items-center gap-2 text-sm text-slate-400 px-1">
            <Loader2 className="w-4 h-4 animate-spin" />
            Editing &amp; reviewing…
          </div>
        )}
      </div>

      {pending && (
        <div className="mx-3 mb-3 p-3 rounded-lg border border-emerald-200 bg-emerald-50/80 shrink-0">
          <div className="flex items-start gap-2 text-xs text-emerald-900">
            <CheckCircle2 className="w-3.5 h-3.5 shrink-0 mt-0.5" />
            <div className="flex-1 min-w-0">
              <p className="font-semibold truncate">{pending.summary}</p>
              <p className="text-emerald-700/80">
                +{pending.linesAdded} / −{pending.linesRemoved} lines · ready to apply
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={() => void handleApply()}
            disabled={applying}
            className="mt-2.5 w-full py-2 text-sm font-bold bg-emerald-600 text-white rounded-md hover:bg-emerald-700 disabled:opacity-50 flex items-center justify-center gap-1.5"
          >
            {applying ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <ShieldCheck className="w-3.5 h-3.5" />}
            {applying ? "Applying…" : "Apply changes"}
          </button>
        </div>
      )}

      {error && (
        <p className="mx-3 mb-1 text-[10px] text-red-600 flex items-center gap-1">
          <AlertCircle className="w-3 h-3 shrink-0" /> {error}
        </p>
      )}

      <form onSubmit={sendMessage} className="border-t border-slate-100 p-3 flex gap-2 items-end shrink-0">
        <textarea
          ref={inputRef}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={3}
          maxLength={4000}
          disabled={busy || applying}
          placeholder="Ask for a change… (Enter to send, Shift+Enter for new line)"
          className="flex-1 resize-none text-sm px-3 py-2 border border-slate-200 rounded-lg focus:outline-none focus:ring-2 focus:ring-indigo-200 disabled:opacity-50 min-h-[4.5rem]"
        />
        <button
          type="submit"
          disabled={busy || applying || !input.trim()}
          className="p-2.5 rounded-lg bg-indigo-600 text-white hover:bg-indigo-700 disabled:opacity-40 shrink-0"
          title="Send"
        >
          {busy ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
        </button>
      </form>
    </section>
  );
}
