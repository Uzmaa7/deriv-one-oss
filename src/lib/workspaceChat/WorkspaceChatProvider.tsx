import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { apiFetch } from "@/lib/api";
import { trackEvent } from "@/lib/analytics";
import { hydrateMessageContent } from "@/src/lib/chatMarkdown";
import type { MentionKind } from "@/src/components/composer/mention/types";
import {
  answerChatNow,
  isLocalChatStreamActive,
  runChatStream,
  stopChatStream,
} from "./streamChat";
import {
  emptyTab,
  isPersistedChatId,
  type Message,
  type PendingAttachment,
  type Tab,
} from "./types";

export interface ActiveStream {
  chatId: string;
  status: string;
}

interface WorkspaceChatContextValue {
  tabs: Tab[];
  activeTabId: string;
  booting: boolean;
  availableModels: string[];
  defaultModel: string;
  activeStreams: ActiveStream[];
  setActiveTabId: (id: string) => void;
  addTab: () => Promise<string>;
  deleteTab: (id: string) => Promise<boolean>;
  loadTabMessages: (tabId: string) => Promise<void>;
  adoptChatTab: (row: { id: string; title: string; activeSkillId: string | null }) => void;
  updateMessages: (tabId: string, msgs: Message[]) => void;
  setStreaming: (tabId: string, v: boolean) => void;
  setStreamingStatus: (tabId: string, status: string) => void;
  setSkill: (tabId: string, skillId: string | null, auto?: boolean) => void;
  setModel: (tabId: string, model: string) => void;
  setResolvedModel: (tabId: string, model: string) => void;
  createChatInDb: () => Promise<Tab>;
  sendMessage: (
    tabId: string,
    params: {
      textOrRetry?: string | {
        skipUserBubble?: boolean;
        pending: { newMessages: Message[]; sentAttachments: PendingAttachment[] };
        skillId?: string | null;
        noSkill?: boolean;
      };
      options?: { skillId?: string | null };
      createKind?: MentionKind | null;
      thinkingEnabled: boolean;
      pendingAttachments: PendingAttachment[];
      onClearPendingAttachments: () => void;
      onSkillDisambiguation: (value: {
        candidates: { slug: string; confidence: number; reason: string }[];
        pending: { newMessages: Message[]; sentAttachments: PendingAttachment[] };
      } | null) => void;
      isFocusedTab: () => boolean;
      onClearPendingCreate?: () => void;
    },
  ) => Promise<void>;
  stopStream: (tabId: string) => Promise<void>;
  answerNow: (tabId: string) => Promise<void>;
  isTabStreaming: (tabId: string) => boolean;
}

const WorkspaceChatContext = createContext<WorkspaceChatContextValue | null>(null);

function mapApiMessages(messages: { role: string; content: string }[]): Message[] {
  return messages.map(m => {
    const role = (m.role === "assistant" ? "model" : "user") as Message["role"];
    const hydrated = hydrateMessageContent(m.content);
    return {
      role,
      content: hydrated.content,
      steps: hydrated.steps.length > 0 ? hydrated.steps : undefined,
      files: hydrated.files.length > 0 ? hydrated.files : undefined,
      proposal: hydrated.proposal ?? undefined,
    };
  });
}

