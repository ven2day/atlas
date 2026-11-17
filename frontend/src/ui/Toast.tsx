// Toasts for alerts and control outcomes, announced through an aria-live region.

import { create } from "zustand";

import { cx } from "../lib/cx";

export type ToastLevel = "info" | "warn" | "crit";

export interface ToastItem {
  id: number;
  level: ToastLevel;
  title: string;
  body?: string;
}

interface ToastState {
  toasts: ToastItem[];
  push: (toast: Omit<ToastItem, "id">) => void;
  dismiss: (id: number) => void;
}

let next = 1;

export const useToasts = create<ToastState>((set, get) => ({
  toasts: [],
  push: (toast) => {
    const id = next++;
    set({ toasts: [...get().toasts.slice(-4), { ...toast, id }] });
    if (toast.level === "info") setTimeout(() => get().dismiss(id), 6_000);
  },
  dismiss: (id) => set({ toasts: get().toasts.filter((t) => t.id !== id) }),
}));

const LEVEL = {
  info: "border-line-strong bg-bg-2",
  warn: "border-warn bg-bg-2",
  crit: "border-down bg-down-soft",
} as const;

const TITLE = {
  info: "text-fg-0",
  warn: "text-warn",
  crit: "text-down",
} as const;

export function Toaster() {
  const { toasts, dismiss } = useToasts();
  return (
    <div
      role="status"
      aria-live="polite"
      className="pointer-events-none fixed bottom-14 right-4 z-50 flex w-[380px] max-w-[calc(100vw-2rem)] flex-col gap-2.5"
    >
      {toasts.map((t) => (
        <div
          key={t.id}
          className={cx(
            "pointer-events-auto rounded-lg border px-4 py-3 shadow-popover animate-rise-in",
            LEVEL[t.level],
          )}
        >
          <div className="flex items-start justify-between gap-3">
            <p className={cx("text-sm font-semibold", TITLE[t.level])}>{t.title}</p>
            <button
              type="button"
              aria-label="Dismiss"
              onClick={() => dismiss(t.id)}
              className="-mr-1 -mt-1 flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-fg-2 transition-colors duration-150 hover:bg-bg-3 hover:text-fg-0"
            >
              <svg aria-hidden="true" width="12" height="12" viewBox="0 0 12 12" fill="none">
                <path d="M1.5 1.5l9 9m0-9-9 9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
            </button>
          </div>
          {t.body && <p className="mt-1 text-sm leading-relaxed text-fg-1">{t.body}</p>}
        </div>
      ))}
    </div>
  );
}
