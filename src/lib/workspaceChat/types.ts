import type { GeneratedFile, ProposalMarker, ToolStep } from "@/src/lib/chatMarkdown";

export type { GeneratedFile, ProposalMarker, ToolStep };

export interface Message {
  role: "user" | "model";
  content: string;
  thinking?: string;
  steps?: ToolStep[];
  files?: GeneratedFile[];
  proposal?: ProposalMarker;
}

export interface PendingAttachment {
  id: string;
  mime: string;
  name: string;
  previewUrl?: string;
}

export interface Tab {
  id: string;
  title: string;
  messages: Message[];
  isStreaming: boolean;
  streamingStatus: string;
  activeSkillId: string | null;
  activeSkillAuto?: boolean;
  messagesLoaded: boolean;
  messagesLoadError?: boolean;
  selectedModel: string;
  resolvedModel?: string;
}

export function isPersistedChatId(id: string | undefined | null): boolean {
  return typeof id === "string" && id !== "undefined" && id !== "" && !id.startsWith("local-");
}

export function emptyTab(overrides: Partial<Tab> = {}): Tab {
  return {
    id: `local-${Date.now()}`,
    title: "New Chat",
    messages: [],
    isStreaming: false,
    streamingStatus: "",
    activeSkillId: null,
    messagesLoaded: true,
    selectedModel: "",
    ...overrides,
  };
}
