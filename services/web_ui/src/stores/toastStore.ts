import { create } from "zustand";

export interface ToastItem {
  id: string;
  type: "success" | "error" | "info";
  message: string;
}

interface ToastState {
  toasts: ToastItem[];
  pushToast: (message: string, type?: "success" | "error" | "info") => void;
  dismissToast: (id: string) => void;
}

export const useToastStore = create<ToastState>()((set) => ({
  toasts: [],
  pushToast: (message: string, type: "success" | "error" | "info" = "info") => {
    const id = Math.random().toString(36).slice(2, 9);
    set((state) => ({
      toasts: [...state.toasts, { id, type, message }],
    }));

    // Auto-dismiss after 4 seconds
    setTimeout(() => {
      useToastStore.getState().dismissToast(id);
    }, 4000);
  },
  dismissToast: (id: string) => {
    set((state) => ({
      toasts: state.toasts.filter((t) => t.id !== id),
    }));
  },
}));
