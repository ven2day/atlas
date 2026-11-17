import type { ReactNode } from "react";

import { cx } from "../lib/cx";

export interface SegmentOption<T extends string> {
  value: T;
  label: ReactNode;
  title?: string;
  disabled?: boolean;
}

/** A compact single-choice control: chart type, history range, book, density. */
export function Segmented<T extends string>({
  value,
  onChange,
  options,
  label,
  size = "sm",
  className,
}: {
  value: T | undefined;
  onChange: (value: T) => void;
  options: SegmentOption<T>[];
  label: string;
  size?: "xs" | "sm";
  className?: string;
}) {
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cx("inline-flex items-center gap-0.5 rounded-lg border border-line bg-bg-2 p-0.5", className)}
    >
      {options.map((o) => {
        const active = o.value === value;
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            title={o.title}
            disabled={o.disabled}
            onClick={() => onChange(o.value)}
            className={cx(
              "rounded-md font-medium transition-colors duration-150 disabled:cursor-not-allowed disabled:opacity-40",
              size === "xs" ? "h-6 px-2 text-2xs" : "h-7 px-2.5 text-xs",
              active ? "bg-bg-3 text-fg-0 shadow-card" : "text-fg-2 hover:text-fg-0",
            )}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

/** A pill that toggles one thing on and off (volume, markers, an indicator). */
export function Toggle({
  checked,
  onChange,
  children,
  title,
  className,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  children: ReactNode;
  title?: string;
  className?: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      title={title}
      onClick={() => onChange(!checked)}
      className={cx(
        "inline-flex h-7 items-center gap-1.5 rounded-md border px-2.5 text-xs font-medium transition-colors duration-150",
        checked
          ? "border-accent-line bg-accent-soft text-accent-fg"
          : "border-line-strong bg-bg-2 text-fg-2 hover:text-fg-0",
        className,
      )}
    >
      <span
        aria-hidden="true"
        className={cx("h-1.5 w-1.5 rounded-full", checked ? "bg-accent" : "bg-fg-2")}
      />
      {children}
    </button>
  );
}
