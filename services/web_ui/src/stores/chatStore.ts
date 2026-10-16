import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ChatMessage, Conversation, MessageStatus } from "../types/chat";
import type { ApiStoredMessage, Source } from "../types/api";
import { streamChat } from "../api/sse";
import {
  deleteConversationApi,
  fetchConversation,
  fetchConversations,
  renameConversationApi,
  uploadDocumentFile,
} from "../api/endpoints";
import { ApiError } from "../api/client";
import { titleFromQuery } from "../lib/titleFromQuery";
import { useToastStore } from "./toastStore";
import { useSkillStore } from "./skillStore";

interface ChatState {
  conversations: Record<string, Conversation>;
  order: string[]; // List of conversation IDs in reverse-chronological order
  activeId: string | null;
  isStreaming: boolean;
  isLoading: boolean;

  newConversation: () => string;
  resetActive: () => void;
  loadConversations: () => Promise<void>;
  selectConversation: (id: string) => Promise<void>;
  renameConversation: (id: string, newTitle: string) => Promise<void>;
  deleteConversation: (id: string) => Promise<void>;
  sendMessage: (query: string, attachedFiles?: File[]) => Promise<void>;
  stopStreaming: () => void;
  regenerate: (messageId?: string) => Promise<void>;
  setFeedback: (conversationId: string, messageId: string, feedback: "up" | "down") => void;
  clearAll: () => Promise<void>;
}

// Module-level abort controller for active streaming session
let activeAbortController: AbortController | null = null;

