import { create } from "zustand";
import { fetchHealth } from "../api/endpoints";

export type ConnectionStatus = "checking" | "healthy" | "degraded" | "disconnected";

interface HealthState {
  status: ConnectionStatus;
  modelName: string;
  lastChecked: number | null;
  checkHealth: () => Promise<void>;
  startPolling: (intervalMs?: number) => () => void;
}

export const useHealthStore = create<HealthState>()((set) => ({
  status: "checking",
  modelName: "Unknown",
  lastChecked: null,

  checkHealth: async () => {
    try {
      const data = await fetchHealth();
      set({
        status: data.status === "healthy" ? "healthy" : "degraded",
        modelName: data.model_name || "Gemma 4 E4B",
        lastChecked: Date.now(),
      });
    } catch {
      set({
        status: "disconnected",
        lastChecked: Date.now(),
      });
    }
  },

  startPolling: (intervalMs: number = 30000) => {
    // Initial check
    useHealthStore.getState().checkHealth();
    const intervalId = window.setInterval(() => {
      useHealthStore.getState().checkHealth();
    }, intervalMs);

    return () => window.clearInterval(intervalId);
  },
}));
