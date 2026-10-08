import { create } from "zustand";
import { getJSON } from "../api/client";
import type { Skill, SkillListResponse } from "../types/api";

interface SkillState {
  skills: Skill[];
  isLoading: boolean;
  error: string | null;
  fetchSkills: () => Promise<void>;
  
  activeMainSkillId: string;
  activeModifierSkillIds: string[];
  setActiveMainSkillId: (id: string) => void;
  toggleModifierSkillId: (id: string) => void;
}

export const useSkillStore = create<SkillState>((set, get) => ({
  skills: [],
  isLoading: false,
  error: null,
  activeMainSkillId: "general-assistant",
  activeModifierSkillIds: [],
  
  fetchSkills: async () => {
    set({ isLoading: true, error: null });
    try {
      const res = await getJSON<SkillListResponse>("/skills");
      set({ skills: res.skills, isLoading: false });
    } catch (e: any) {
      set({ error: e.message || "Failed to fetch skills", isLoading: false });
    }
  },
  
  setActiveMainSkillId: (id: string) => set({ activeMainSkillId: id }),
  
  toggleModifierSkillId: (id: string) => {
    const current = get().activeModifierSkillIds;
    if (current.includes(id)) {
      set({ activeModifierSkillIds: current.filter(x => x !== id) });
    } else {
      set({ activeModifierSkillIds: [...current, id] });
    }
  }
}));
