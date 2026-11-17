// Small pieces shared by the screens: the session day, book pickers, filter controls and the
// two links that tie the app together (a symbol → its research page, a decision → its lineage).

import { useId, type ReactNode } from "react";
import { Link } from "react-router";

import { useSummary } from "../api/queries";
import { dateIST } from "../lib/format";
import { sessionDay } from "../shell/TopBar";
import { SelectInput, TextInput } from "../ui/Input";
import { Segmented } from "../ui/Segmented";

/** The session's IST date (the demo's is its tape's day). */
export function useSessionDay(): string {
  const { data } = useSummary();
  return data?.session?.date ?? sessionDay(data?.now);
}

export function useBookIds(): string[] {
  const { data } = useSummary();
  return data?.books.map((b) => b.book_id) ?? [];
}

/** A segmented book chooser (All · A · B · C). */
export function BookPicker({
  value,
  onChange,
  all = false,
}: {
  value: string | undefined;
  onChange: (book: string | undefined) => void;
  all?: boolean;
}) {
  const books = useBookIds();
  const options = [
    ...(all ? [{ value: "all", label: "All" }] : []),
    ...books.map((b) => ({ value: b, label: b })),
  ];
  if (!options.length) return null;
  return (
    <Segmented
      label="Book"
      size="xs"
      value={value ?? (all ? "all" : undefined)}
      onChange={(next) => onChange(next === "all" ? undefined : next)}
      options={options}
    />
  );
}

export function DecisionLink({ id, children }: { id: string | null | undefined; children?: ReactNode }) {
  if (!id) return <span className="text-fg-2">—</span>;
  return (
    <Link
      to={`/decisions/${id}`}
      className="inline-flex min-h-6 min-w-6 items-center rounded-sm font-mono text-xs text-accent-fg hover:underline"
      title={`Decision ${id}`}
    >
      {children ?? id.slice(-8)}
    </Link>
  );
}

export function SymbolLink({ symbol }: { symbol: string }) {
  return (
    <Link
      to={`/market?symbol=${encodeURIComponent(symbol)}`}
      className="inline-flex min-h-6 min-w-6 items-center rounded-sm font-medium text-fg-0 hover:text-accent-fg hover:underline"
      title={`Research ${symbol}`}
    >
      {symbol}
    </Link>
  );
}

// -- filter controls ------------------------------------------------------------------------------

function FilterShell({
  label,
  htmlFor,
  children,
  className,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <div className={`flex min-w-0 flex-col gap-1.5 ${className ?? ""}`}>
      <label htmlFor={htmlFor} className="rq-label">
        {label}
      </label>
      {children}
    </div>
  );
}

/** A labelled select for filter bars. */
export function Select({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: string;
  options: { value: string; label: string }[];
  onChange: (value: string) => void;
  className?: string;
}) {
  const id = useId();
  return (
    <FilterShell label={label} htmlFor={id} className={className}>
      <SelectInput id={id} value={value} onChange={(e) => onChange(e.target.value)} className="h-9 min-w-[9rem]">
        {options.map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </SelectInput>
    </FilterShell>
  );
}

/** A date picker that also prints the date unambiguously (the native control follows the
 *  operating system's locale: 05/10 could be either month). */
export function DateFilter({ value, onChange }: { value: string; onChange: (value: string | null) => void }) {
  const id = useId();
  return (
    <FilterShell label="Date" htmlFor={id}>
      <div className="flex items-center gap-2">
        <TextInput id={id} type="date" value={value} onChange={(e) => onChange(e.target.value || null)} className="h-9" />
        <span className="whitespace-nowrap text-xs text-fg-2">{dateIST(value)} IST</span>
      </div>
    </FilterShell>
  );
}

export function TextFilter({
  label,
  value,
  onChange,
  placeholder,
  width = "w-36",
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: string;
}) {
  const id = useId();
  return (
    <FilterShell label={label} htmlFor={id} className={width}>
      <TextInput
        id={id}
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
        className="h-9"
      />
    </FilterShell>
  );
}

/** Human labels for dispositions (what a book did with a signal). */
export const DISPOSITION: Record<string, string> = {
  submitted: "✓",
  vetoed: "VETO",
  risk_rejected: "risk ✕",
  broker_rejected: "broker ✕",
  regime_gated: "regime",
  policy_skipped: "skipped",
  shadow_strategy: "shadow",
  unknown: "unknown",
};
