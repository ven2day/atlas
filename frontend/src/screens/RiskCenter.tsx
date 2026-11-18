// 4. Risk Center: the safety controls first, then the limits behind them - utilisation, kill
// switches, sector exposure, equity against its peak, what was blocked today and why.

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { get, post, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { cx } from "../lib/cx";
import { num } from "../lib/format";
import { useUi } from "../lib/ui";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Chart } from "../ui/Chart";
import { Collapsible } from "../ui/Collapsible";
import { ConfirmTyped } from "../ui/Dialog";
import { Meter } from "../ui/Meter";
import { DateIST, Money, TimeIST } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Empty, EmptyState, Panel } from "../ui/Panel";
import { QueryGate } from "../ui/QueryState";
import { StatCard } from "../ui/Stat";
import { useToasts } from "../ui/Toast";
import { BookPicker } from "./common";
import { usageDetail } from "./CommandCenter";

type BookRisk = Schemas["BookRisk"];

function Controls({ book, size = "md" }: { book: string; size?: "xs" | "sm" | "md" }) {
  const [dialog, setDialog] = useState<"RESUME" | "FLATTEN" | null>(null);
  const setHalt = useUi((s) => s.setHalt);
  const push = useToasts((s) => s.push);
  const client = useQueryClient();
  const act = async (phrase: "RESUME" | "FLATTEN", reason: string) => {
    const result =
      phrase === "RESUME"
        ? await post("/api/risk/resume", { confirm: true, phrase, reason, book })
        : await post("/api/risk/flatten", { confirm: true, phrase, reason, book });
    push({ level: phrase === "FLATTEN" ? "crit" : "info", title: `${phrase} ${result.outcome.replace("_", " ")}`, body: result.detail });
    void client.invalidateQueries();
  };
  return (
    <>
      <div className="flex flex-wrap items-center gap-2">
        <Button size={size} variant="danger" onClick={() => setHalt(true)} title="Block new entries (all books)">
          <Icon name="stop" size={14} strokeWidth={1.8} />
          HALT
        </Button>
        <Button size={size} variant="secondary" onClick={() => setDialog("RESUME")} title="Re-arm this book's global kill switch">
          <Icon name="play" size={14} strokeWidth={1.8} />
          RESUME {book}
        </Button>
        <Button size={size} variant="dangerSoft" onClick={() => setDialog("FLATTEN")} title="Exit every position in this book and block new entries">
          <Icon name="arrowDownRight" size={14} strokeWidth={1.8} />
          FLATTEN {book}
        </Button>
      </div>
      <ConfirmTyped
        open={dialog !== null}
        onOpenChange={(open) => !open && setDialog(null)}
        title={dialog === "FLATTEN" ? `Flatten book ${book}` : `Resume book ${book}`}
        description={
          dialog === "FLATTEN"
            ? "Exits every position of this book through the OMS at the next risk tick, and blocks new entries."
            : "Re-arms this book's global kill switch: new entries are allowed again. A HALT file blocks this."
        }
        phrase={dialog ?? "RESUME"}
        confirmLabel={dialog === "FLATTEN" ? "Flatten" : "Resume"}
        onConfirm={(reason) => act(dialog ?? "RESUME", reason)}
      />
    </>
  );
}

