// Operator preferences, kept in localStorage. Single-key shortcuts are OFF by default
// (WCAG 2.1.4): Ctrl/⌘+K always works.

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Density = "comfortable" | "compact";

interface Settings {
  shortcuts: boolean;
  /** Table row height: 40 px (comfortable) or 32 px (compact). */
  density: Density;
  toggleShortcuts: () => void;
  setDensity: (density: Density) => void;
}

export const useSettings = create<Settings>()(
  persist(
    (set, get) => ({
      shortcuts: false,
      density: "comfortable",
      toggleShortcuts: () => set({ shortcuts: !get().shortcuts }),
      setDensity: (density) => set({ density }),
    }),
    {
      name: "rq.settings",
      version: 2,
      // v1 also stored `navExpanded` for the old icon rail, which the redesign replaced; taking
      // only the two keys that still exist drops it.
      migrate: (state) => {
        const previous = (state ?? {}) as Partial<Settings>;
        return {
          shortcuts: previous.shortcuts ?? false,
          density: previous.density ?? "comfortable",
        };
      },
    },
  ),
);
