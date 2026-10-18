import { create } from "zustand";
import type { DocumentInfo, DocumentStatusEventPayload } from "../types/api";
import type { UploadProgressItem } from "../types/chat";
import {
  fetchDocuments,
  uploadDocumentFile,
  deleteDocument,
  triggerSharePointSync,
  getSharePointAuthUrl,
} from "../api/endpoints";
import { useToastStore } from "./toastStore";

let documentsFetchInFlight: Promise<void> | null = null;
let documentEventSource: EventSource | null = null;
let eventSourceSubscribers = 0;

interface DocumentsState {
  documents: DocumentInfo[];
  isLoading: boolean;
  isSyncing: boolean;
  uploads: UploadProgressItem[];

  fetchDocuments: (silent?: boolean) => Promise<void>;
  uploadFiles: (files: File[]) => Promise<void>;
  deleteDocument: (id: string) => Promise<void>;
  syncSharePoint: () => Promise<void>;
  initDocumentEvents: () => () => void;
  handleDocumentStatusUpdate: (payload: DocumentStatusEventPayload) => void;
}

export const useDocumentsStore = create<DocumentsState>()((set, get) => ({
  documents: [],
  isLoading: false,
  isSyncing: false,
  uploads: [],

  initDocumentEvents: () => {
    eventSourceSubscribers++;

    if (!documentEventSource) {
      const es = new EventSource("/api/v1/documents/events");
      documentEventSource = es;

      es.addEventListener("document_status", (event: MessageEvent) => {
        try {
          const payload = JSON.parse(event.data) as DocumentStatusEventPayload;
          get().handleDocumentStatusUpdate(payload);
        } catch (err) {
          console.error("Failed to parse document status event:", err);
        }
      });

      es.onerror = (err) => {
        // EventSource will automatically retry in the background
        console.debug("Document SSE connection issue, retrying...", err);
      };
    }

    return () => {
      eventSourceSubscribers = Math.max(0, eventSourceSubscribers - 1);
      if (eventSourceSubscribers === 0 && documentEventSource) {
        documentEventSource.close();
        documentEventSource = null;
      }
    };
  },

  handleDocumentStatusUpdate: (payload: DocumentStatusEventPayload) => {
    const { document_id, filename, status, chunks_count, error } = payload;

    // 1. Update existing document in table state
    set((state) => {
      const exists = state.documents.some((d) => d.document_id === document_id);
      if (exists) {
        return {
          documents: state.documents.map((d) =>
            d.document_id === document_id
              ? {
                  ...d,
                  status: (status === "completed" ? "completed" : status === "failed" ? "failed" : d.status) as DocumentInfo["status"],
                  chunk_count: typeof chunks_count === "number" ? chunks_count : d.chunk_count,
                  error_message: error || d.error_message,
                }
              : d
          ),
        };
      }
      return state;
    });

    // 2. Update progress queue items
    set((state) => ({
      uploads: state.uploads.map((u) => {
        if (u.filename === filename) {
          if (status === "completed") {
            return { ...u, status: "done", progress: 100 };
          }
          if (status === "failed") {
            return { ...u, status: "failed", error: error || "Processing failed" };
          }
        }
        return u;
      }),
    }));

    // If new document not yet in list or status changed, sync list silently
    const exists = get().documents.some((d) => d.document_id === document_id);
    if (!exists) {
      void get().fetchDocuments(true);
    }

    // 3. Real-time push notification toast
    if (status === "completed") {
      useToastStore
        .getState()
        .pushToast(
          `Tài liệu "${filename}" đã được xử lý thành công (${chunks_count ?? 0} chunks).`,
          "success"
        );
    } else if (status === "failed") {
      useToastStore
        .getState()
        .pushToast(
          `Xử lý tài liệu "${filename}" thất bại: ${error || "Lỗi không xác định"}`,
          "error"
        );
    } else if (status === "cancelled") {
      useToastStore
        .getState()
        .pushToast(`Quá trình xử lý tài liệu "${filename}" đã bị hủy.`, "info");
    }
  },

  fetchDocuments: async (silent = false) => {
    // Các nơi gọi đồng thời dùng chung request đang chạy.
    if (documentsFetchInFlight) {
      return documentsFetchInFlight;
    }

    if (!silent) {
      set({ isLoading: true });
    }

    documentsFetchInFlight = (async () => {
      try {
        const data = await fetchDocuments();

        set({
          documents: data.documents || [],
        });
      } catch (err: unknown) {
        const message =
          err instanceof Error
            ? err.message
            : "Failed to load documents";

        if (silent) {
          console.warn("Failed to refresh documents:", message);
        } else {
          useToastStore.getState().pushToast(message, "error");
        }
      } finally {
        if (!silent) {
          set({ isLoading: false });
        }
      }
    })().finally(() => {
      documentsFetchInFlight = null;
    });

    return documentsFetchInFlight;
  },

  uploadFiles: async (files: File[]) => {
    if (!files.length) return;

    // Create queue items
    const newItems: UploadProgressItem[] = files.map((file) => ({
      id: Math.random().toString(36).slice(2, 9),
      filename: file.name,
      progress: 0,
      status: "queued",
    }));

    set((state) => ({ uploads: [...newItems, ...state.uploads] }));

    for (let i = 0; i < files.length; i++) {
      const file = files[i];
      const item = newItems[i];

      // Set uploading
      set((state) => ({
        uploads: state.uploads.map((u) =>
          u.id === item.id ? { ...u, status: "uploading", progress: 60 } : u
        ),
      }));

      try {
        await uploadDocumentFile(file);
        useToastStore.getState().pushToast(`Đã tải lên "${file.name}", đang xử lý trong nền...`, "info");
      } catch (err: unknown) {
        const errorMsg = err instanceof Error ? err.message : "Upload failed";
        set((state) => ({
          uploads: state.uploads.map((u) =>
            u.id === item.id ? { ...u, status: "failed", error: errorMsg } : u
          ),
        }));
        useToastStore.getState().pushToast(`Failed to upload "${file.name}": ${errorMsg}`, "error");
      }
    }

    // Refresh documents list after batch upload to show pending/processing state
    await get().fetchDocuments(true);
  },

  deleteDocument: async (documentId: string) => {
    try {
      await deleteDocument(documentId);
      set((state) => ({
        documents: state.documents.filter((d) => d.document_id !== documentId),
      }));
      useToastStore.getState().pushToast("Document deleted", "success");
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to delete document";
      useToastStore.getState().pushToast(msg, "error");
    }
  },

  syncSharePoint: async () => {
    set({ isSyncing: true });
    try {
      await triggerSharePointSync();
      useToastStore
        .getState()
        .pushToast(
          "SharePoint sync started in background. Documents will appear shortly.",
          "info"
        );
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "";
      if (msg === "O365_AUTH_REQUIRED") {
        try {
          const authData = await getSharePointAuthUrl();
          if (authData.url) {
            window.location.href = authData.url;
            return;
          }
        } catch {
          useToastStore.getState().pushToast("Failed to retrieve Microsoft login URL.", "error");
        }
      } else {
        useToastStore
          .getState()
          .pushToast(msg || "Failed to trigger sync. Check Azure credentials.", "error");
      }
    } finally {
      set({ isSyncing: false });
    }
  },
}));
