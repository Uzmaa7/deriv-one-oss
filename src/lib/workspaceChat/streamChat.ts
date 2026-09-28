import { apiFetch } from "@/lib/api";
import { trackEvent } from "@/lib/analytics";
import type { MentionKind } from "@/src/components/composer/mention/types";
import type { GeneratedFile, ProposalMarker, ToolStep } from "@/src/lib/chatMarkdown";
import type { Message, PendingAttachment, Tab } from "./types";
import { isPersistedChatId } from "./types";

export const chatAbortControllers = new Map<string, AbortController>();

export interface StreamHandlers {
  onUpdateMessages: (msgs: Message[]) => void;
  onSetStreaming: (v: boolean) => void;
  onSetStreamingStatus: (status: string) => void;
  onSetSkill: (skillId: string | null, auto?: boolean) => void;
  onSetResolvedModel: (model: string) => void;
  onSkillDisambiguation: (value: {
    candidates: { slug: string; confidence: number; reason: string }[];
    pending: { newMessages: Message[]; sentAttachments: PendingAttachment[] };
  }) => void;
  isFocusedTab: () => boolean;
  onClearPendingCreate?: () => void;
  onStreamStart?: () => void;
  onStreamEnd?: () => void;
}

export function isLocalChatStreamActive(tabId: string): boolean {
  return chatAbortControllers.has(tabId);
}

export interface SendChatParams {
  tab: Tab;
  newMessages: Message[];
  userContent: string;
  sentAttachments: PendingAttachment[];
  manualSkillId: string | null;
  noSkill: boolean;
  thinkingEnabled: boolean;
  createKind?: MentionKind | null;
  handlers: StreamHandlers;
}