function Switches({ view }: { view: BookRisk }) {
  const rows = view.kill_switches.length
    ? view.kill_switches
    : [{ scope: "global", name: "global", state: "ARMED", reason: "never tripped", actor: "—", since: null }];
  return (
    // No overflow here on purpose: the enclosing Panel is the scroll container and carries the
    // keyboard focus, so a nested scroller cannot strand the table from keyboard users.
    <div>
      <table className="w-full min-w-[640px] border-collapse text-sm">
        <thead>
          <tr className="border-b border-line-strong bg-bg-2 text-left text-2xs uppercase tracking-wide text-fg-2">
            <th scope="col" className="px-4 py-2.5 font-medium">Switch</th>
            <th scope="col" className="px-4 py-2.5 font-medium">State</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Reason</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Actor</th>
            <th scope="col" className="px-4 py-2.5 font-medium">Since (IST)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((k) => (
            <tr key={`${k.scope}/${k.name}`} className="border-b border-line last:border-0">
              <td className="px-4 py-3 font-mono text-xs text-fg-0">{k.scope === k.name ? k.scope : `${k.scope}/${k.name}`}</td>
              <td className="px-4 py-3">
                <Badge tone={k.state === "ARMED" ? "neutral" : undefined}>{k.state === "HALT_NEW" ? "HALTED" : k.state}</Badge>
              </td>
              <td className="max-w-[280px] px-4 py-3 text-fg-1">{k.reason}</td>
              <td className="px-4 py-3 text-fg-1">{k.actor}</td>
              <td className="px-4 py-3">{k.since ? <TimeIST value={k.since} label /> : <span className="text-fg-2">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function Histogram({ counts }: { counts: Record<string, number> }) {
  const entries = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  if (!entries.length) return <Empty>No check blocked an order today.</Empty>;
  const max = Math.max(...entries.map(([, n]) => n));
  return (
    <ul className="flex flex-col gap-2.5 p-4 text-sm">
      {entries.map(([code, n]) => (
        <li key={code} className="grid grid-cols-[minmax(120px,200px)_1fr_auto] items-center gap-3">
          <span className="truncate font-mono text-xs text-fg-1" title={code}>{code}</span>
          <span className="h-2 overflow-hidden rounded-full bg-bg-3" aria-hidden="true">
            <span className="block h-full rounded-full bg-warn" style={{ width: `${(n / max) * 100}%` }} />
          </span>
          <span className="num w-8 text-right font-medium text-fg-0">{n}</span>
        </li>
      ))}
    </ul>
  );
}

export default function RiskCenter() {
  const [book, setBook] = useState<string | undefined>("A");
  const risk = useQuery({ queryKey: keys.risk(book), queryFn: ({ signal }) => get("/api/risk", { query: { book }, signal }), refetchInterval: 10_000 });
  const equity = useQuery({ queryKey: ["equity"], queryFn: ({ signal }) => get("/api/equity", { signal }), refetchInterval: 30_000 });
  const view = risk.data?.books[0];
  const curve = useMemo(() => equity.data?.books.find((b) => b.book_id === book)?.points ?? [], [equity.data, book]);
  const lines = useMemo(
    () => [
      { name: "Equity", tone: "accent" as const, points: curve.map((p) => ({ time: p.date, value: Number(p.equity) })) },
      { name: "Running peak", tone: "neutral" as const, dashed: true, points: curve.map((p) => ({ time: p.date, value: Number(p.peak) })) },
    ],
    [curve],
  );
  const limits = risk.data?.limits ?? {};
  const global = view?.kill_switches.find((k) => k.scope === "global");
  const tripped = !!global && global.state !== "ARMED";
  const rejections = Object.values(view?.rejections_today ?? {}).reduce((a, b) => a + b, 0);

  return (
    <Page>
      <PageHeader
        title="Risk centre"
        description="The limits the RiskEngine enforces, how much of each is used, and the three operator controls that override it. Kill switches latch: only an explicit resume re-arms them."
        actions={<BookPicker value={book} onChange={setBook} />}
      />

      <section
        className={cx(
          "rq-card flex flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center",
          tripped && "border-warn",
        )}
        aria-label="Safety controls"
      >
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Icon name="risk" size={18} className={tripped ? "text-warn" : "text-accent-fg"} />
            <h2 className="text-sm font-semibold text-fg-0">Book {book ?? "—"}</h2>
            <Badge tone={!global || global.state === "ARMED" ? "up" : global.state === "FLATTEN" ? "crit" : "warn"}>
              {global ? (global.state === "HALT_NEW" ? "HALTED" : global.state) : "ARMED"}
            </Badge>
          </div>
          <p className="mt-1.5 text-sm leading-relaxed text-fg-2">
            {tripped
              ? `${global?.reason ?? "Tripped"} — set by ${global?.actor ?? "—"}. New entries are blocked until an operator resumes with the typed phrase.`
              : "Armed: entries pass through every check. HALT blocks new entries at once and needs only a reason; RESUME and FLATTEN need the typed phrase."}
          </p>
        </div>
        {book && <Controls book={book} />}
      </section>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Kill switch" size="sm" value={global ? (global.state === "HALT_NEW" ? "HALTED" : global.state) : "ARMED"} hint={tripped ? "latched, needs a resume" : "armed"} />
        <StatCard label="Valuation" size="sm" value={view?.valuation ?? "—"} hint={view?.valuation === "live" ? "this tick" : "from the last mark"} />
        <StatCard label="Checks blocked today" size="sm" value={num(rejections, 0)} hint="across every reason code" />
        <StatCard label="Event blocks" size="sm" value={num(view?.event_blocks.length ?? 0, 0)} hint="entries held for announcements" />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <Panel title="Limits in use" description="100% is where the check blocks an order">
          <QueryGate
            isLoading={risk.isLoading}
            isError={risk.isError}
            error={risk.error}
            data={view}
            emptyTitle="No risk view for this book"
            emptyHint="The risk view appears once the engine has recorded a session for this book. Run a paper session or replay a recorded day."
            className="p-4"
            skeletonRows={5}
          >
            {(bookView) => (
              <div className="flex flex-col gap-4 p-4">
                {bookView.utilisation.map((u) => (
                  <Meter key={u.key} label={u.label} fraction={u.fraction} detail={usageDetail(u)} />
                ))}
                <p className="text-2xs leading-relaxed text-fg-2">
                  {bookView.valuation === "live"
                    ? "Live, with the RiskEngine's own definitions."
                    : "From the stored state and the last mark: heat and sector limits need a running session."}
                </p>
              </div>
            )}
          </QueryGate>
        </Panel>

        <Panel title={`Kill switches · book ${book ?? ""}`} description="Every switch that can stop this book, and who set it" bodyClassName="overflow-x-auto" scrollable>
          {view ? <Switches view={view} /> : <Empty>No risk view for this book.</Empty>}
        </Panel>

        <Panel title="Sector exposure" description="Share of the sector cap each sector uses">
          {view && view.sectors.length ? (
            <div className="flex flex-col gap-4 p-4">
              {view.sectors.map((u) => (
                <Meter key={u.key} label={u.label} fraction={u.fraction} detail={usageDetail(u)} />
              ))}
            </div>
          ) : (
            <EmptyState
              title={view?.valuation === "live" ? "No open positions" : "Needs live marks"}
              hint={view?.valuation === "live" ? "Sector exposure is zero while the book is flat." : "Sector exposure is computed from live marks, so it needs a running session."}
            />
          )}
        </Panel>

        <Panel title={`Equity vs peak · book ${book ?? ""}`} description="Daily marks in rupees, with the running peak the drawdown limit measures from">
          {curve.length ? (
            <Chart label={`Book ${book} equity and running peak, rupees, by session`} lines={lines} height={260} axis="inr" legend />
          ) : (
            <EmptyState title="No marks yet" hint="The first mark is recorded when a session ends." />
          )}
        </Panel>

        <Panel title="Blocked checks today" description="Counts by the RiskEngine's own reason codes">
          <Histogram counts={view?.rejections_today ?? {}} />
        </Panel>

        <Panel title="Event-calendar blocks" description="Entries held because of a classified announcement">
          {view && view.event_blocks.length ? (
            <ul className="divide-y divide-line">
              {view.event_blocks.map((b) => (
                <li key={`${b.instrument_key}${b.event_id}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-3 text-sm">
                  <span className="font-medium text-fg-0">{b.symbol}</span>
                  <Badge mono size="sm">{b.code}</Badge>
                  <span className="num text-xs text-fg-2">
                    <DateIST value={b.start} /> – <DateIST value={b.end} />
                  </span>
                  <span className="min-w-0 flex-1 truncate text-fg-1" title={b.reason}>{b.reason}</span>
                </li>
              ))}
            </ul>
          ) : (
            <Empty>No announcement is blocking an entry right now.</Empty>
          )}
        </Panel>
      </div>

      <Collapsible
        title="Configured limits"
        summary={`Read-only, from the running configuration · hash ${risk.data?.limits_hash ?? "—"}`}
        badge={<Badge mono size="sm">{Object.keys(limits).length} limits</Badge>}
      >
        {Object.keys(limits).length === 0 ? (
          <Empty>No limits are published. They come from src/config/limits.py and the RISK_* settings.</Empty>
        ) : (
          <dl className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-x-6 gap-y-3 p-4 text-sm">
            {Object.entries(limits)
              .filter(([, v]) => typeof v === "number" || typeof v === "boolean")
              .map(([k, v]) => (
                <div key={k} className="flex min-w-0 items-baseline justify-between gap-3 border-b border-line pb-2">
                  <dt className="min-w-0 truncate font-mono text-xs text-fg-2" title={k}>{k}</dt>
                  <dd className="num shrink-0 text-fg-0">
                    {typeof v === "number" ? (
                      k.toLowerCase().includes("inr") ? <Money value={v} /> : num(v, v % 1 ? 4 : 0)
                    ) : (
                      String(v)
                    )}
                  </dd>
                </div>
              ))}
          </dl>
        )}
      </Collapsible>
    </Page>
  );
}
