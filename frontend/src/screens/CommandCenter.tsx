// 1. Command Center: what is happening right now, then the session, the books, the risk headroom,
// open positions, today's decisions and the alerts. Every number is a server projection - the UI
// never adds money up - and rows open the Decision Inspector.
//
// Hierarchy: the "Right now" panel answers a trader's first three questions (is the system
// running, is the data fresh, does anything need me) and holds the session start/stop controls.
// The summary tiles and the session track stay always visible; the long lists (decisions,
// alerts) are cards that say what they hold before you open them, and alerts open themselves
// when something is warning. The plain-language trading guide lives at the bottom, collapsed.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router";

import { get, post, type Schemas } from "../api/client";
import { keys, useConfig, useSummary, useSystem } from "../api/queries";
import { BOOKS, SESSION_STATES, STRATEGIES, dataFreshness } from "../lib/explain";
import { cx } from "../lib/cx";
import { duration, hhmmIST, inrCompact, num, ratio } from "../lib/format";
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
import { useToasts } from "../ui/Toast";
import { BookPicker, DISPOSITION, SymbolLink, useSessionDay } from "./common";

type Book = Schemas["BookSummary"];
type Comparison = Schemas["BookComparison"];
type Position = Schemas["PositionRow"];
type Usage = Schemas["Utilisation"];
type DecisionRow = Schemas["DecisionRow"];

// -- right now: session control, data freshness, attention ----------------------------------------

/** Start/stop the paper session through the server's own controls. Read-only consoles refuse;
 *  a start while one is running is a 409 the server names, and both are surfaced, not swallowed. */
function SessionControl() {
  const { data: summary } = useSummary();
  const { data: config } = useConfig();
  const client = useQueryClient();
  const push = useToasts((s) => s.push);
  const [busy, setBusy] = useState<string | null>(null);
  const running = summary?.running ?? false;
  const readOnly = config?.read_only ?? false;

  const act = async (action: "paper" | "demo" | "stop") => {
    setBusy(action);
    try {
      const result =
        action === "stop"
          ? await post("/api/session/stop", {})
          : await post("/api/session/start", { demo: action === "demo" });
      push({
        level: "info",
        title: `Session ${action === "stop" ? "stop" : "start"} ${result.outcome.replace("_", " ")}`,
        body: result.detail,
      });
      void client.invalidateQueries();
    } catch (e) {
      push({
        level: "warn",
        title: "Session control refused",
        body: e instanceof Error ? e.message : "the request failed",
      });
    } finally {
      setBusy(null);
    }
  };

  if (readOnly) {
    return <span className="text-2xs text-fg-2">This console is read-only: session control is disabled.</span>;
  }
  return (
    <span className="flex flex-wrap items-center gap-2">
      {running ? (
        <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => void act("stop")}
                title="Cooperative: the engine finishes the step it is in, then stops">
          <Icon name="stop" size={14} />
          {busy === "stop" ? "Stopping…" : "Stop session"}
        </Button>
      ) : (
        <>
          <Button size="sm" variant="primary" disabled={busy !== null} onClick={() => void act("paper")}
                  title="Start a paper session: real market data, simulated broker, no real orders">
            <Icon name="play" size={14} />
            {busy === "paper" ? "Starting…" : "Start paper session"}
          </Button>
          <Button size="sm" variant="secondary" disabled={busy !== null} onClick={() => void act("demo")}
                  title="Replay the bundled recorded day through the real engine - no market connection">
            {busy === "demo" ? "Starting…" : "Start demo replay"}
          </Button>
        </>
      )}
    </span>
  );
}

