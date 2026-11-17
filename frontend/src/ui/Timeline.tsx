import type { ReactNode } from "react";

import { cx } from "../lib/cx";

export interface Step {
  id: string;
  title: ReactNode;
  meta?: ReactNode; // e.g. a time or a badge
  tone?: "neutral" | "warn" | "crit" | "accent";
  children?: ReactNode;
}

const DOT = {
  neutral: "border-line-strong bg-bg-3",
  warn: "border-warn bg-warn",
  crit: "border-down bg-down",
  accent: "border-accent bg-accent",
};

/** Vertical lineage steps, one card each, joined by a rail (the Decision Inspector). */
export function Timeline({ steps }: { steps: Step[] }) {
  return (
    <ol className="relative flex flex-col gap-3 pl-7">
      <span aria-hidden="true" className="absolute bottom-5 left-[10px] top-5 w-px bg-line-strong" />
      {steps.map((step) => (
        <li key={step.id} className="relative">
          <span
            aria-hidden="true"
            className={cx("absolute -left-7 top-5 h-[21px] w-[21px] rounded-full border-2", DOT[step.tone ?? "neutral"])}
          />
          <div className="rounded-lg border border-line bg-bg-2 px-4 py-4">
            <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
              <h3 className="text-sm font-semibold tracking-tight text-fg-0">{step.title}</h3>
              {step.meta && <div className="shrink-0 text-xs text-fg-2">{step.meta}</div>}
            </div>
            {step.children && <div className="mt-3 text-sm text-fg-1">{step.children}</div>}
          </div>
        </li>
      ))}
    </ol>
  );
}
