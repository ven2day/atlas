// Operator preferences, kept in localStorage. Single-key shortcuts are OFF by default
// (WCAG 2.1.4): Ctrl/⌘+K always works.

import { create } from "zustand";
import { persist } from "zustand/middleware";

export type Density = "comfortable" | "compact";

interface Settings {
  shortcuts: boolean;
  /** Table row height: 40 px (comfortable) or 32 px (compact). */
  density: Density;
  /** Desktop sidebar collapsed to its icon rail. Persisted: it is a workspace preference. */
  navCollapsed: boolean;
  toggleShortcuts: () => void;
  setDensity: (density: Density) => void;
  setNavCollapsed: (collapsed: boolean) => void;
}

export const useSettings = create<Settings>()(
  persist(
    (set, get) => ({
      shortcuts: false,
      density: "comfortable",
      navCollapsed: false,
      toggleShortcuts: () => set({ shortcuts: !get().shortcuts }),
      setDensity: (density) => set({ density }),
      setNavCollapsed: (navCollapsed) => set({ navCollapsed }),
    }),
    {
      name: "rq.settings",
      version: 3,
      // v1 stored `navExpanded` for an older icon rail; v2 dropped it when navigation became a
      // horizontal bar. v3 brings a collapsible sidebar back, defaulting to expanded. Anything
      // older is ignored rather than guessed at.
      migrate: (state) => {
        const previous = (state ?? {}) as Partial<Settings>;
        return {
          shortcuts: previous.shortcuts ?? false,
          density: previous.density ?? "comfortable",
          navCollapsed: previous.navCollapsed ?? false,
        };
      },
    },
  ),
);
