// The universe as a research list: search, sort, and one tap to make a symbol the subject of the
// page. Every figure is the server's quote (or the fresher one from the event stream).

import { useMemo, useState } from "react";

import type { Schemas } from "../../api/client";
import { cx } from "../../lib/cx";
import { duration } from "../../lib/format";
import { Icon } from "../../shell/icons";
import { Badge } from "../../ui/Badge";
import { EmptyState, ErrorState, Panel, Skeleton } from "../../ui/Panel";
import { describeError } from "../../ui/QueryState";
import { Pct, Price } from "../../ui/Num";
import { Segmented } from "../../ui/Segmented";

type Watch = Schemas["WatchRow"];
type Sort = "symbol" | "change";

export function Watchlist({
  rows,
  selected,
  onSelect,
  loading,
  error,
}: {
  rows: Watch[];
  selected: string;
  onSelect: (symbol: string) => void;
  loading?: boolean;
  error?: unknown;
}) {
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<Sort>("symbol");
  const text = query.trim().toLowerCase();

  const shown = useMemo(() => {
    const filtered = text
      ? rows.filter((r) => r.symbol.toLowerCase().includes(text) || (r.sector ?? "").toLowerCase().includes(text))
      : rows;
    return [...filtered].sort((a, b) =>
      sort === "symbol"
        ? a.symbol.localeCompare(b.symbol)
        : (b.change_pct ?? -Infinity) - (a.change_pct ?? -Infinity),
    );
  }, [rows, text, sort]);

  return (
    <Panel
      title="Watchlist"
      description={`${rows.length} instruments in the universe`}
      actions={
        <Segmented
          label="Sort watchlist"
          size="xs"
          value={sort}
          onChange={setSort}
          options={[
            { value: "symbol", label: "A-Z" },
            { value: "change", label: "Change" },
          ]}
        />
      }
      bodyClassName="flex flex-col"
    >
      <div className="shrink-0 border-b border-line p-3">
        <div className="relative">
          <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-2" />
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Filter by symbol or sector"
            aria-label="Filter the watchlist"
            className="rq-field pl-9"
          />
        </div>
      </div>

      {error ? (
        <div className="p-3">
          <ErrorState message={`Could not load the watchlist: ${describeError(error)}`} />
        </div>
      ) : loading && rows.length === 0 ? (
        <div className="p-4">
          <Skeleton rows={7} />
        </div>
      ) : shown.length === 0 ? (
        <EmptyState
          title={rows.length === 0 ? "No quotes yet" : `Nothing matches “${query}”`}
          hint={
            rows.length === 0
              ? "The universe appears with the first poll of a session, or from the newest tape."
              : "Clear the filter to see the whole universe again."
          }
        />
      ) : (
        <ul className="max-h-[560px] min-h-0 flex-1 overflow-auto p-1.5" aria-label="Watchlist">
          {shown.map((r) => {
            const active = r.symbol === selected;
            return (
              <li key={r.instrument_key}>
                <button
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => onSelect(r.symbol)}
                  className={cx(
                    "relative flex w-full items-center gap-3 rounded-md px-3 py-2.5 text-left transition-colors duration-150",
                    active ? "bg-accent-soft" : "hover:bg-bg-2",
                  )}
                >
                  {active && <span aria-hidden="true" className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-accent" />}
                  <span className="min-w-0 flex-1">
                    <span className={cx("block truncate text-sm font-semibold", active ? "text-accent-fg" : "text-fg-0")}>
                      {r.symbol}
                    </span>
                    <span className="mt-0.5 flex items-center gap-1.5 truncate text-2xs text-fg-2">
                      {r.sector ?? "—"}
                      {r.held && <Badge size="sm" tone="accent">held</Badge>}
                      {r.events.length > 0 && <Badge size="sm" tone="warn">event</Badge>}
                    </span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="num block text-sm text-fg-0">
                      <Price value={r.ltp} />
                    </span>
                    <span className="mt-0.5 block text-2xs">
                      {r.change_pct === null || r.change_pct === undefined ? (
                        <span className="text-fg-2">no change data</span>
                      ) : (
                        <Pct value={r.change_pct} pnl />
                      )}
                    </span>
                  </span>
                  <span className="num w-12 shrink-0 text-right text-2xs text-fg-2" title="Age of the last quote">
                    {r.quote_age_s === null || r.quote_age_s === undefined ? "—" : duration(r.quote_age_s)}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}
