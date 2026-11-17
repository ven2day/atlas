// Which indicators are on, and with what parameters. Persisted, because an operator who has
// dialled in an EMA ribbon does not want to rebuild it on every reload.

import { create } from "zustand";
import { persist } from "zustand/middleware";

import { BY_ID, clampParams, defaultsOf, type ActiveIndicator, type IndicatorDef, type Params } from "./indicators";

/** Oscillators each need their own pane; more than three and the price chart stops being the point. */
export const MAX_PANES = 3;
export const MAX_ACTIVE = 10;

let counter = 0;
function nextKey(id: string): string {
  counter += 1;
  return `${id}-${Date.now().toString(36)}-${counter}`;
}

interface IndicatorState {
  active: ActiveIndicator[];
  add: (id: string, params?: Params) => void;
  remove: (key: string) => void;
  setParams: (key: string, params: Params) => void;
  replace: (items: { id: string; params?: Params }[]) => void;
  clear: () => void;
}

function normalise(items: ActiveIndicator[]): ActiveIndicator[] {
  const out: ActiveIndicator[] = [];
  for (const item of items) {
    const def = BY_ID.get(item.id);
    if (!def) continue; // an indicator that no longer exists: drop it rather than draw nothing
    out.push({ key: item.key || nextKey(item.id), id: item.id, params: clampParams(def, item.params ?? {}) });
  }
  return out;
}

export const useIndicators = create<IndicatorState>()(
  persist(
    (set, get) => ({
      active: [],
      add: (id, params) => {
        const def = BY_ID.get(id);
        if (!def || get().active.length >= MAX_ACTIVE) return;
        set({
          active: [...get().active, { key: nextKey(id), id, params: clampParams(def, params ?? defaultsOf(def)) }],
        });
      },
      remove: (key) => set({ active: get().active.filter((a) => a.key !== key) }),
      setParams: (key, params) =>
        set({
          active: get().active.map((a) => {
            if (a.key !== key) return a;
            const def = BY_ID.get(a.id);
            return def ? { ...a, params: clampParams(def, { ...a.params, ...params }) } : a;
          }),
        }),
      replace: (items) => set({ active: normalise(items.map((i) => ({ key: nextKey(i.id), id: i.id, params: i.params ?? {} }))).slice(0, MAX_ACTIVE) }),
      clear: () => set({ active: [] }),
    }),
    {
      name: "rq.indicators",
      version: 1,
      merge: (persisted, current) => {
        const state = (persisted ?? {}) as Partial<IndicatorState>;
        return { ...current, active: normalise(state.active ?? []) };
      },
    },
  ),
);

/** How many of the active indicators need a pane of their own. */
export function paneCount(active: ActiveIndicator[]): number {
  return active.filter((a) => BY_ID.get(a.id)?.placement === "pane").length;
}

/** Why another indicator cannot be added right now, or null when it can. */
export function addBlocked(active: ActiveIndicator[], def: IndicatorDef): string | null {
  if (active.length >= MAX_ACTIVE) return `At most ${MAX_ACTIVE} indicators at once`;
  if (def.placement === "pane" && paneCount(active) >= MAX_PANES) {
    return `At most ${MAX_PANES} separate indicator panes`;
  }
  return null;
}
