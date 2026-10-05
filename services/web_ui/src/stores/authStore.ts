import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";
import { setApiKey as setClientApiKey, setUnauthorizedHandler } from "../api/client";

interface AuthState {
  apiKey: string;
  isKeyModalOpen: boolean;
  setApiKey: (key: string) => void;
  openKeyModal: () => void;
  closeKeyModal: () => void;
  logout: () => void;
}

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      apiKey: "",
      isKeyModalOpen: false,
      setApiKey: (key: string) => {
        const trimmed = key.trim();
        setClientApiKey(trimmed);
        set({ apiKey: trimmed, isKeyModalOpen: false });
      },
      openKeyModal: () => set({ isKeyModalOpen: true }),
      closeKeyModal: () => set({ isKeyModalOpen: false }),
      logout: () => {
        setClientApiKey("");
        set({ apiKey: "", isKeyModalOpen: true });
      },
    }),
    {
      name: "rag_auth",
      storage: createJSONStorage(() => sessionStorage),
      onRehydrateStorage: () => (state) => {
        if (state?.apiKey) {
          setClientApiKey(state.apiKey);
        } else {
          state?.openKeyModal();
        }
      },
    }
  )
);

// Connect 401/403 callback to auto-open key modal
setUnauthorizedHandler(() => {
  useAuthStore.getState().logout();
});
