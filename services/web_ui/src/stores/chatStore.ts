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
    status = "streaming";
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
        const id = generateUuid();
        const newConv: Conversation = {
          id,
          title: "New chat",
          messages: [],
          updatedAt: Date.now(),
        };

        set((state) => ({
          conversations: { ...state.conversations, [id]: newConv },
          order: [id, ...state.order.filter((x) => x !== id)],
          activeId: id,
        }));

        return id;
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

            // Keep local-only conversations (e.g. freshly created blank chats) at the top
            const localOnly = state.order.filter((id) => !backendOrder.includes(id));
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
        if (state.conversations[id]) {
          set({ activeId: id });
        }

        // Avoid overwriting conversation state if currently streaming this conversation
        if (get().isStreaming && get().activeId === id) {
          return;
        }

        const currentConv = get().conversations[id];

        try {
          const detail = await fetchConversation(id);
          const messages = (detail.messages || []).map(mapStoredMessageToChatMessage);

          set((s) => {
            const prev = s.conversations[id];
            const updatedConv: Conversation = {
              id: detail.id,
              title: detail.title || prev?.title || "New chat",
              messages,
              updatedAt: detail.updated_at
                ? new Date(detail.updated_at).getTime()
                : (prev?.updatedAt ?? Date.now()),
            };

            const nextOrder = s.order.includes(id) ? s.order : [id, ...s.order];

            return {
              conversations: {
                ...s.conversations,
                [id]: updatedConv,
              },
              order: nextOrder,
              activeId: id,
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
        const hasFiles = attachedFiles && attachedFiles.length > 0;
        if (!trimmedQuery && !hasFiles) return;
        if (get().isStreaming) return;

        let activeId = get().activeId;
        if (!activeId || !get().conversations[activeId]) {
          activeId = get().newConversation();
        }

        const uploadedDocumentIds: string[] = [];

        // 1. Handle file uploads if any
        if (hasFiles) {
          set({ isStreaming: true });
          const uploadMsgId = generateUuid();
          const uploadMsg: ChatMessage = {
            id: uploadMsgId,
            role: "assistant",
            content: `Uploading ${attachedFiles.length} attached document(s)...`,
            sources: [],
            status: "streaming",
            createdAt: Date.now(),
          };

          set((state) => {
            const conv = state.conversations[activeId!];
            return {
              conversations: {
                ...state.conversations,
                [activeId!]: {
                  ...conv,
                  messages: [...conv.messages, uploadMsg],
                  updatedAt: Date.now(),
                },
              },
            };
          });

          try {
            for (const file of attachedFiles) {
              const res = await uploadDocumentFile(file);
              if (res?.document_id) {
                uploadedDocumentIds.push(res.document_id);
              }
            }

            // Update upload assistant message
            set((state) => {
              const conv = state.conversations[activeId!];
              return {
                conversations: {
                  ...state.conversations,
                  [activeId!]: {
                    ...conv,
                    messages: conv.messages.map((m) =>
                      m.id === uploadMsgId
                        ? {
                            ...m,
                            content: `Successfully uploaded ${attachedFiles.length} document(s). Processing query...`,
                            status: "done",
                          }
                        : m
                    ),
                    updatedAt: Date.now(),
                  },
                },
              };
            });
            useToastStore.getState().pushToast("Attached documents uploaded successfully", "success");
          } catch (err: unknown) {
            const errorMsg = err instanceof Error ? err.message : "Failed to upload attachments";
            set((state) => {
              const conv = state.conversations[activeId!];
              return {
                isStreaming: false,
                conversations: {
                  ...state.conversations,
                  [activeId!]: {
                    ...conv,
                    messages: conv.messages.map((m) =>
                      m.id === uploadMsgId
                        ? {
                            ...m,
                            content: `Failed to upload documents: ${errorMsg}`,
                            status: "error",
                          }
                        : m
                    ),
                    updatedAt: Date.now(),
                  },
                },
              };
            });
            useToastStore.getState().pushToast(errorMsg, "error");
            return;
          }
        }

        // If no text query was provided, stop here after upload
        if (!trimmedQuery) {
          set({ isStreaming: false });
          return;
        }

        // 2. Add User Message
        const userMsgId = generateUuid();
        const userMsg: ChatMessage = {
          id: userMsgId,
          role: "user",
          content: trimmedQuery,
          sources: [],
          status: "done",
          createdAt: Date.now(),
        };

        // 3. Add Assistant Message placeholder
        const assistantMsgId = generateUuid();
        const assistantMsg: ChatMessage = {
          id: assistantMsgId,
          role: "assistant",
          content: "",
          sources: [],
          status: "streaming",
          createdAt: Date.now(),
        };

        let isFirstMessage = false;
        let nextTitle = "";

        set((state) => {
          const conv = state.conversations[activeId!];
          isFirstMessage = conv.messages.filter((m) => m.role === "user").length === 0;
          nextTitle = isFirstMessage ? titleFromQuery(trimmedQuery) : conv.title;

          return {
            isStreaming: true,
            conversations: {
              ...state.conversations,
              [activeId!]: {
                ...conv,
                title: nextTitle,
                messages: [...conv.messages, userMsg, assistantMsg],
                updatedAt: Date.now(),
              },
            },
          };
        });

        // 4. Start streaming via SSE
        activeAbortController = new AbortController();

        let accumulatedContent = "";
        let sources: Source[] = [];

        try {
          const { activeMainSkillId, activeModifierSkillIds } = useSkillStore.getState();
          const stream = streamChat(
            {
              query: trimmedQuery,
              top_k: 5,
              temperature: 0.7,
              stream: true,
              conversation_id: activeId,
              skill_id: activeMainSkillId,
              modifier_skill_ids: activeModifierSkillIds,
              selected_document_ids: uploadedDocumentIds,
            },
            activeAbortController.signal
          );

          for await (const event of stream) {
            if (event.type === "sources") {
              sources = event.data;
              set((state) => {
                const conv = state.conversations[activeId!];
                if (!conv) return state;
                return {
                  conversations: {
                    ...state.conversations,
                    [activeId!]: {
                      ...conv,
                      messages: conv.messages.map((m) =>
                        m.id === assistantMsgId ? { ...m, sources } : m
                      ),
                      updatedAt: Date.now(),
                    },
                  },
                };
              });
            } else if (event.type === "token") {
              accumulatedContent += event.data;
              set((state) => {
                const conv = state.conversations[activeId!];
                if (!conv) return state;
                return {
                  conversations: {
                    ...state.conversations,
                    [activeId!]: {
                      ...conv,
                      messages: conv.messages.map((m) =>
                        m.id === assistantMsgId ? { ...m, content: accumulatedContent } : m
                      ),
                      updatedAt: Date.now(),
                    },
                  },
                };
              });
            } else if (event.type === "error") {
              set((state) => {
                const conv = state.conversations[activeId!];
                if (!conv) return state;
                return {
                  conversations: {
                    ...state.conversations,
                    [activeId!]: {
                      ...conv,
                      messages: conv.messages.map((m) =>
                        m.id === assistantMsgId
                          ? {
                              ...m,
                              content: accumulatedContent || event.data || "An error occurred while generating.",
                              status: "error",
                            }
                          : m
                      ),
                      updatedAt: Date.now(),
                    },
                  },
                };
              });
            } else if (event.type === "done") {
              set((state) => {
                const conv = state.conversations[activeId!];
                if (!conv) return state;
                return {
                  conversations: {
                    ...state.conversations,
                    [activeId!]: {
                      ...conv,
                      messages: conv.messages.map((m) =>
                        m.id === assistantMsgId
                          ? {
                              ...m,
                              content: accumulatedContent || "No response received. Please try again.",
                              status: "done",
                            }
                          : m
                      ),
                      updatedAt: Date.now(),
                    },
                  },
                };
              });

              // If first message in chat, sync title to backend
              if (isFirstMessage && nextTitle && nextTitle !== "New chat") {
                renameConversationApi(activeId, nextTitle).catch(() => {});
              }
            }
          }
        } catch (err: unknown) {
          if (activeAbortController?.signal.aborted) {
            set((state) => {
              const conv = state.conversations[activeId!];
              if (!conv) return state;
              return {
                conversations: {
                  ...state.conversations,
                  [activeId!]: {
                    ...conv,
                    messages: conv.messages.map((m) =>
                      m.id === assistantMsgId ? { ...m, status: "stopped" } : m
                    ),
                    updatedAt: Date.now(),
                  },
                },
              };
            });
          } else {
            const errText = err instanceof Error ? err.message : "Failed to connect to assistant";
            set((state) => {
              const conv = state.conversations[activeId!];
              if (!conv) return state;
              return {
                conversations: {
                  ...state.conversations,
                  [activeId!]: {
                    ...conv,
                    messages: conv.messages.map((m) =>
                      m.id === assistantMsgId
                        ? {
                            ...m,
                            content: accumulatedContent || errText,
                            status: "error",
                          }
                        : m
                    ),
                    updatedAt: Date.now(),
                  },
                },
              };
            });
          }
        } finally {
          activeAbortController = null;
          set({ isStreaming: false });
        }
      },

      stopStreaming: () => {
        if (activeAbortController) {
          activeAbortController.abort();
          activeAbortController = null;
        }
        set({ isStreaming: false });
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
