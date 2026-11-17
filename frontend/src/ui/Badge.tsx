// Status badges. Colour is never the only signal: the word is always in the badge, and green /
// red stay reserved for direction (P&L, BUY/SELL) while amber marks a degraded or blocked state
// and solid red a critical one (a latched kill switch).

import type { ReactNode } from "react";

import { cx } from "../lib/cx";

export type Tone = "neutral" | "up" | "down" | "warn" | "crit" | "accent";

const STYLE: Record<Tone, string> = {
  neutral: "border-line-strong bg-bg-2 text-fg-1",
  up: "border-transparent bg-up-soft text-up",
  down: "border-transparent bg-down-soft text-down",
  warn: "border-transparent bg-warn-soft text-warn",
  crit: "border-crit-bg bg-crit-bg text-crit-fg",
  accent: "border-accent-line bg-accent-soft text-accent-fg",
};

const TONE_OF: Record<string, Tone> = {
  BUY: "up",
  SELL: "down",
  VETO: "warn",
  VETOED: "warn",
  ABSTAIN: "warn",
  DEGRADED: "warn",
  DEMO: "warn",
  HALT_NEW: "warn",
  HALTED: "warn",
  REJECTED: "warn",
  RISK_REJECTED: "warn",
  BROKER_REJECTED: "warn",
  UNKNOWN: "warn",
  RESIZED: "warn",
  FLATTEN: "crit",
  CRITICAL: "crit",
  WARNING: "warn",
  FILLED: "neutral",
  OPEN: "accent",
  SUBMITTED: "accent",
  ARMED: "up",
};

/** The tone a status word gets unless one is given. */
export function toneOf(status: string): Tone {
  return TONE_OF[status.toUpperCase()] ?? "neutral";
}

export function Badge({
  children,
  tone,
  title,
  className,
  mono = false,
  size = "md",
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
  /** Reason codes and ids read better in the monospace face. */
  mono?: boolean;
  size?: "sm" | "md";
}) {
  const resolved = tone ?? (typeof children === "string" ? toneOf(children) : "neutral");
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center whitespace-nowrap rounded-full border font-medium uppercase leading-none tracking-wide",
        size === "sm" ? "h-[18px] px-1.5 text-[10px]" : "h-5 px-2 text-2xs",
        mono && "font-mono normal-case tracking-normal",
        STYLE[resolved],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** A badge with a leading state dot: connection, session and feed health. */
export function StatusDot({
  children,
  tone = "neutral",
  title,
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  title?: string;
  className?: string;
}) {
  const dot =
    tone === "up" || tone === "accent"
      ? "bg-accent"
      : tone === "warn"
        ? "bg-warn"
        : tone === "down" || tone === "crit"
          ? "bg-down"
          : "bg-fg-2";
  return (
    <span
      title={title}
      className={cx("inline-flex items-center gap-1.5 whitespace-nowrap text-xs text-fg-1", className)}
    >
      <span aria-hidden="true" className={cx("h-1.5 w-1.5 shrink-0 rounded-full", dot)} />
      {children}
    </span>
  );
}
