// 1. Command Center: the session, the books, the risk headroom, open positions, today's decisions
// and the alerts. Every number is a server projection - the UI never adds money up - and rows open
// the Decision Inspector.
//
// Hierarchy: the summary tiles and the session track are always visible, because they answer
// "is anything happening and is anything wrong". The long lists (decisions, alerts) are cards that
// say what they hold before you open them, and alerts open themselves when something is warning.

import { useQuery } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { useNavigate } from "react-router";

import { get, type Schemas } from "../api/client";
import { keys, useSummary } from "../api/queries";
import { cx } from "../lib/cx";
import { hhmmIST, inrCompact, num, ratio } from "../lib/format";
import { useUi } from "../lib/ui";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Collapsible } from "../ui/Collapsible";
import { DataTable, type Columns } from "../ui/DataTable";
import { Meter } from "../ui/Meter";
import { Money, Pct, Price, Qty, TimeIST } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Panel } from "../ui/Panel";
import { QueryGate } from "../ui/QueryState";
import { StatCard } from "../ui/Stat";
import { BookPicker, DISPOSITION, SymbolLink, useSessionDay } from "./common";

type Book = Schemas["BookSummary"];
type Comparison = Schemas["BookComparison"];
type Position = Schemas["PositionRow"];
type Usage = Schemas["Utilisation"];
type DecisionRow = Schemas["DecisionRow"];

// -- session -----------------------------------------------------------------------------------

const LABEL: Record<string, string> = {
  PRE_OPEN: "Pre-open",
  OPEN: "Open",
  ENTRY_WINDOW: "Entry",
  MONITOR: "Monitor",
  CLOSE: "Close",
  REPORT: "Report",
  EXIT: "Exit",
};

function SessionTrack() {
  const { data: summary } = useSummary();
  const current = summary?.session?.state;
  const steps = summary?.schedule ?? [];
  if (!steps.length) {
    return (
      <p className="px-4 py-3 text-sm text-fg-2">
        No session today (a holiday or a weekend). The engine still serves the last recorded state,
        so the books and positions below are the most recent ones on record.
      </p>
    );
  }
  const entry = steps.findIndex((s) => s.state === "ENTRY_WINDOW");
  const currentIndex = steps.findIndex((s) => s.state === current);
  return (
    <ol aria-label="Session" className="flex items-stretch gap-1 overflow-x-auto px-4 py-3">
      {steps.map((s, i) => {
        const active = s.state === current;
        const done = currentIndex >= 0 && i < currentIndex;
        return (
          <li key={s.state} className="flex min-w-0 flex-1 items-center gap-1">
            <div
              aria-current={active ? "step" : undefined}
              className={cx(
                "flex min-w-0 flex-1 flex-col gap-1 rounded-md border px-3 py-2",
                active ? "border-accent-line bg-accent-soft" : done ? "border-line bg-bg-2" : "border-line",
              )}
            >
              <span className="flex items-center gap-1.5">
                <span
                  aria-hidden="true"
                  className={cx("h-1.5 w-1.5 shrink-0 rounded-full", active ? "bg-accent" : done ? "bg-fg-2" : "bg-bg-3")}
                />
                <span className={cx("truncate text-xs font-medium", active ? "text-accent-fg" : done ? "text-fg-1" : "text-fg-2")}>
                  {LABEL[s.state] ?? s.state}
                </span>
              </span>
              <span className={cx("num pl-3 text-2xs", active ? "text-accent-fg" : "text-fg-2")}>
                {hhmmIST(s.at)}
                {i === entry && steps[i + 1] && `–${hhmmIST(steps[i + 1]!.at)}`}
              </span>
            </div>
          </li>
        );
      })}
    </ol>
  );
}

// -- books -----------------------------------------------------------------------------------

interface BookLine extends Book {
  comparison?: Comparison;
}