function RightNow({ warningCount }: { warningCount: number }) {
  const { data: summary } = useSummary();
  const { data: system } = useSystem();
  const ages = Object.entries(system?.quote_age_s ?? {}).sort((a, b) => a[1] - b[1]);
  const freshest = ages[0];
  const freshness = dataFreshness(freshest ? freshest[1] : null);
  const state = summary?.session?.state ?? null;
  const explained = state ? SESSION_STATES[state] : null;
  const schedule = summary?.schedule ?? [];
  const currentIndex = schedule.findIndex((s) => s.state === state);
  const next = currentIndex >= 0 ? schedule[currentIndex + 1] : undefined;
  const drift = (system?.last_reconcile ?? []).some((r) => !r.in_sync);
  const halted = (summary?.books ?? []).filter((b) => b.kill_switch !== "ARMED");
  const running = summary?.running ?? false;

  const attention: { text: string; to?: string; link?: string }[] = [];
  if (!running) {
    attention.push({ text: "No session is running - quotes, signals and risk state stay frozen at the last record until one is started." });
  }
  if (freshness.state === "stale") {
    attention.push({ text: `Market data is stale (${freshest ? duration(freshest[1]) : "—"} old): new entries are blocked until quotes are fresh again.`, to: "/system", link: "System health" });
  } else if (freshness.state === "none" && running) {
    attention.push({ text: "The session is running but no quote has arrived yet - waiting for the first poll.", to: "/system", link: "System health" });
  }
  for (const b of halted) {
    attention.push({ text: `Book ${b.book_id}'s kill switch is ${b.kill_switch === "HALT_NEW" ? "halted (new entries blocked)" : b.kill_switch}.`, to: "/risk", link: "Risk centre" });
  }
  if (drift) {
    attention.push({ text: "The OMS and the broker disagree (reconciliation drift) - entries stay blocked until it clears.", to: "/system", link: "System health" });
  }
  if (warningCount > 0) {
    attention.push({ text: `${warningCount} warning(s) or worse in the alert stream - see Alerts below.` });
  }

  return (
    <Panel
      title="Right now"
      description="Is the system running, is its data fresh, and does anything need you"
      actions={<SessionControl />}
      bodyClassName="flex flex-col gap-3 p-4"
    >
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Badge tone={running ? "accent" : "neutral"}>{running ? "SESSION RUNNING" : "IDLE"}</Badge>
        <span className="text-sm text-fg-0">
          {explained ? `${explained.label}: ` : "No session: "}
          <span className="text-fg-1">{explained ? explained.plain : "the engine serves the last recorded state; start a session to trade."}</span>
        </span>
        {next && (
          <span className="text-xs text-fg-2">
            Next: {SESSION_STATES[next.state]?.label ?? next.state} at <span className="num">{hhmmIST(next.at)}</span> IST
          </span>
        )}
        <span
          className={cx(
            "ml-auto flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-2xs font-medium",
            freshness.state === "live" && "border-accent-line bg-accent-soft text-accent-fg",
            freshness.state === "delayed" && "border-line-strong bg-bg-2 text-fg-1",
            freshness.state === "stale" && "border-warn bg-warn-soft text-warn",
            freshness.state === "none" && "border-line bg-bg-2 text-fg-2",
          )}
          title={`${freshness.detail}${freshest ? ` Source: ${freshest[0]}, age ${duration(freshest[1])}.` : ""}`}
        >
          <Icon name="database" size={13} />
          Data {freshness.label}
          {freshest && freshness.state !== "none" && <span className="num">· {duration(freshest[1])} old</span>}
        </span>
      </div>
      {attention.length > 0 ? (
        <ul aria-label="Needs attention" className="flex flex-col gap-1.5">
          {attention.map((a, i) => (
            <li key={i} className="flex flex-wrap items-baseline gap-x-2 rounded-md border border-warn bg-warn-soft px-3 py-2 text-xs leading-relaxed text-warn">
              <Icon name="alert" size={13} className="shrink-0" />
              <span className="min-w-0 flex-1">{a.text}</span>
              {a.to && a.link && (
                <Link to={a.to} className="shrink-0 font-medium underline">
                  {a.link}
                </Link>
              )}
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-xs text-fg-2">
          All quiet: nothing needs attention. New signals appear on trading days inside the entry
          window (09:20-09:45 IST); between sessions this screen shows the last recorded state.
        </p>
      )}
    </Panel>
  );
}

// -- the plain-language guide ----------------------------------------------------------------------

const GUIDE_STEPS: { title: string; text: string; to: string; link: string }[] = [
  { title: "Market data arrives", text: "Quotes and settled daily bars stream in (YFinance or Dhan on paper; a recorded tape in the demo). The top bar always shows how fresh the newest quote is - LIVE, DELAYED, STALE or NO DATA - and stale data blocks new entries instead of trading on old prices.", to: "/system", link: "System" },
  { title: "A strategy proposes a signal", text: "Once per trading day, inside the 09:20-09:45 IST entry window, the enabled strategies scan the universe on settled bars. A signal is a proposal with its reasons attached - not an order.", to: "/decisions", link: "Decisions" },
  { title: "AI reviewers may veto (books B and C)", text: "Book A trades the raw strategy. Books B and C each add an AI reviewer that can only veto an entry - it never changes price, size, stop or target. 'No opinion' (abstain) is not an approval: the deterministic decision simply stands.", to: "/ai", link: "AI desk" },
  { title: "Risk checks decide", text: "Every proposed order passes the RiskEngine's hard limits: per-trade risk, exposure, sector caps, cash, data freshness, kill switches and the announcement calendar. A risk rejection is a rule, not an AI opinion; orders can also be resized to fit.", to: "/risk", link: "Risk centre" },
  { title: "The order goes to the simulated broker", text: "Approved entries become orders with a protective stop resting at the broker. This is paper trading: the broker is simulated, and every fill, rejection and expiry is recorded with its price lineage (decision price, arrival price, fill price).", to: "/blotter", link: "Blotter" },
  { title: "The position is managed to its exit", text: "Open positions are watched every tick: the stop caps the loss, the target books the profit, and a time exit closes anything still open after 10 sessions. Closed trades show their net P&L after charges.", to: "/blotter", link: "Blotter > Trades" },
];

function TradingGuide() {
  return (
    <Collapsible
      title="How RakshaQuant trades (plain-language guide)"
      summary="The six steps from a market tick to a closed position, what books A/B/C mean, and where to find each"
      badge={<Badge size="sm" tone="neutral">start here</Badge>}
    >
      <div className="flex flex-col gap-5 p-4">
        <ol className="flex flex-col gap-3">
          {GUIDE_STEPS.map((s, i) => (
            <li key={s.title} className="flex gap-3">
              <span aria-hidden="true" className="num flex h-6 w-6 shrink-0 items-center justify-center rounded-full border border-accent-line bg-accent-soft text-xs font-semibold text-accent-fg">
                {i + 1}
              </span>
              <span className="min-w-0">
                <span className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-sm font-medium text-fg-0">{s.title}</span>
                  <Link to={s.to} className="text-2xs text-accent-fg underline">{s.link}</Link>
                </span>
                <span className="mt-0.5 block text-xs leading-relaxed text-fg-1">{s.text}</span>
              </span>
            </li>
          ))}
        </ol>
        <div>
          <h3 className="rq-label">The three books (a paired experiment, all paper)</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {Object.entries(BOOKS).map(([id, b]) => (
              <li key={id} className="text-xs leading-relaxed text-fg-1">
                <span className="font-medium text-fg-0">{b.label}:</span> {b.plain}
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3 className="rq-label">The strategies</h3>
          <ul className="mt-2 flex flex-col gap-1.5">
            {Object.entries(STRATEGIES).map(([name, s]) => (
              <li key={name} className="text-xs leading-relaxed text-fg-1">
                <span className="font-mono text-fg-0">{name}</span> - {s.plain}
              </li>
            ))}
          </ul>
        </div>
        <p className="text-2xs leading-relaxed text-fg-2">
          Everything here is paper trading against a simulated broker: no real order is ever
          placed, and every number on every screen comes from a recorded event. Technical terms
          keep their original names in tooltips and Details sections.
        </p>
      </div>
    </Collapsible>
  );
}

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
        description="The whole book at a glance: what is happening right now, where the session is, how each paired book stands, what is open, and what the engine decided and flagged today."
        actions={
          <span className="flex items-center gap-2 rounded-full border border-line bg-bg-1 px-3 py-1.5 text-xs text-fg-1">
            <Icon name="calendar" size={14} className="text-fg-2" />
            <span className="num">{day}</span>
            <span className="text-fg-2">IST</span>
          </span>
        }
      />

      <RightNow warningCount={alertCount} />

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

      <TradingGuide />
    </Page>
  );
}