export async function runChatStream({
  tab,
  newMessages,
  userContent,
  sentAttachments,
  manualSkillId,
  noSkill,
  thinkingEnabled,
  createKind,
  handlers,
}: SendChatParams): Promise<void> {
  const {
    onUpdateMessages,
    onSetStreaming,
    onSetStreamingStatus,
    onSetSkill,
    onSetResolvedModel,
    onSkillDisambiguation,
    isFocusedTab,
    onClearPendingCreate,
    onStreamStart,
    onStreamEnd,
  } = handlers;

  onSetStreaming(true);
  onSetStreamingStatus("");
  onStreamStart?.();

  try {
    if (!isPersistedChatId(tab.id)) {
      onUpdateMessages([
        ...newMessages,
        {
          role: "model",
          content: "Could not reach the server. Check that the backend is running, then try again.",
        },
      ]);
      return;
    }

    const controller = new AbortController();
    chatAbortControllers.set(tab.id, controller);
    const res = await apiFetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: controller.signal,
      body: JSON.stringify({
        chat_id: tab.id,
        message: userContent,
        skill_id: manualSkillId,
        active_skill_hint:
          !noSkill && manualSkillId == null && tab.activeSkillAuto && tab.activeSkillId
            ? tab.activeSkillId
            : null,
        no_skill: noSkill,
        thinking: thinkingEnabled,
        attachments: sentAttachments.map(a => ({ id: a.id, mime: a.mime })),
        model: tab.selectedModel || undefined,
        create_kind: createKind ?? undefined,
      }),
    });

    if (!res.ok || !res.body) {
      throw new Error(`Server error ${res.status}: ${res.statusText}`);
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let accumulated = "";
    let currentThinking = "";
    let currentSteps: ToolStep[] = [];
    let currentFiles: GeneratedFile[] = [];
    let currentProposal: ProposalMarker | undefined;

    const updateStreamingMsg = () => {
      onUpdateMessages([
        ...newMessages,
        {
          role: "model",
          content: accumulated,
          thinking: currentThinking || undefined,
          steps: currentSteps.length > 0 ? [...currentSteps] : undefined,
          files: currentFiles.length > 0 ? [...currentFiles] : undefined,
          proposal: currentProposal,
        },
      ]);
    };

    let streamComplete = false;
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop() ?? "";

      for (const part of parts) {
        const line = part.trim();
        if (!line.startsWith("data: ")) continue;
        let parsed: {
          type: string;
          content?: string;
          label?: string;
          ok?: boolean;
          id?: string;
          duration_ms?: number;
          output?: string;
          filename?: string;
          url?: string;
          mime?: string;
          size_bytes?: number;
          model?: string;
          slug?: string;
          candidates?: { slug: string; confidence: number; reason: string }[];
          proposal_id?: string;
          kind?: string;
          name?: string;
        };
        try {
          parsed = JSON.parse(line.slice(6));
        } catch {
          continue;
        }

        if (parsed.type === "text") {
          accumulated += parsed.content ?? "";
          onSetStreamingStatus("");
          updateStreamingMsg();
        } else if (parsed.type === "thinking") {
          currentThinking += parsed.content ?? "";
          updateStreamingMsg();
        } else if (parsed.type === "step_start") {
          currentSteps = [
            ...currentSteps,
            {
              id: parsed.id ?? `step-${Date.now()}`,
              label: parsed.label ?? "Running…",
              status: "running",
            },
          ];
          onSetStreamingStatus(parsed.label ?? "Running code…");
          updateStreamingMsg();
        } else if (parsed.type === "step_end") {
          currentSteps = currentSteps.map(s =>
            s.id === parsed.id
              ? {
                  ...s,
                  status: parsed.ok ? "done" : "error",
                  durationMs: parsed.duration_ms,
                  output: parsed.output,
                }
              : s,
          );
          onSetStreamingStatus("Analysing results…");
          updateStreamingMsg();
        } else if (parsed.type === "proposal_created" && parsed.proposal_id && parsed.kind && parsed.name) {
          currentProposal = { id: parsed.proposal_id, kind: parsed.kind as MentionKind, name: parsed.name };
          updateStreamingMsg();
          if (isFocusedTab()) onClearPendingCreate?.();
        } else if (parsed.type === "file_generated") {
          currentFiles = [
            ...currentFiles,
            {
              filename: parsed.filename ?? "file",
              url: parsed.url ?? "#",
              mime: parsed.mime ?? "application/octet-stream",
              sizeBytes: parsed.size_bytes ?? 0,
            },
          ];
          updateStreamingMsg();
        } else if (parsed.type === "tool_start") {
          onSetStreamingStatus(parsed.label ?? "Running code…");
        } else if (parsed.type === "tool_end") {
          onSetStreamingStatus("Analysing results…");
        } else if (parsed.type === "error") {
          onUpdateMessages([
            ...newMessages,
            { role: "model", content: `**Error:** ${parsed.content ?? "Something went wrong. Please try again."}` },
          ]);
        } else if (parsed.type === "model_selected") {
          if (parsed.model) onSetResolvedModel(parsed.model);
        } else if (parsed.type === "skill_auto_selected") {
          if (parsed.slug) onSetSkill(parsed.slug, true);
        } else if (parsed.type === "skill_auto_cleared") {
          onSetSkill(null, false);
        } else if (parsed.type === "skill_disambiguation" && parsed.candidates?.length) {
          onSkillDisambiguation({
            candidates: parsed.candidates,
            pending: { newMessages, sentAttachments },
          });
          onUpdateMessages(newMessages);
          streamComplete = true;
          break;
        } else if (parsed.type === "cancelled") {
          streamComplete = true;
          break;
        } else if (parsed.type === "answer_now") {
          onSetStreamingStatus(parsed.content ?? "Answering now…");
        } else if (parsed.type === "done") {
          streamComplete = true;
          break;
        }
      }
      if (streamComplete) break;
    }
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      return;
    }
    const msg = err instanceof Error ? err.message : "Unknown error";
    trackEvent("workspace.error_shown", { error: msg });
    onUpdateMessages([
      ...newMessages,
      {
        role: "model",
        content:
          "I couldn’t complete that request. Your message is still here. You can revise it below and send it again. If this keeps happening, contact your workspace administrator.",
      },
    ]);
  } finally {
    chatAbortControllers.delete(tab.id);
    onSetStreaming(false);
    onStreamEnd?.();
  }
}

export async function stopChatStream(tabId: string): Promise<void> {
  chatAbortControllers.get(tabId)?.abort();
  if (isPersistedChatId(tabId)) {
    await apiFetch("/api/chat/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ chat_id: tabId, mode: "stop" }),
    }).catch(() => {});
  }
}

export async function answerChatNow(tabId: string, onSetStreamingStatus: (status: string) => void): Promise<void> {
  if (!isPersistedChatId(tabId)) return;
  onSetStreamingStatus("Answering now…");
  await apiFetch("/api/chat/cancel", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: tabId, mode: "answer_now" }),
  }).catch(() => {});
}