const bookCol = createColumnHelper<BookLine>();
const BOOK_COLUMNS: Columns<BookLine> = [
  bookCol.accessor("book_id", { header: "Book", size: 68, cell: (c) => <span className="font-semibold">{c.getValue()}</span> }),
  bookCol.accessor("advisor", { header: "Advisor", size: 104, cell: (c) => <span className="text-fg-1">{c.getValue()}</span> }),
  bookCol.accessor("equity", { header: "Equity", size: 132, meta: { align: "right" }, cell: (c) => <Money value={c.getValue()} /> }),
  bookCol.accessor("day_pnl", { header: "Day P&L", size: 118, meta: { align: "right" }, cell: (c) => <Money value={c.getValue()} pnl /> }),
  bookCol.accessor("day_return_pct", { header: "Day %", size: 84, meta: { align: "right" }, cell: (c) => <Pct value={c.getValue()} pnl /> }),
  bookCol.accessor((r) => r.comparison?.return_pct ?? null, { id: "return", header: "Return", size: 88, meta: { align: "right" }, cell: (c) => <Pct value={c.getValue()} pnl /> }),
  bookCol.accessor("realized_pnl_today", { header: "Realised today", size: 132, meta: { align: "right" }, cell: (c) => <Money value={c.getValue()} pnl /> }),
  bookCol.accessor("trades_today", { header: "Trades", size: 76, meta: { align: "right" }, cell: (c) => <Qty value={c.getValue()} /> }),
  bookCol.accessor((r) => r.comparison?.net_ai_value_inr ?? null, {
    id: "net_ai",
    header: "Net AI value",
    size: 128,
    meta: { align: "right" },
    cell: (c) => (c.getValue() === null ? <span className="text-fg-2">baseline</span> : <Money value={c.getValue()} pnl />),
  }),
  bookCol.accessor("kill_switch", {
    header: "Kill switch",
    size: 116,
    cell: (c) => <Badge tone={c.getValue() === "ARMED" ? "neutral" : undefined}>{c.getValue() === "HALT_NEW" ? "HALTED" : c.getValue()}</Badge>,
  }),
];

function BooksCard() {
  const { data: summary } = useSummary();
  const books = useQuery({ queryKey: keys.books, queryFn: ({ signal }) => get("/api/books", { signal }), refetchInterval: 15_000 });
  const rows = useMemo<BookLine[]>(
    () => (summary?.books ?? []).map((b) => ({ ...b, comparison: books.data?.books.find((c) => c.book_id === b.book_id) })),
    [summary, books.data],
  );
  const valuation = summary?.books[0]?.valuation;
  return (
    <Panel
      title="Books"
      description={valuation === "live" ? "Valued live, this tick" : valuation === "last_mark" ? "Valued from the last mark" : "No valuation yet"}
      bodyClassName="overflow-x-auto"
    >
      <QueryGate
        isLoading={books.isLoading}
        isError={books.isError}
        error={books.error}
        data={rows}
        isEmpty={(r) => r.length === 0}
        emptyTitle="No books configured"
        emptyHint="Books come from src/config/experiment.yaml. Run a paper session and they appear here."
        className="p-4"
        skeletonRows={3}
      >
        {(data) => (
          <DataTable
            label="Books"
            data={data}
            columns={BOOK_COLUMNS}
            getRowId={(r) => r.book_id}
            empty="No books configured."
          />
        )}
      </QueryGate>
    </Panel>
  );
}

// -- risk strip --------------------------------------------------------------------------------

export function usageDetail(u: Usage): string {
  if (u.unit === "inr") return `${inrCompact(u.used)} / ${inrCompact(u.limit)}`;
  if (u.unit === "ratio") return `${ratio(u.used)} / ${ratio(u.limit)}`;
  return `${num(u.used, 0)} / ${num(u.limit, 0)}`;
}

