import type { ReactNode } from "react";

import { cx } from "../lib/cx";

/** Label over a value, with an optional delta and hint line. The value is always tabular. */
export function Stat({
  label,
  value,
  delta,
  hint,
  className,
  size = "md",
}: {
  label: ReactNode;
  value: ReactNode;
  delta?: ReactNode;
  hint?: ReactNode;
  className?: string;
  size?: "sm" | "md" | "lg";
}) {
  return (
    <div className={cx("flex min-w-0 flex-col gap-1", className)}>
      <span className="rq-label truncate">{label}</span>
      <span
        className={cx(
          "num truncate font-semibold tracking-tight text-fg-0",
          size === "sm" ? "text-lg" : size === "lg" ? "text-3xl" : "text-2xl",
        )}
      >
        {value}
      </span>
      {delta !== undefined && <span className="truncate text-xs">{delta}</span>}
      {hint !== undefined && <span className="truncate text-2xs text-fg-2">{hint}</span>}
    </div>
  );
}

/** A `Stat` in its own tile: the summary strips at the top of a screen. */
export function StatCard({
  label,
  value,
  delta,
  hint,
  className,
  size = "md",
}: Parameters<typeof Stat>[0]) {
  return (
    <div className={cx("rq-card px-4 py-3.5", className)}>
      <Stat label={label} value={value} delta={delta} hint={hint} size={size} />
    </div>
  );
}
