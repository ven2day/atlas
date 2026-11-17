import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "../lib/cx";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "soft" | "danger" | "dangerSoft" | "success";
export type ButtonSize = "xs" | "sm" | "md" | "lg";

// Opacity modifiers are not used: the colours are CSS variables, so each state has its own
// `-soft` token instead of `bg-x/20`.
const VARIANT: Record<ButtonVariant, string> = {
  primary: "border-accent bg-accent text-bg-0 hover:border-accent-hover hover:bg-accent-hover",
  secondary: "border-line-strong bg-bg-2 text-fg-0 hover:border-fg-2 hover:bg-bg-3",
  ghost: "border-transparent bg-transparent text-fg-1 hover:bg-bg-2 hover:text-fg-0",
  soft: "border-accent-line bg-accent-soft text-accent-fg hover:border-accent hover:bg-bg-2",
  danger: "border-down bg-down text-bg-0 hover:brightness-110",
  dangerSoft: "border-transparent bg-down-soft text-down hover:bg-bg-2",
  success: "border-transparent bg-up-soft text-up hover:bg-bg-2",
};

const SIZE: Record<ButtonSize, string> = {
  xs: "h-7 gap-1.5 rounded-md px-2.5 text-xs",
  sm: "h-8 gap-1.5 rounded-md px-3 text-sm",
  md: "h-9 gap-2 rounded-md px-4 text-sm",
  lg: "h-11 gap-2 rounded-lg px-5 text-base",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Fills the width of its container (toolbars on small screens). */
  block?: boolean;
  children: ReactNode;
}

/** The one button. `variant` carries the intent, `size` the density of its surroundings. */
export function Button({
  variant = "secondary",
  size = "sm",
  block = false,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      className={cx(
        "inline-flex shrink-0 select-none items-center justify-center whitespace-nowrap border font-medium",
        "transition-colors duration-150",
        "disabled:cursor-not-allowed disabled:opacity-45 disabled:hover:brightness-100",
        VARIANT[variant],
        SIZE[size],
        block && "w-full",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}

/** A text-only control for card headers and dense toolbars. */
export function LinkButton({
  children,
  className,
  tone = "muted",
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { tone?: "muted" | "accent" | "danger"; children: ReactNode }) {
  return (
    <button
      type="button"
      className={cx(
        "inline-flex items-center gap-1 rounded-md px-1.5 py-1 text-xs font-medium transition-colors duration-150",
        tone === "muted" && "text-fg-2 hover:bg-bg-2 hover:text-fg-0",
        tone === "accent" && "text-accent-fg hover:bg-accent-soft",
        tone === "danger" && "text-down hover:bg-down-soft",
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
