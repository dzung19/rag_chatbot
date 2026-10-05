import { create } from "zustand";
import { persist, createJSONStorage } from "zustand/middleware";

export type ThemeMode = "dark" | "light" | "system";

interface UiState {
  sidebarCollapsed: boolean;
  mobileDrawerOpen: boolean;
  theme: ThemeMode;
  userName: string;
  toggleSidebar: () => void;
  setSidebarCollapsed: (collapsed: boolean) => void;
  setMobileDrawerOpen: (open: boolean) => void;
  setTheme: (theme: ThemeMode) => void;
  setUserName: (name: string) => void;
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      sidebarCollapsed: false,
      mobileDrawerOpen: false,
      theme: "dark",
      userName: "Phung",
      toggleSidebar: () => set((state) => ({ sidebarCollapsed: !state.sidebarCollapsed })),
      setSidebarCollapsed: (collapsed) => set({ sidebarCollapsed: collapsed }),
      setMobileDrawerOpen: (open) => set({ mobileDrawerOpen: open }),
      setTheme: (theme) => set({ theme }),
      setUserName: (userName) => set({ userName }),
    }),
    {
      name: "rag_ui_settings",
      storage: createJSONStorage(() => localStorage),
    }
  )
);