function RiskCard() {
  const [book, setBook] = useState<string | undefined>("A");
  const setHalt = useUi((s) => s.setHalt);
  const navigate = useNavigate();
  const risk = useQuery({
    queryKey: keys.risk(book),
    queryFn: ({ signal }) => get("/api/risk", { query: { book }, signal }),
    refetchInterval: 10_000,
  });
  const view = risk.data?.books[0];
  return (
    <Panel
      title="Risk headroom"
      description="How much of each limit is used, by the RiskEngine's own definition"
      actions={<BookPicker value={book} onChange={setBook} />}
    >
      <QueryGate
        isLoading={risk.isLoading}
        isError={risk.isError}
        error={risk.error}
        data={view}
        emptyTitle="No risk view for this book"
        emptyHint="The risk view appears once the engine has recorded a session. Run a paper session or replay a recorded day."
        className="p-4"
        skeletonRows={5}
      >
        {(bookView) => {
          const bookGlobal = bookView.kill_switches.find((k) => k.scope === "global");
          const bookTripped = !!bookGlobal && bookGlobal.state !== "ARMED";
          return (
            <div className="flex flex-col gap-4">
              <div className="flex flex-col gap-3">
                {bookView.utilisation.map((u) => (
                  <Meter key={u.key} label={u.label} fraction={u.fraction} detail={usageDetail(u)} />
                ))}
              </div>
              <div
                className={cx(
                  "flex flex-wrap items-center gap-2 rounded-md border px-3 py-2.5",
                  bookTripped ? "border-warn bg-warn-soft" : "border-line bg-bg-2",
                )}
              >
                <Icon name="risk" size={16} className={bookTripped ? "text-warn" : "text-fg-2"} />
                <span className="text-sm text-fg-1">Kill switch</span>
                <Badge tone={!bookGlobal || bookGlobal.state === "ARMED" ? "neutral" : undefined}>
                  {bookGlobal ? (bookGlobal.state === "HALT_NEW" ? "HALTED" : bookGlobal.state) : "ARMED"}
                </Badge>
                {bookGlobal && bookGlobal.state !== "ARMED" && (
                  <span className="min-w-0 flex-1 truncate text-xs text-fg-2">{bookGlobal.reason}</span>
                )}
                <span className="ml-auto flex items-center gap-2">
                  <Button size="xs" variant="danger" onClick={() => setHalt(true)}>
                    HALT
                  </Button>
                  <Button size="xs" variant="ghost" onClick={() => navigate("/risk")}>
                    Risk centre
                  </Button>
                </span>
              </div>
              <p className="text-2xs text-fg-2">
                {bookView.valuation === "live"
                  ? "Live: 100% is where the check blocks an order."
                  : "From the stored state and the last mark - heat and sector limits need a running session."}
              </p>
            </div>
          );
        }}
      </QueryGate>
    </Panel>
  );
}

// -- positions ---------------------------------------------------------------------------------

const posCol = createColumnHelper<Position>();
const POSITION_COLUMNS: Columns<Position> = [
  posCol.accessor("book_id", { header: "Book", size: 68 }),
  posCol.accessor("symbol", { header: "Symbol", size: 112, cell: (c) => <SymbolLink symbol={c.getValue()} /> }),
  posCol.accessor("quantity", { header: "Qty", size: 80, meta: { align: "right" }, cell: (c) => <Qty value={c.getValue()} /> }),
  posCol.accessor("avg_price", { header: "Average", size: 104, meta: { align: "right" }, cell: (c) => <Price value={c.getValue()} /> }),
  posCol.accessor("mark", { header: "LTP", size: 104, meta: { align: "right" }, cell: (c) => <Price value={c.getValue()} /> }),
  posCol.accessor("unrealized_pnl", { header: "Unrealised P&L", size: 138, meta: { align: "right" }, cell: (c) => <Money value={c.getValue()} pnl arrow /> }),
  posCol.accessor("stop_price", { header: "Stop", size: 100, meta: { align: "right" }, cell: (c) => <Price value={c.getValue()} /> }),
  posCol.accessor("target_price", { header: "Target", size: 100, meta: { align: "right" }, cell: (c) => <Price value={c.getValue()} /> }),
  posCol.accessor("held_sessions", { header: "Days held", size: 96, meta: { align: "right" }, cell: (c) => <Qty value={c.getValue()} /> }),
  posCol.accessor("strategy", { header: "Strategy", size: 132, cell: (c) => <span className="text-fg-1">{c.getValue() ?? "—"}</span> }),
];

function PositionsCard() {
  const [book, setBook] = useState<string | undefined>(undefined);
  const navigate = useNavigate();
  const { data: summary } = useSummary();
  const positions = useOpenPositions();
  return (
    <Panel
      title="Open positions"
      description={summary?.market_open ? "Marked from the freshest quote" : "Market closed: marked from the last quote"}
      actions={<BookPicker value={book} onChange={setBook} all />}
      bodyClassName="overflow-x-auto"
    >
      <DataTable
        label="Open positions"
        data={positions.data}
        columns={POSITION_COLUMNS}
        error={positions.error}
        loading={positions.isLoading}
        getRowId={(r) => `${r.book_id}|${r.instrument_key}|${r.product}`}
        onRowClick={(r) => r.entry_decision_id && navigate(`/decisions/${r.entry_decision_id}`)}
        rowLabel={(r) => `${r.book_id} ${r.symbol}: open the entry decision`}
        empty={
          summary?.market_open
            ? "No open positions. Entries are proposed once per day, inside the 09:20-09:45 entry window."
            : "No open positions, and the market is closed. Positions are CNC, so any held position would still be listed here."
        }
      />
    </Panel>
  );
}

