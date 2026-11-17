import type { ReactNode } from "react";

import { cx } from "../lib/cx";

/** Every screen opens with its name and one line saying what it is for. */
export function PageHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <header className={cx("flex flex-wrap items-start justify-between gap-x-6 gap-y-3", className)}>
      <div className="min-w-0">
        <h1 className="text-xl font-semibold tracking-tight text-fg-0">{title}</h1>
        {description && <p className="mt-1 max-w-3xl text-sm leading-relaxed text-fg-2">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </header>
  );
}

/** A filter bar: labelled controls that wrap onto their own rows on narrow screens. */
export function Toolbar({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <div
      className={cx(
        "rq-card flex flex-wrap items-end gap-x-4 gap-y-3 px-4 py-3",
        className,
      )}
    >
      {children}
    </div>
  );
}

/** The standard page frame: a max width, page gutters, and a stack of sections. */
export function Page({ children, className, wide = false }: { children: ReactNode; className?: string; wide?: boolean }) {
  return (
    <div className={cx("flex flex-col gap-5 p-4 sm:p-6", wide ? "max-w-none" : "max-w-[1680px]", className)}>
      {children}
    </div>
  );
}
