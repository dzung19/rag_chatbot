import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";

import type { AuthUser } from "../types/authTypes";

type AuthState = {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isHydrated: boolean;
  loginForDevelopment: () => void;
  logout: () => void;
  setHydrated: (value: boolean) => void;
};

const developmentUser: AuthUser = {
  id: "local-dev-user",
  name: "Development User",
  email: "dev.user@local.test",
};

export const useAuthStore = create<AuthState>()(
  persist(
    (set) => ({
      user: null,
      isAuthenticated: false,
      isHydrated: false,

      loginForDevelopment: () => {
        if (!import.meta.env.DEV) {
          throw new Error(
            "Development login is disabled outside Vite development mode.",
          );
        }

        set({
          user: developmentUser,
          isAuthenticated: true,
        });
      },

      logout: () => {
        set({
          user: null,
          isAuthenticated: false,
        });
      },

      setHydrated: (value) => {
        set({ isHydrated: value });
      },
    }),
    {
      name: "rag-chatbot-dev-auth",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (state) => ({
        user: state.user,
        isAuthenticated: state.isAuthenticated,
      }),
      onRehydrateStorage: () => (state) => {
        state?.setHydrated(true);
      },
    },
  ),
);
