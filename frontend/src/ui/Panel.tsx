import type { ReactNode } from "react";

import { cx } from "../lib/cx";

/** The surfaces: a card is the only container in the app. `Panel` is a card with a header. */
export function Card({
  children,
  className,
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "div" | "aside";
}) {
  return <Tag className={cx("rq-card flex min-h-0 min-w-0 flex-col", className)}>{children}</Tag>;
}

export function Panel({
  title,
  description,
  actions,
  children,
  footer,
  className,
  bodyClassName,
  scrollable = false,
}: {
  title: ReactNode;
  /** One calm line under the title: what this card is for. */
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  /** A closing note under the body: definitions, caveats, sources. */
  footer?: ReactNode;
  className?: string;
  bodyClassName?: string;
  /** A fixed-height panel whose body scrolls: make it reachable by keyboard. */
  scrollable?: boolean;
}) {
  return (
    <Card className={className}>
      <header className="flex shrink-0 flex-wrap items-center justify-between gap-x-3 gap-y-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="truncate text-sm font-semibold tracking-tight text-fg-0">{title}</h2>
          {description && <p className="mt-0.5 text-xs text-fg-2">{description}</p>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </header>
      <div
        className={cx("min-h-0 flex-1", bodyClassName)}
        tabIndex={scrollable ? 0 : undefined}
        role={scrollable ? "region" : undefined}
        aria-label={scrollable && typeof title === "string" ? title : undefined}
      >
        {children}
      </div>
      {footer && (
        <footer className="shrink-0 border-t border-line px-4 py-2.5 text-2xs leading-relaxed text-fg-2">
          {footer}
        </footer>
      )}
    </Card>
  );
}

/** A calm line explaining why there is nothing to show. */
export function Empty({ children, className }: { children: ReactNode; className?: string }) {
  return <p className={cx("px-4 py-6 text-sm text-fg-2", className)}>{children}</p>;
}

/** A titled empty state: an outline glyph, one line of what is missing, one of what to do. */
export function EmptyState({
  title,
  hint,
  action,
  className,
}: {
  title: string;
  hint?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cx("flex flex-col items-center justify-center gap-2 px-6 py-12 text-center", className)}>
      <span
        aria-hidden="true"
        className="flex h-11 w-11 items-center justify-center rounded-full border border-line-strong bg-bg-2 text-fg-2"
      >
        <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
          <path d="M2.5 12.5V5.5a1 1 0 0 1 1-1h11a1 1 0 0 1 1 1v7M2.5 12.5h13M2.5 12.5l3-3.5 2.5 2 2.5-3 4 4.5" stroke="currentColor" strokeWidth="1.25" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
      </span>
      <p className="text-sm font-medium text-fg-1">{title}</p>
      {hint && <p className="max-w-md text-xs leading-relaxed text-fg-2">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

/** A load that failed: what broke, in the server's own words. */
export function ErrorState({ message, className }: { message: string; className?: string }) {
  return (
    <div
      role="alert"
      className={cx("flex items-start gap-3 rounded-md border border-down bg-down-soft px-4 py-3 text-sm text-down", className)}
    >
      <svg aria-hidden="true" width="16" height="16" viewBox="0 0 16 16" fill="none" className="mt-0.5 shrink-0">
        <path d="M8 1.5 15 14H1zM8 6v3.5M8 11.6v.9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="min-w-0 break-words">{message}</span>
    </div>
  );
}

/** A loading placeholder shaped like the content it stands in for. */
export function Skeleton({ className, rows = 3 }: { className?: string; rows?: number }) {
  return (
    <div className={cx("flex flex-col gap-2", className)} aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <span key={i} className="rq-skeleton h-3.5" style={{ width: `${92 - i * 9}%` }} />
      ))}
    </div>
  );
}

/** A row of key/value pairs inside a card (the inspector, the system screen). */
export function FieldGrid({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <dl className={cx("grid grid-cols-[repeat(auto-fill,minmax(160px,1fr))] gap-x-5 gap-y-4", className)}>
      {children}
    </dl>
  );
}

export function Field({ label, children, className }: { label: ReactNode; children: ReactNode; className?: string }) {
  return (
    <div className={cx("min-w-0", className)}>
      <dt className="rq-label">{label}</dt>
      <dd className="mt-1 truncate text-sm text-fg-0">{children}</dd>
    </div>
  );
}
