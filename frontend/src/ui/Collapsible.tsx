// A disclosure for dense detail. The summary line is always visible, so a reader can scan what a
// section holds without opening it; the body is opt-in. Used for the long tails of a screen
// (lineage, configuration, per-event evidence) that would otherwise push the summary off-screen.

import { useId, useState, type ReactNode } from "react";

import { cx } from "../lib/cx";
import { Icon } from "../shell/icons";

export function Collapsible({
  title,
  summary,
  badge,
  defaultOpen = false,
  children,
  className,
  bodyClassName,
}: {
  title: string;
  /** One calm line under the title: what is inside, or the headline figure. */
  summary?: ReactNode;
  /** A count or status pill kept visible while collapsed. */
  badge?: ReactNode;
  defaultOpen?: boolean;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  const [open, setOpen] = useState(defaultOpen);
  const id = useId();
  return (
    <section className={cx("rq-card overflow-hidden", className)}>
      <h2 className="m-0 text-sm">
        <button
          type="button"
          aria-expanded={open}
          // Only reference the panel while it exists: a dangling aria-controls points at nothing.
          aria-controls={open ? id : undefined}
          onClick={() => setOpen((value) => !value)}
          className="flex w-full items-center gap-2.5 px-4 py-3 text-left transition-colors duration-150 hover:bg-bg-2"
        >
          <Icon
            name={open ? "chevronDown" : "chevronRight"}
            size={15}
            className="shrink-0 text-fg-2"
            aria-hidden="true"
          />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-semibold tracking-tight text-fg-0">{title}</span>
            {summary && <span className="mt-0.5 block text-xs leading-relaxed text-fg-2">{summary}</span>}
          </span>
          {badge && <span className="flex shrink-0 items-center gap-2">{badge}</span>}
        </button>
      </h2>
      {open && (
        <div
          id={id}
          role="region"
          aria-label={title}
          className={cx("animate-rise-in border-t border-line", bodyClassName)}
        >
          {children}
        </div>
      )}
    </section>
  );
}
