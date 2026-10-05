import { create } from "zustand";
import type { DocumentInfo } from "../types/api";
import type { UploadProgressItem } from "../types/chat";
import {
  fetchDocuments,
  uploadDocumentFile,
  deleteDocument,
  triggerSharePointSync,
  getSharePointAuthUrl,
} from "../api/endpoints";
import { useToastStore } from "./toastStore";

interface DocumentsState {
  documents: DocumentInfo[];
  isLoading: boolean;
  isSyncing: boolean;
  uploads: UploadProgressItem[];

  fetchDocuments: () => Promise<void>;
  uploadFiles: (files: File[]) => Promise<void>;
  deleteDocument: (id: string) => Promise<void>;
  syncSharePoint: () => Promise<void>;
}

export const useDocumentsStore = create<DocumentsState>()((set, get) => ({
  documents: [],
  isLoading: false,
  isSyncing: false,
  uploads: [],

  fetchDocuments: async () => {
    set({ isLoading: true });
    try {
      const data = await fetchDocuments();
      set({ documents: data.documents || [], isLoading: false });
    } catch (err: unknown) {
      set({ documents: [], isLoading: false });
      const msg = err instanceof Error ? err.message : "Failed to load documents";
      useToastStore.getState().pushToast(msg, "error");
    }
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
          u.id === item.id ? { ...u, status: "uploading", progress: 50 } : u
        ),
      }));

      try {
        await uploadDocumentFile(file);
        set((state) => ({
          uploads: state.uploads.map((u) =>
            u.id === item.id ? { ...u, status: "done", progress: 100 } : u
          ),
        }));
        useToastStore.getState().pushToast(`Uploaded "${file.name}"`, "success");
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

    // Refresh documents list after batch finishes
    await get().fetchDocuments();
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