// -- today's decisions ----------------------------------------------------------------------------

interface SignalLine {
  signal_id: string;
  decision_id: string;
  ts: string;
  symbol: string;
  strategy: string;
  books: DecisionRow[];
}

function group(rows: DecisionRow[]): SignalLine[] {
  const lines = new Map<string, SignalLine>();
  for (const r of rows) {
    if (r.disposition === "shadow_strategy") continue;
    const line = lines.get(r.signal_id) ?? { signal_id: r.signal_id, decision_id: r.decision_id, ts: r.ts, symbol: r.symbol, strategy: r.strategy, books: [] };
    line.books.push(r);
    lines.set(r.signal_id, line);
  }
  return [...lines.values()].map((l) => ({ ...l, books: [...l.books].sort((a, b) => a.book_id.localeCompare(b.book_id)) }));
}

function TodaysDecisions() {
  const navigate = useNavigate();
  const decisions = useTodaySignals();
  const lines = useMemo(() => group(decisions.data ?? []), [decisions.data]);
  return (
    <Panel
      title="Today's decisions"
      description={`${lines.length} signals, grouped; open one for its full lineage`}
      actions={
        <Button size="xs" variant="ghost" onClick={() => navigate("/decisions")}>
          All decisions
          <Icon name="chevronRight" size={13} />
        </Button>
      }
      bodyClassName="max-h-[420px] overflow-auto"
      scrollable
    >
      <QueryGate
        isLoading={decisions.isLoading}
        isError={decisions.isError}
        error={decisions.error}
        data={lines}
        isEmpty={(l) => l.length === 0}
        emptyTitle="No signals today"
        emptyHint="The engine runs one decision cycle per trading day, inside the 09:20-09:45 entry window. On a holiday or weekend there are none."
        className="px-4 py-4"
        skeletonRows={4}
      >
        {(rows) => (
          <ul className="divide-y divide-line">
            {rows.map((l) => (
              <li key={l.signal_id}>
                <button
                  type="button"
                  onClick={() => navigate(`/decisions/${l.decision_id}`)}
                  className="flex w-full flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-2.5 text-left transition-colors duration-150 hover:bg-bg-2"
                >
                  <span className="num w-12 shrink-0 text-xs text-fg-2">{hhmmIST(l.ts)}</span>
                  <span className="w-24 shrink-0 truncate text-sm font-medium text-fg-0">{l.symbol}</span>
                  <span className="w-32 shrink-0 truncate text-xs text-fg-1">{l.strategy}</span>
                  <span className="flex min-w-0 flex-1 flex-wrap gap-1">
                    {l.books.map((b) => (
                      <Badge key={b.book_id} size="sm" tone={b.disposition === "submitted" ? "neutral" : undefined} title={b.detail || b.disposition}>
                        {`${b.book_id} ${DISPOSITION[b.disposition] ?? b.disposition}`}
                      </Badge>
                    ))}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </QueryGate>
    </Panel>
  );
}

// -- alerts ------------------------------------------------------------------------------------

/** Queries the summary tiles and the cards share: identical keys, so React Query serves both
 *  from one request rather than two. */
function useAlerts() {
  return useQuery({
    queryKey: ["alerts", { limit: 100 }],
    queryFn: ({ signal }) => get("/api/alerts", { query: { limit: 100 }, signal }),
    refetchInterval: 15_000,
  });
}

function useOpenPositions() {
  return useQuery({
    queryKey: keys.positions(undefined),
    queryFn: ({ signal }) => get("/api/positions", { query: { book: undefined }, signal }),
    refetchInterval: 10_000,
  });
}

function useTodaySignals() {
  const day = useSessionDay();
  const query = { date: day, limit: 1000 };
  return useQuery({
    queryKey: keys.decisions(query),
    queryFn: ({ signal }) => get("/api/decisions", { query, signal }),
    refetchInterval: 15_000,
  });
}

function AlertsCard({ warningCount }: { warningCount: number }) {
  const alerts = useAlerts();
  const rows = alerts.data ?? [];
  const worst = rows.find((a) => a.level === "CRITICAL")?.level ?? (warningCount > 0 ? "WARNING" : null);
  return (
    <Collapsible
      title="Alerts"
      summary="Newest first, from the engine's own alert stream (the last 100)"
      // A warning opens itself: an alert the operator has to discover is not an alert.
      defaultOpen={warningCount > 0}
      badge={
        rows.length === 0 ? (
          <Badge tone="neutral">quiet</Badge>
        ) : (
          <Badge tone={worst === "CRITICAL" ? "crit" : warningCount > 0 ? "warn" : "neutral"}>
            {warningCount > 0 ? `${warningCount} needing attention` : `${rows.length} info`}
          </Badge>
        )
      }
      bodyClassName="max-h-[420px] overflow-auto"
    >
      <QueryGate
        isLoading={alerts.isLoading}
        isError={alerts.isError}
        error={alerts.error}
        data={rows}
        isEmpty={(r) => r.length === 0}
        emptyTitle="No alerts"
        emptyHint="Nothing has been flagged. Feed outages, stale quotes, risk breaches and kill-switch changes all arrive here."
        className="px-4 py-4"
        skeletonRows={3}
      >
        {(items) => (
          <ul className="divide-y divide-line">
            {items.map((a) => (
              <li key={a.seq} className="flex flex-wrap items-start gap-x-3 gap-y-1 px-4 py-2.5">
                <TimeIST value={a.ts} className="w-16 shrink-0 text-xs text-fg-2" />
                <Badge size="sm" tone={a.level === "INFO" ? "neutral" : a.level === "CRITICAL" ? "crit" : "warn"}>
                  {a.level}
                </Badge>
                <span className="w-44 shrink-0 truncate font-mono text-2xs text-fg-1" title={a.key}>
                  {a.key}
                </span>
                <span className="min-w-0 flex-1 break-words text-sm text-fg-0">{a.message}</span>
              </li>
            ))}
          </ul>
        )}
      </QueryGate>
    </Collapsible>
  );
}

// -- the screen ----------------------------------------------------------------------------------

export default function CommandCenter() {
  const { data: summary } = useSummary();
  const day = useSessionDay();
  const positions = useOpenPositions();
  const alerts = useAlerts();
  const signals = useTodaySignals();
  const signalCount = useMemo(() => group(signals.data ?? []).length, [signals.data]);
  const alertCount = (alerts.data ?? []).filter((a) => a.level !== "INFO").length;
  const valuation = summary?.books[0]?.valuation;

  return (
    <Page>
      <PageHeader
        title="Command"
        description="The whole book at a glance: where the session is, how each paired book stands, what is open, and what the engine decided and flagged today."
        actions={
          <span className="flex items-center gap-2 rounded-full border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-1">
            <Icon name="calendar" size={14} className="text-fg-2" />
            <span className="num">{day}</span>
            <span className="text-fg-2">IST</span>
          </span>
        }
      />

      {valuation === "last_mark" && (
        <p className="flex items-start gap-2 rounded-md border border-warn bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">
          <Icon name="clock" size={14} className="mt-0.5 shrink-0" />
          <span>
            Valuations come from the last recorded mark, not a live quote. Start a session to see
            live figures; the numbers below are still the most recent ones on record.
          </span>
        </p>
      )}

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Session"
          size="sm"
          value={summary?.session?.state ?? "NO SESSION"}
          hint={summary?.market_open ? "market open" : "market closed"}
        />
        <StatCard
          label="Open positions"
          size="sm"
          value={<Qty value={(positions.data ?? []).length} />}
          hint="across every book"
        />
        <StatCard
          label="Signals today"
          size="sm"
          value={<Qty value={signalCount} />}
          hint="each judged per book"
        />
        <StatCard
          label="Alerts"
          size="sm"
          value={
            <span className={alertCount > 0 ? "text-warn" : undefined}>
              <Qty value={alertCount} />
            </span>
          }
          hint={alertCount > 0 ? "warning or worse, of the last 100" : "nothing to attend to"}
        />
      </div>

      <Panel title="Session" description="The lifecycle the engine walks each trading day, in IST" bodyClassName="min-w-0">
        <SessionTrack />
      </Panel>

      <BooksCard />

      <div className="grid min-w-0 items-start gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
        <RiskCard />
        <TodaysDecisions />
      </div>

      <PositionsCard />

      <AlertsCard warningCount={alertCount} />
    </Page>
  );
}