function generateUuid(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

function mapStoredMessageToChatMessage(msg: ApiStoredMessage): ChatMessage {
  let sources: Source[] = [];
  if (msg.sources_json) {
    try {
      const parsed = JSON.parse(msg.sources_json);
      if (Array.isArray(parsed)) {
        sources = parsed;
      }
    } catch {
      sources = [];
    }
  }

  let status: MessageStatus = "done";
  if (msg.status === "generating" || msg.status === "streaming") {
    status = msg.content && msg.content.trim().length > 0 ? "done" : "stopped";
  } else if (msg.status === "failed") {
    status = "error";
  } else if (msg.status === "cancelled") {
    status = "stopped";
  }

  return {
    id: msg.id,
    role: msg.role === "assistant" ? "assistant" : "user",
    content: msg.content || "",
    sources,
    status,
    createdAt: msg.created_at ? new Date(msg.created_at).getTime() : Date.now(),
  };
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      conversations: {},
      order: [],
      activeId: null,
      isStreaming: false,
      isLoading: false,

      newConversation: () => {
        const state = get();
        // 1. If currently active conversation is empty (0 messages), reuse it
        if (state.activeId) {
          const current = state.conversations[state.activeId];
          if (current && (!current.messages || current.messages.length === 0)) {
            return state.activeId;
          }
        }

        // 2. Prune any orphaned empty conversations from state
        const nextConvs = { ...state.conversations };
        let hasPruned = false;
        for (const [convId, conv] of Object.entries(nextConvs)) {
          if (!conv.messages || conv.messages.length === 0) {
            delete nextConvs[convId];
            hasPruned = true;
          }
        }

        const id = generateUuid();
        const newConv: Conversation = {
          id,
          title: "New chat",
          messages: [],
          updatedAt: Date.now(),
        };
        nextConvs[id] = newConv;

        // Note: New empty chat is not shown in history order until the first message is sent
        const nextOrder = hasPruned
          ? state.order.filter((x) => !!nextConvs[x] && x !== id)
          : state.order.filter((x) => x !== id);

        set({
          conversations: nextConvs,
          order: nextOrder,
          activeId: id,
        });

        return id;
      },

      resetActive: () => {
        const state = get();
        const nextConvs = { ...state.conversations };
        let nextOrder = state.order;
        if (state.activeId) {
          const current = nextConvs[state.activeId];
          if (current && (!current.messages || current.messages.length === 0)) {
            delete nextConvs[state.activeId];
            nextOrder = nextOrder.filter((x) => x !== state.activeId);
          }
        }
        set({
          conversations: nextConvs,
          order: nextOrder,
          activeId: null,
        });
      },

      loadConversations: async () => {
        try {
          const summaries = await fetchConversations();
          set((state) => {
            const nextConvs = { ...state.conversations };
            const backendOrder: string[] = [];

            for (const item of summaries) {
              backendOrder.push(item.id);
              const existing = nextConvs[item.id];
              const updatedAt = item.updated_at ? new Date(item.updated_at).getTime() : Date.now();
              if (existing) {
                nextConvs[item.id] = {
                  ...existing,
                  title: item.title || existing.title,
                  updatedAt,
                };
              } else {
                nextConvs[item.id] = {
                  id: item.id,
                  title: item.title || "New chat",
                  messages: [],
                  updatedAt,
                };
              }
            }

            // Prune local-only empty conversations (cleans up legacy localStorage "New chat" rác)
            for (const [id, conv] of Object.entries(nextConvs)) {
              if (
                !backendOrder.includes(id) &&
                (!conv.messages || conv.messages.length === 0) &&
                id !== state.activeId
              ) {
                delete nextConvs[id];
              }
            }

            // Only keep local-only conversations in order if they actually have messages
            const localOnly = state.order.filter(
              (id) => !backendOrder.includes(id) && !!nextConvs[id] && nextConvs[id].messages && nextConvs[id].messages.length > 0
            );
            const newOrder = [...localOnly, ...backendOrder];

            return {
              conversations: nextConvs,
              order: newOrder,
            };
          });
        } catch (err) {
          console.warn("Failed to load conversations from backend:", err);
        }
      },

      selectConversation: async (id: string) => {
        const state = get();

        // If switching away from an empty conversation, prune that empty conversation
        const nextConvs = { ...state.conversations };
        let nextOrder = state.order;
        if (state.activeId && state.activeId !== id) {
          const currentActive = nextConvs[state.activeId];
          if (currentActive && (!currentActive.messages || currentActive.messages.length === 0)) {
            delete nextConvs[state.activeId];
            nextOrder = nextOrder.filter((x) => x !== state.activeId);
          }
        }

        set({
          conversations: nextConvs,
          order: nextOrder,
          activeId: id,
        });

        // Avoid overwriting conversation state if currently streaming this conversation
        if (get().isStreaming && get().activeId === id) {
          return;
        }

        const currentConv = get().conversations[id];

        try {
          const detail = await fetchConversation(id);
          const rawMessages = (detail.messages || []).map(mapStoredMessageToChatMessage);

          set((s) => {
            // Avoid clobbering active streaming conversation if streaming started while fetching
            if (s.isStreaming && s.activeId === id) {
              return s;
            }

            const prev = s.conversations[id];
            const prevMsgs = prev?.messages || [];
            const messages = rawMessages.map((m, idx) => {
              if (m.role === "assistant" && !m.content.trim()) {
                const localCandidate = prevMsgs[idx];
                if (
                  localCandidate &&
                  localCandidate.role === "assistant" &&
                  localCandidate.content.trim().length > 0
                ) {
                  return {
                    ...m,
                    content: localCandidate.content,
                    sources:
                      localCandidate.sources && localCandidate.sources.length > 0
                        ? localCandidate.sources
                        : m.sources,
                    status:
                      localCandidate.status === "streaming"
                        ? "done"
                        : localCandidate.status,
                  };
                }
              }
              return m;
            });

            const updatedConv: Conversation = {
              id: detail.id,
              title: detail.title || prev?.title || "New chat",
              messages,
              updatedAt: detail.updated_at
                ? new Date(detail.updated_at).getTime()
                : (prev?.updatedAt ?? Date.now()),
              selectedDocumentIds: prev?.selectedDocumentIds,
            };

            const updatedOrder = s.order.includes(id) ? s.order : [id, ...s.order];

            return {
              conversations: {
                ...s.conversations,
                [id]: updatedConv,
              },
              order: updatedOrder,
              activeId: s.activeId === id ? id : s.activeId,
            };
          });
        } catch (err: unknown) {
          // If conversation is neither in backend nor in local cache, rethrow
          if (!currentConv) {
            throw err;
          }
        }
      },

      renameConversation: async (id: string, newTitle: string) => {
        const trimmed = newTitle.trim();
        if (!trimmed) return;

        set((state) => {
          const conv = state.conversations[id];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [id]: { ...conv, title: trimmed, updatedAt: Date.now() },
            },
          };
        });

        try {
          await renameConversationApi(id, trimmed);
        } catch (err: unknown) {
          const is404 = err instanceof ApiError && err.status === 404;
          if (!is404) {
            console.error("Failed to rename conversation on server:", err);
            useToastStore.getState().pushToast("Failed to rename conversation on server", "error");
          }
        }
      },

      deleteConversation: async (id: string) => {
        set((state) => {
          const nextConvs = { ...state.conversations };
          delete nextConvs[id];
          const nextOrder = state.order.filter((x) => x !== id);
          const nextActiveId =
            state.activeId === id ? (nextOrder.length > 0 ? nextOrder[0] : null) : state.activeId;

          return {
            conversations: nextConvs,
            order: nextOrder,
            activeId: nextActiveId,
          };
        });

        try {
          await deleteConversationApi(id);
        } catch (err: unknown) {
          const is404 = err instanceof ApiError && err.status === 404;
          if (!is404) {
            console.error("Failed to delete conversation on server:", err);
            useToastStore.getState().pushToast("Failed to delete conversation on server", "error");
          }
        }
      },

      sendMessage: async (query: string, attachedFiles?: File[]) => {
        const trimmedQuery = query.trim();
        const files = attachedFiles ?? [];
        const hasFiles = files.length > 0;

        if (!trimmedQuery && !hasFiles) return;
        if (get().isStreaming) return;

        // Xác định cuộc trò chuyện đang dùng
        let currentId = get().activeId;
        if (!currentId || !get().conversations[currentId]) {
          currentId = get().newConversation();
        }
        const conversationId: string = currentId;

        // Tạo controller ngay từ đầu để nút Stop dùng được cả lúc đang upload
        const controller = new AbortController();
        activeAbortController = controller;
        set({ isStreaming: true });

        const userMsgId = generateUuid();
        const assistantMsgId = generateUuid();
        let isFirstMessage = false;
        let nextTitle = "";
        let accumulatedContent = "";

        // Cập nhật tin nhắn trợ lý (bỏ qua nếu cuộc trò chuyện đã bị xóa)
        const updateAssistant = (patch: Partial<ChatMessage>) => {
          set((state) => {
            const conv = state.conversations[conversationId];
            if (!conv) return {};
            return {
              conversations: {
                ...state.conversations,
                [conversationId]: {
                  ...conv,
                  messages: conv.messages.map((m) =>
                    m.id === assistantMsgId ? { ...m, ...patch } : m
                  ),
                  updatedAt: Date.now(),
                },
              },
            };
          });
        };

        try {
          // 1. Ghi tin nhắn người dùng TRƯỚC khi upload,
          //    để cuộc trò chuyện không còn trống và không bị dọn mất
          if (trimmedQuery) {
            set((state) => {
              const conv = state.conversations[conversationId];
              if (!conv) return {};

              isFirstMessage = !conv.messages.some((m) => m.role === "user");
              nextTitle = isFirstMessage ? titleFromQuery(trimmedQuery) : conv.title;

              const userMsg: ChatMessage = {
                id: userMsgId,
                role: "user",
                content: trimmedQuery,
                sources: [],
                status: "done",
                createdAt: Date.now(),
              };

              const assistantMsg: ChatMessage = {
                id: assistantMsgId,
                role: "assistant",
                content: "",
                sources: [],
                status: "streaming",
                createdAt: Date.now(),
              };

              return {
                activeId: conversationId,
                order: [conversationId, ...state.order.filter((x) => x !== conversationId)],
                conversations: {
                  ...state.conversations,
                  [conversationId]: {
                    ...conv,
                    title: nextTitle,
                    messages: [...conv.messages, userMsg, assistantMsg],
                    updatedAt: Date.now(),
                  },
                },
              };
            });
          }

          // 2. Upload file đính kèm
          const uploadedDocumentIds: string[] = [];

          if (hasFiles) {
            try {
              for (const file of files) {
                if (controller.signal.aborted) break;
                const res = await uploadDocumentFile(file);
                if (res?.document_id) {
                  uploadedDocumentIds.push(res.document_id);
                }
              }

              if (!controller.signal.aborted) {
                useToastStore
                  .getState()
                  .pushToast(`Đã tải lên thành công ${uploadedDocumentIds.length} tài liệu`, "success");
              }
            } catch (err: unknown) {
              const msg = err instanceof Error ? err.message : "Failed to upload attachments";
              useToastStore.getState().pushToast(msg, "error");
              if (trimmedQuery) {
                updateAssistant({ content: msg, status: "error" });
              }
              return;
            }
          }

          // Chỉ upload, không có câu hỏi
          if (!trimmedQuery) return;

          // Người dùng bấm Stop trong lúc upload
          if (controller.signal.aborted) {
            updateAssistant({ status: "stopped" });
            return;
          }

          // 3. Cuộc trò chuyện phải còn tồn tại sau khi upload
          const conv = get().conversations[conversationId];
          if (!conv) {
            useToastStore
              .getState()
              .pushToast("Cuộc trò chuyện đã bị đóng trong lúc tải tệp. Vui lòng gửi lại.", "error");
            return;
          }

          // 4. Xác định tài liệu được chọn
          const selectedDocIds =
            uploadedDocumentIds.length > 0
              ? uploadedDocumentIds
              : [...(conv.selectedDocumentIds ?? [])];

          if (uploadedDocumentIds.length > 0) {
            set((state) => {
              const c = state.conversations[conversationId];
              if (!c) return {};
              return {
                conversations: {
                  ...state.conversations,
                  [conversationId]: {
                    ...c,
                    selectedDocumentIds: selectedDocIds,
                  },
                },
              };
            });
          }

          // 5. Gọi chat và nhận stream
          const { activeMainSkillId, activeModifierSkillIds } = useSkillStore.getState();

          const stream = streamChat(
            {
              query: trimmedQuery,
              top_k: 5,
              temperature: 0.7,
              stream: true,
              conversation_id: conversationId,
              skill_id: activeMainSkillId,
              modifier_skill_ids: activeModifierSkillIds,
              selected_document_ids: selectedDocIds,
            },
            controller.signal
          );

          for await (const event of stream) {
            if (event.type === "sources") {
              updateAssistant({ sources: event.data });
            } else if (event.type === "token") {
              accumulatedContent += event.data;
              updateAssistant({ content: accumulatedContent });
            } else if (event.type === "error") {
              updateAssistant({
                content: accumulatedContent || event.data || "An error occurred while generating.",
                status: "error",
              });
            } else if (event.type === "done") {
              updateAssistant({
                content: accumulatedContent || "No response received. Please try again.",
                status: "done",
              });

              if (isFirstMessage && nextTitle && nextTitle !== "New chat") {
                renameConversationApi(conversationId, nextTitle).catch(() => {});
              }
            }
          }
        } catch (err: unknown) {
          if (controller.signal.aborted) {
            updateAssistant({ status: "stopped" });
          } else {
            const errText = err instanceof Error ? err.message : "Failed to connect to assistant";
            updateAssistant({
              content: accumulatedContent || errText,
              status: "error",
            });
          }
        } finally {
          // Luôn chạy, kể cả khi return sớm hoặc gặp lỗi:
          // Đảm bảo không bao giờ để sót message ở trạng thái "streaming" khi isStreaming = false
          if (activeAbortController === controller) {
            activeAbortController = null;
          }
          set((state) => {
            const conv = state.conversations[conversationId];
            if (!conv) {
              return { isStreaming: false };
            }
            const hasStuckStreaming = conv.messages.some(
              (m) => m.id === assistantMsgId && m.status === "streaming"
            );
            if (!hasStuckStreaming) {
              return { isStreaming: false };
            }
            const fallbackStatus: MessageStatus = controller.signal.aborted
              ? "stopped"
              : accumulatedContent.trim().length > 0
              ? "done"
              : "error";
            return {
              isStreaming: false,
              conversations: {
                ...state.conversations,
                [conversationId]: {
                  ...conv,
                  messages: conv.messages.map((m) =>
                    m.id === assistantMsgId && m.status === "streaming"
                      ? {
                          ...m,
                          content:
                            m.content ||
                            accumulatedContent ||
                            (fallbackStatus === "error"
                              ? "No response received. Please try again."
                              : ""),
                          status: fallbackStatus,
                        }
                      : m
                  ),
                  updatedAt: Date.now(),
                },
              },
            };
          });
        }
      },

      stopStreaming: () => {
        if (activeAbortController) {
          activeAbortController.abort();
          activeAbortController = null;
        }
        set((state) => {
          const currentId = state.activeId;
          const conv = currentId ? state.conversations[currentId] : null;
          if (!currentId || !conv) {
            return { isStreaming: false };
          }
          return {
            isStreaming: false,
            conversations: {
              ...state.conversations,
              [currentId]: {
                ...conv,
                messages: conv.messages.map((m) =>
                  m.status === "streaming"
                    ? { ...m, status: m.content.trim().length > 0 ? "done" : "stopped" }
                    : m
                ),
              },
            },
          };
        });
      },

      regenerate: async (messageId?: string) => {
        const activeId = get().activeId;
        if (!activeId) return;
        const conv = get().conversations[activeId];
        if (!conv) return;

        let targetIndex = -1;
        if (messageId) {
          targetIndex = conv.messages.findIndex((m) => m.id === messageId);
        } else {
          // Last assistant message
          for (let i = conv.messages.length - 1; i >= 0; i--) {
            if (conv.messages[i].role === "assistant") {
              targetIndex = i;
              break;
            }
          }
        }

        if (targetIndex === -1) return;

        // Find the preceding user message
        let userPrompt = "";
        for (let i = targetIndex - 1; i >= 0; i--) {
          if (conv.messages[i].role === "user") {
            userPrompt = conv.messages[i].content;
            break;
          }
        }

        if (!userPrompt) return;

        // Remove the target assistant message
        set((state) => {
          const currentConv = state.conversations[activeId];
          const newMessages = [...currentConv.messages];
          newMessages.splice(targetIndex, 1);
          return {
            conversations: {
              ...state.conversations,
              [activeId]: {
                ...currentConv,
                messages: newMessages,
              },
            },
          };
        });

        // Resend query
        await get().sendMessage(userPrompt);
      },

      setFeedback: (conversationId: string, messageId: string, feedback: "up" | "down") => {
        set((state) => {
          const conv = state.conversations[conversationId];
          if (!conv) return state;
          return {
            conversations: {
              ...state.conversations,
              [conversationId]: {
                ...conv,
                messages: conv.messages.map((m) =>
                  m.id === messageId
                    ? {
                        ...m,
                        feedback: m.feedback === feedback ? undefined : feedback,
                      }
                    : m
                ),
              },
            },
          };
        });
      },

      clearAll: async () => {
        const ids = Object.keys(get().conversations);
        set({
          conversations: {},
          order: [],
          activeId: null,
          isStreaming: false,
        });

        await Promise.allSettled(ids.map((id) => deleteConversationApi(id)));
      },
    }),
    {
      name: "rag_chat_history",
      storage: createJSONStorage(() => localStorage),
      // Clean up incomplete streaming state when loading from storage
      partialize: (state) => {
        const sanitizedConversations: Record<string, Conversation> = {};
        for (const [id, conv] of Object.entries(state.conversations)) {
          sanitizedConversations[id] = {
            ...conv,
            messages: conv.messages.map((m) =>
              m.status === "streaming" ? { ...m, status: "stopped" } : m
            ),
          };
        }
        return {
          conversations: sanitizedConversations,
          order: state.order,
          activeId: state.activeId,
        };
      },
    }
  )
);
