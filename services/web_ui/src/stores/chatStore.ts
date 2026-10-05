import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import type { ChatMessage, Conversation } from "../types/chat";
import type { Source } from "../types/api";
import { streamChat } from "../api/sse";
import { uploadDocumentFile } from "../api/endpoints";
import { titleFromQuery } from "../lib/titleFromQuery";
import { useToastStore } from "./toastStore";

interface ChatState {
  conversations: Record<string, Conversation>;
  order: string[]; // List of conversation IDs in reverse-chronological order
  activeId: string | null;
  isStreaming: boolean;

  newConversation: () => string;
  selectConversation: (id: string) => void;
  renameConversation: (id: string, newTitle: string) => void;
  deleteConversation: (id: string) => void;
  sendMessage: (query: string, attachedFiles?: File[]) => Promise<void>;
  stopStreaming: () => void;
  regenerate: (messageId?: string) => Promise<void>;
  setFeedback: (conversationId: string, messageId: string, feedback: "up" | "down") => void;
  clearAll: () => void;
}

// Module-level abort controller for active streaming session
let activeAbortController: AbortController | null = null;

function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return "id-" + Math.random().toString(36).slice(2, 11) + "-" + Date.now().toString(36);
}

export const useChatStore = create<ChatState>()(
  persist(
    (set, get) => ({
      conversations: {},
      order: [],
      activeId: null,
      isStreaming: false,

      newConversation: () => {
        const id = generateId();
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

      selectConversation: (id: string) => {
        if (get().conversations[id]) {
          set({ activeId: id });
        }
      },

      renameConversation: (id: string, newTitle: string) => {
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
      },

      deleteConversation: (id: string) => {
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

        // 1. Handle file uploads if any
        if (hasFiles) {
          set({ isStreaming: true });
          const uploadMsgId = generateId();
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
              await uploadDocumentFile(file);
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
        const userMsgId = generateId();
        const userMsg: ChatMessage = {
          id: userMsgId,
          role: "user",
          content: trimmedQuery,
          sources: [],
          status: "done",
          createdAt: Date.now(),
        };

        // 3. Add Assistant Message placeholder
        const assistantMsgId = generateId();
        const assistantMsg: ChatMessage = {
          id: assistantMsgId,
          role: "assistant",
          content: "",
          sources: [],
          status: "streaming",
          createdAt: Date.now(),
        };

        set((state) => {
          const conv = state.conversations[activeId!];
          const isFirstMessage = conv.messages.filter((m) => m.role === "user").length === 0;
          const nextTitle = isFirstMessage ? titleFromQuery(trimmedQuery) : conv.title;

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
          const stream = streamChat(
            { query: trimmedQuery, top_k: 5, temperature: 0.7, stream: true },
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

      clearAll: () => {
        set({
          conversations: {},
          order: [],
          activeId: null,
          isStreaming: false,
        });
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
