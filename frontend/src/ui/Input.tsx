import { useId, type InputHTMLAttributes, type ReactNode, type SelectHTMLAttributes } from "react";

import { cx } from "../lib/cx";

/** A labelled control: the label is always bound to the field, so a screen reader gets it too. */
export function Field({
  label,
  hint,
  children,
  className,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <label htmlFor={id} className="rq-label">
        {label}
      </label>
      {children(id)}
      {hint && <p className="text-2xs text-fg-2">{hint}</p>}
    </div>
  );
}

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx("rq-field", className)} {...rest} />;
}

export function SelectInput({
  className,
  children,
  ...rest
}: SelectHTMLAttributes<HTMLSelectElement> & { children: ReactNode }) {
  return (
    <div className="relative">
      <select className={cx("rq-field appearance-none pr-8", className)} {...rest}>
        {children}
      </select>
      <svg
        aria-hidden="true"
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="none"
        className="pointer-events-none absolute right-3 top-1/2 -translate-y-1/2 text-fg-2"
      >
        <path d="M2.5 4.5 6 8l3.5-3.5" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    </div>
  );
}

/** An inline labelled control for filter bars, where a stacked label would be too tall. */
export function InlineField({
  label,
  children,
  className,
}: {
  label: ReactNode;
  children: (id: string) => ReactNode;
  className?: string;
}) {
  const id = useId();
  return (
    <div className={cx("flex min-w-0 flex-col gap-1.5", className)}>
      <label htmlFor={id} className="rq-label">
        {label}
      </label>
      {children(id)}
    </div>
  );
}