export function WorkspaceChatProvider({ children }: { children: ReactNode }) {
  const [tabs, setTabs] = useState<Tab[]>([]);
  const [activeTabId, setActiveTabId] = useState("");
  const [booting, setBooting] = useState(true);
  const [availableModels, setAvailableModels] = useState<string[]>([]);
  const [defaultModel, setDefaultModel] = useState("");
  const [localStreamIds, setLocalStreamIds] = useState<ReadonlySet<string>>(() => new Set());
  const tabsRef = useRef(tabs);
  const activeTabIdRef = useRef(activeTabId);
  const pollTimersRef = useRef<Map<string, ReturnType<typeof setInterval>>>(new Map());
  const localStreamIdsRef = useRef(localStreamIds);

  tabsRef.current = tabs;
  activeTabIdRef.current = activeTabId;
  localStreamIdsRef.current = localStreamIds;

  const isLocalStreamActive = useCallback((tabId: string) => (
    localStreamIdsRef.current.has(tabId) || isLocalChatStreamActive(tabId)
  ), []);

  const markLocalStreamStart = useCallback((tabId: string) => {
    setLocalStreamIds(prev => {
      if (prev.has(tabId)) return prev;
      const next = new Set(prev);
      next.add(tabId);
      return next;
    });
  }, []);

  const markLocalStreamEnd = useCallback((tabId: string) => {
    setLocalStreamIds(prev => {
      if (!prev.has(tabId)) return prev;
      const next = new Set(prev);
      next.delete(tabId);
      return next;
    });
  }, []);

  const isTabStreaming = useCallback((tabId: string) => {
    const tab = tabsRef.current.find(t => t.id === tabId);
    return Boolean(tab?.isStreaming) || isLocalStreamActive(tabId);
  }, [isLocalStreamActive, localStreamIds]);

  useEffect(() => {
    if (!activeTabId) return;
    try { sessionStorage.setItem("deriv-workspace-active-chat", activeTabId); } catch { /* optional */ }
  }, [activeTabId]);

  const getTab = useCallback((tabId: string) => tabsRef.current.find(t => t.id === tabId), []);

  const stopPolling = useCallback((tabId: string) => {
    const timer = pollTimersRef.current.get(tabId);
    if (timer) {
      clearInterval(timer);
      pollTimersRef.current.delete(tabId);
    }
  }, []);

  const startPollingActiveChat = useCallback((tabId: string) => {
    if (pollTimersRef.current.has(tabId)) return;
    const timer = setInterval(async () => {
      if (!isPersistedChatId(tabId)) {
        stopPolling(tabId);
        return;
      }
      const localActive = isLocalStreamActive(tabId);
      try {
        const [activeRes, chatRes] = await Promise.all([
          apiFetch(`/api/chats/${tabId}/active`),
          apiFetch(`/api/chats/${tabId}`),
        ]);
        const active = activeRes.ok ? Boolean((await activeRes.json()).active) : false;
        const streaming = active || localActive;
        if (!chatRes.ok) {
          if (streaming) {
            setTabs(prev => prev.map(t => t.id !== tabId ? t : {
              ...t,
              isStreaming: true,
              streamingStatus: t.streamingStatus || "Still working…",
            }));
          }
          return;
        }
        const chat = await chatRes.json();
        setTabs(prev => prev.map(t => t.id !== tabId ? t : {
          ...t,
          title: chat.title,
          activeSkillId: chat.active_skill_id ?? null,
          ...(localActive ? {} : {
            messages: mapApiMessages(chat.messages ?? []),
          }),
          messagesLoaded: true,
          messagesLoadError: false,
          isStreaming: streaming,
          streamingStatus: streaming ? (t.streamingStatus || "Still working…") : "",
        }));
        if (!streaming) stopPolling(tabId);
      } catch {
        if (localActive) {
          setTabs(prev => prev.map(t => t.id !== tabId ? t : {
            ...t,
            isStreaming: true,
            streamingStatus: t.streamingStatus || "Still working…",
          }));
        }
      }
    }, 2000);
    pollTimersRef.current.set(tabId, timer);
  }, [isLocalStreamActive, stopPolling]);

  const checkActiveSession = useCallback(async (tabId: string) => {
    if (!isPersistedChatId(tabId)) return;
    try {
      const res = await apiFetch(`/api/chats/${tabId}/active`);
      if (!res.ok) return;
      const { active } = await res.json();
      if (active) {
        setTabs(prev => prev.map(t => t.id === tabId ? {
          ...t,
          isStreaming: true,
          streamingStatus: t.streamingStatus || "Still working…",
        } : t));
        startPollingActiveChat(tabId);
      }
    } catch {
      // ignore
    }
  }, [startPollingActiveChat]);

  useEffect(() => () => {
    pollTimersRef.current.forEach(timer => clearInterval(timer));
    pollTimersRef.current.clear();
  }, []);

  const createChatInDb = useCallback(async (): Promise<Tab> => {
    const res = await apiFetch("/api/chats", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: "New Chat" }),
    });
    if (!res.ok) throw new Error("Could not create chat");
    const chat = await res.json();
    return emptyTab({
      id: chat.id,
      title: chat.title,
      activeSkillId: chat.active_skill_id ?? null,
    });
  }, []);

  const promoteLocalTab = useCallback(async (localTabId: string): Promise<string | null> => {
    if (isPersistedChatId(localTabId)) return localTabId;
    try {
      const persisted = await createChatInDb();
      setTabs(prev => prev.map(t => (
        t.id === localTabId
          ? {
            ...persisted,
            messages: t.messages,
            messagesLoaded: true,
            isStreaming: t.isStreaming,
            streamingStatus: t.streamingStatus,
            selectedModel: t.selectedModel,
            resolvedModel: t.resolvedModel,
            activeSkillId: t.activeSkillId,
            activeSkillAuto: t.activeSkillAuto,
          }
          : t
      )));
      if (activeTabIdRef.current === localTabId) {
        setActiveTabId(persisted.id);
      }
      return persisted.id;
    } catch {
      return null;
    }
  }, [createChatInDb]);

  const loadTabMessages = useCallback(async (tabId: string) => {
    const localActive = isLocalStreamActive(tabId);
    setTabs(prev => prev.map(t => t.id === tabId ? { ...t, messagesLoaded: false, messagesLoadError: false } : t));
    try {
      const res = await apiFetch(`/api/chats/${tabId}`);
      if (!res.ok) throw new Error("Could not load chat");
      const chat = await res.json();
      const streaming = Boolean(chat.streaming) || localActive;
      setTabs(prev => prev.map(t => t.id !== tabId ? t : {
        ...t,
        title: chat.title,
        activeSkillId: chat.active_skill_id ?? null,
        ...(localActive ? {} : {
          messages: mapApiMessages(chat.messages ?? []),
        }),
        messagesLoaded: true,
        messagesLoadError: false,
        isStreaming: streaming,
        streamingStatus: streaming ? (t.streamingStatus || "Still working…") : "",
      }));
      if (streaming) {
        startPollingActiveChat(tabId);
      } else {
        stopPolling(tabId);
      }
    } catch {
      setTabs(prev => prev.map(t => t.id === tabId ? { ...t, messagesLoaded: true, messagesLoadError: true } : t));
    }
  }, [isLocalStreamActive, startPollingActiveChat, stopPolling]);

  const adoptChatTab = useCallback((row: { id: string; title: string; activeSkillId: string | null }) => {
    setTabs(prev => (
      prev.some(t => t.id === row.id)
        ? prev
        : [...prev, emptyTab({
          id: row.id,
          title: row.title,
          activeSkillId: row.activeSkillId,
          messagesLoaded: false,
        })]
    ));
    setActiveTabId(row.id);
    void loadTabMessages(row.id);
  }, [loadTabMessages]);

  useEffect(() => {
    trackEvent("workspace.page_viewed");
  }, []);

  useEffect(() => {
    async function boot() {
      const [modelsRes, chatsRes] = await Promise.allSettled([
        apiFetch("/api/models"),
        apiFetch("/api/chats"),
      ]);
      if (modelsRes.status === "fulfilled" && modelsRes.value.ok) {
        try {
          const data = await modelsRes.value.json();
          if (Array.isArray(data.models) && data.models.length > 0) {
            const ids = (data.models as Array<string | { id: string }>).map(m =>
              typeof m === "string" ? m : m.id,
            );
            setAvailableModels(ids);
          }
          if (typeof data.default === "string") setDefaultModel(data.default);
        } catch { /* defaults */ }
      }
      try {
        if (chatsRes.status !== "fulfilled" || !chatsRes.value.ok) throw new Error("chats fetch failed");
        const chats = await chatsRes.value.json();
        if (chats.length > 0) {
          const loaded: Tab[] = chats.map((c: { id: string; title: string; active_skill_id: string | null }) =>
            emptyTab({
              id: c.id,
              title: c.title,
              activeSkillId: c.active_skill_id ?? null,
              messagesLoaded: false,
            }),
          );
          setTabs(loaded);
          let selectedId = loaded[0].id;
          try {
            const saved = sessionStorage.getItem("deriv-workspace-active-chat");
            if (saved && loaded.some(t => t.id === saved)) selectedId = saved;
          } catch { /* use most recent */ }
          setActiveTabId(selectedId);
          await loadTabMessages(selectedId);
          void checkActiveSession(selectedId);
        } else {
          const newTab = await createChatInDb();
          setTabs([newTab]);
          setActiveTabId(newTab.id);
        }
      } catch {
        const fallback = emptyTab();
        setTabs([fallback]);
        setActiveTabId(fallback.id);
      } finally {
        setBooting(false);
      }
    }
    void boot();
  }, [checkActiveSession, createChatInDb, loadTabMessages]);

  const addTab = useCallback(async (): Promise<string> => {
    try {
      const newTab = await createChatInDb();
      setTabs(prev => [...prev, newTab]);
      setActiveTabId(newTab.id);
      return newTab.id;
    } catch {
      const fallback = emptyTab();
      setTabs(prev => [...prev, fallback]);
      setActiveTabId(fallback.id);
      return fallback.id;
    }
  }, [createChatInDb]);

  const deleteTab = useCallback(async (id: string): Promise<boolean> => {
    stopPolling(id);
    if (isPersistedChatId(id)) {
      try {
        const res = await apiFetch(`/api/chats/${id}`, { method: "DELETE" });
        if (!res.ok) throw new Error("Delete failed");
      } catch {
        return false;
      }
    }
    const remaining = tabsRef.current.filter(t => t.id !== id);
    if (remaining.length === 0) {
      setTabs([]);
      await addTab();
    } else {
      setTabs(remaining);
      if (activeTabIdRef.current === id) {
        const idx = tabsRef.current.findIndex(t => t.id === id);
        setActiveTabId(remaining[Math.max(0, idx - 1)].id);
      }
    }
    return true;
  }, [addTab, stopPolling]);

  const updateMessages = useCallback((tabId: string, msgs: Message[]) => {
    setTabs(prev => prev.map(t => {
      if (t.id !== tabId) return t;
      const newTitle = msgs.length > 0 && msgs[0].role === "user"
        ? msgs[0].content.slice(0, 28) + (msgs[0].content.length > 28 ? "…" : "")
        : t.title;
      if (newTitle !== t.title && msgs.length === 2 && isPersistedChatId(tabId)) {
        apiFetch(`/api/chats/${tabId}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ title: newTitle }),
        }).catch(() => {});
      }
      return { ...t, messages: msgs, title: newTitle };
    }));
  }, []);

  const setStreaming = useCallback((tabId: string, v: boolean) => {
    setTabs(prev => prev.map(t => (
      t.id === tabId ? { ...t, isStreaming: v, streamingStatus: v ? t.streamingStatus : "" } : t
    )));
    if (!v) stopPolling(tabId);
  }, [stopPolling]);

  const setStreamingStatus = useCallback((tabId: string, status: string) => {
    setTabs(prev => prev.map(t => (t.id === tabId ? { ...t, streamingStatus: status } : t)));
  }, []);

  const setSkill = useCallback((tabId: string, skillId: string | null, auto = false) => {
    setTabs(prev => prev.map(t => (
      t.id === tabId ? { ...t, activeSkillId: skillId, activeSkillAuto: Boolean(auto && skillId) } : t
    )));
    if (!auto) trackEvent("workspace.skill_selected", { skill_id: skillId ?? "none" });
    if (!isPersistedChatId(tabId) || auto) return;
    apiFetch(`/api/chats/${tabId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ active_skill_id: skillId }),
    }).catch(() => {});
  }, []);

  const setModel = useCallback((tabId: string, model: string) => {
    setTabs(prev => prev.map(t => (t.id === tabId ? { ...t, selectedModel: model, resolvedModel: undefined } : t)));
  }, []);

  const setResolvedModel = useCallback((tabId: string, model: string) => {
    setTabs(prev => prev.map(t => (t.id === tabId ? { ...t, resolvedModel: model } : t)));
  }, []);

  const sendMessage = useCallback(async (
    tabId: string,
    params: {
      textOrRetry?: string | {
        skipUserBubble?: boolean;
        pending: { newMessages: Message[]; sentAttachments: PendingAttachment[] };
        skillId?: string | null;
        noSkill?: boolean;
      };
      options?: { skillId?: string | null };
      createKind?: MentionKind | null;
      thinkingEnabled: boolean;
      pendingAttachments: PendingAttachment[];
      onClearPendingAttachments: () => void;
      onSkillDisambiguation: (value: {
        candidates: { slug: string; confidence: number; reason: string }[];
        pending: { newMessages: Message[]; sentAttachments: PendingAttachment[] };
      } | null) => void;
      isFocusedTab: () => boolean;
      onClearPendingCreate?: () => void;
    },
  ) => {
    const tab = getTab(tabId);
    if (!tab || tab.isStreaming || isLocalStreamActive(tabId)) return;

    let newMessages: Message[];
    let userContent: string;
    let sentAttachments: PendingAttachment[];
    let noSkill = false;
    let manualSkillId: string | null;

    if (typeof params.textOrRetry === "object" && params.textOrRetry?.pending) {
      const retry = params.textOrRetry;
      newMessages = retry.pending.newMessages;
      sentAttachments = retry.pending.sentAttachments;
      const lastUser = [...newMessages].reverse().find(m => m.role === "user");
      if (!lastUser?.content.trim()) return;
      userContent = lastUser.content;
      updateMessages(tabId, [...newMessages, { role: "model", content: "" }]);
      noSkill = Boolean(retry.noSkill);
      manualSkillId = noSkill ? null : retry.skillId !== undefined ? retry.skillId : null;
    } else {
      const text = typeof params.textOrRetry === "string" ? params.textOrRetry : "";
      if (!text.trim()) return;
      const effectiveSkillId = params.options && "skillId" in params.options
        ? params.options.skillId ?? null
        : tab.activeSkillId;
      const userMsg: Message = { role: "user", content: text.trim() };
      newMessages = [...tab.messages, userMsg];
      userContent = userMsg.content;
      updateMessages(tabId, [...newMessages, { role: "model", content: "" }]);
      trackEvent("workspace.message_sent", {
        skill_id: effectiveSkillId ?? null,
        has_attachments: params.pendingAttachments.length > 0,
      });
      sentAttachments = params.pendingAttachments.slice();
      params.onClearPendingAttachments();
      sentAttachments.forEach(a => { if (a.previewUrl) URL.revokeObjectURL(a.previewUrl); });
      manualSkillId = effectiveSkillId && !tab.activeSkillAuto ? effectiveSkillId : null;
    }

    params.onSkillDisambiguation(null);

    let effectiveTabId = tabId;
    if (!isPersistedChatId(tabId)) {
      const promoted = await promoteLocalTab(tabId);
      if (!promoted) {
        updateMessages(tabId, [
          ...newMessages,
          {
            role: "model",
            content: "Could not reach the server. Check that the backend is running, then try again.",
          },
        ]);
        setStreaming(tabId, false);
        setStreamingStatus(tabId, "");
        markLocalStreamEnd(tabId);
        stopPolling(tabId);
        return;
      }
      effectiveTabId = promoted;
    }

    setStreaming(effectiveTabId, true);
    setStreamingStatus(effectiveTabId, "");
    markLocalStreamStart(effectiveTabId);
    startPollingActiveChat(effectiveTabId);

    await runChatStream({
      tab: getTab(effectiveTabId) ?? tab,
      newMessages,
      userContent,
      sentAttachments,
      manualSkillId,
      noSkill,
      thinkingEnabled: params.thinkingEnabled,
      createKind: params.createKind,
      handlers: {
        onUpdateMessages: msgs => updateMessages(effectiveTabId, msgs),
        onSetStreaming: v => setStreaming(effectiveTabId, v),
        onSetStreamingStatus: status => setStreamingStatus(effectiveTabId, status),
        onSetSkill: (skillId, auto) => setSkill(effectiveTabId, skillId, auto),
        onSetResolvedModel: model => setResolvedModel(effectiveTabId, model),
        onSkillDisambiguation: params.onSkillDisambiguation,
        isFocusedTab: params.isFocusedTab,
        onClearPendingCreate: params.onClearPendingCreate,
        onStreamStart: () => {
          markLocalStreamStart(effectiveTabId);
          startPollingActiveChat(effectiveTabId);
        },
        onStreamEnd: () => {
          markLocalStreamEnd(effectiveTabId);
          void (async () => {
            try {
              const res = await apiFetch(`/api/chats/${effectiveTabId}/active`);
              if (!res.ok) return;
              const { active } = await res.json();
              if (!active) return;
              setStreaming(effectiveTabId, true);
              setStreamingStatus(effectiveTabId, "Still working…");
              startPollingActiveChat(effectiveTabId);
            } catch {
              // ignore
            }
          })();
        },
      },
    });
  }, [getTab, isLocalStreamActive, markLocalStreamEnd, markLocalStreamStart, promoteLocalTab, setResolvedModel, setSkill, setStreaming, setStreamingStatus, startPollingActiveChat, stopPolling, updateMessages]);

  const stopStream = useCallback(async (tabId: string) => {
    await stopChatStream(tabId);
    markLocalStreamEnd(tabId);
    setStreaming(tabId, false);
    setStreamingStatus(tabId, "");
  }, [markLocalStreamEnd, setStreaming, setStreamingStatus]);

  const answerNow = useCallback(async (tabId: string) => {
    await answerChatNow(tabId, status => setStreamingStatus(tabId, status));
  }, [setStreamingStatus]);

  const activeStreams = useMemo<ActiveStream[]>(() =>
    tabs.filter(t => isTabStreaming(t.id)).map(t => ({ chatId: t.id, status: t.streamingStatus || "Working…" })),
  [tabs, isTabStreaming]);

  const value = useMemo<WorkspaceChatContextValue>(() => ({
    tabs,
    activeTabId,
    booting,
    availableModels,
    defaultModel,
    activeStreams,
    setActiveTabId,
    addTab,
    deleteTab,
    loadTabMessages,
    adoptChatTab,
    updateMessages,
    setStreaming,
    setStreamingStatus,
    setSkill,
    setModel,
    setResolvedModel,
    createChatInDb,
    sendMessage,
    stopStream,
    answerNow,
    isTabStreaming,
  }), [
    tabs,
    activeTabId,
    booting,
    availableModels,
    defaultModel,
    activeStreams,
    addTab,
    deleteTab,
    loadTabMessages,
    adoptChatTab,
    updateMessages,
    setStreaming,
    setStreamingStatus,
    setSkill,
    setModel,
    setResolvedModel,
    createChatInDb,
    sendMessage,
    stopStream,
    answerNow,
    isTabStreaming,
  ]);

  return (
    <WorkspaceChatContext.Provider value={value}>
      {children}
    </WorkspaceChatContext.Provider>
  );
}

export function useWorkspaceChat() {
  const ctx = useContext(WorkspaceChatContext);
  if (!ctx) throw new Error("useWorkspaceChat must be used within WorkspaceChatProvider");
  return ctx;
}

export function useWorkspaceStreams() {
  const { activeStreams } = useWorkspaceChat();
  return activeStreams;
}
