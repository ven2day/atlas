// 7. Market: the research page for one symbol. The quote summary sits on top, the chart takes the
// wide column, and the universe, the symbol's own state and the typed announcements stay to hand.
// Indicators are computed here from the symbol's real daily OHLCV; nothing is fetched from
// anywhere else and nothing is invented when the history is too short.

import { useQuery } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router";

import { get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { cx } from "../lib/cx";
import { dateIST, duration } from "../lib/format";
import { useIndicators } from "../lib/indicatorStore";
import { BY_ID, clampParams, instanceLabel, type Bar } from "../lib/indicators";
import { useStream } from "../lib/stream";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button, LinkButton } from "../ui/Button";
import { Chart } from "../ui/Chart";
import { DateIST, Pct, Price, TimeIST } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Empty, EmptyState, ErrorState, Field, FieldGrid, Panel, Skeleton } from "../ui/Panel";
import { Segmented, Toggle } from "../ui/Segmented";
import { IndicatorPanel } from "./market/IndicatorPanel";
import { Watchlist } from "./market/Watchlist";
import { buildIndicatorSeries, instanceColor } from "./market/series";

type Watch = Schemas["WatchRow"];

/** History ranges the bars endpoint can actually serve: it takes `days` (1-2000), daily bars only. */
const RANGES = [
  { value: "1m", label: "1M", days: 22 },
  { value: "3m", label: "3M", days: 66 },
  { value: "6m", label: "6M", days: 130 },
  { value: "1y", label: "1Y", days: 250 },
  { value: "3y", label: "3Y", days: 750 },
  { value: "5y", label: "5Y", days: 1250 },
  { value: "max", label: "Max", days: 2000 },
] as const;

type RangeId = (typeof RANGES)[number]["value"];
type ChartKind = "candles" | "line" | "area";

/** The chart needs real pixels: ask the viewport, and grow with each oscillator pane. */
function useChartHeight(panes: number): number {
  const [base, setBase] = useState(460);
  useEffect(() => {
    const mq = window.matchMedia("(min-width: 1024px)");
    const apply = () => setBase(mq.matches ? 460 : 300);
    apply();
    mq.addEventListener("change", apply);
    return () => mq.removeEventListener("change", apply);
  }, []);
  return base + panes * 120;
}

// -- the quote summary ---------------------------------------------------------------------------

function QuoteSummary({ symbol, row, loading }: { symbol: string; row: Watch | undefined; loading: boolean }) {
  if (!row) {
    return (
      <section className="rq-card flex flex-wrap items-center gap-x-6 gap-y-3 px-5 py-4">
        <h2 className="text-2xl font-semibold tracking-tight text-fg-0">{symbol}</h2>
        {loading ? (
          <span className="text-sm text-fg-2">Loading the quote…</span>
        ) : (
          <span className="text-sm text-fg-2">
            No live quote for this symbol: it is not in the current universe, so only stored history is shown.
          </span>
        )}
      </section>
    );
  }
  const age = row.quote_age_s;
  const stale = age !== null && age !== undefined && age > 1800;
  return (
    <section className="rq-card flex flex-col gap-4 px-5 py-4 lg:flex-row lg:items-center lg:gap-8">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <h2 className="text-2xl font-semibold tracking-tight text-fg-0">{row.symbol}</h2>
          {row.held && <Badge tone="accent">held by a book</Badge>}
          {row.sector && <Badge>{row.sector}</Badge>}
          {row.source && <Badge mono size="sm">{row.source}</Badge>}
        </div>
        <p className="mt-1 font-mono text-2xs text-fg-2">{row.instrument_key}</p>
      </div>

      <div className="flex items-end gap-3">
        <span className="num text-3xl font-semibold tracking-tight text-fg-0">
          <Price value={row.ltp} />
        </span>
        <span className="mb-1">
          {row.change_pct === null || row.change_pct === undefined ? (
            <span className="text-sm text-fg-2">no change data</span>
          ) : (
            <Pct value={row.change_pct} pnl arrow className="text-base font-medium" />
          )}
        </span>
      </div>

      <FieldGrid className="lg:min-w-0 lg:flex-1">
        <Field label="Previous close">
          <Price value={row.prev_close} />
        </Field>
        <Field label="Quote age">
          <span className={cx(stale ? "text-warn" : undefined)}>{age === null || age === undefined ? "—" : `${duration(age)} old`}</span>
        </Field>
        <Field label="Signals today">
          {row.signals_today.length ? (
            <span className="flex flex-wrap gap-1">
              {row.signals_today.map((s) => (
                <Badge key={s} size="sm">{s}</Badge>
              ))}
            </span>
          ) : (
            <span className="text-fg-2">none</span>
          )}
        </Field>
        <Field label="Event flags">
          {row.events.length ? (
            <span className="flex flex-wrap gap-1">
              {row.events.map((e) => (
                <Badge key={e} tone="warn" size="sm">{e}</Badge>
              ))}
            </span>
          ) : (
            <span className="text-fg-2">none</span>
          )}
        </Field>
      </FieldGrid>
    </section>
  );
}

// -- the chart card ------------------------------------------------------------------------------

function MarkerKey() {
  return (
    <span className="flex flex-wrap items-center gap-3 text-2xs text-fg-2">
      <span className="flex items-center gap-1">
        <Icon name="arrowUp" size={12} className="text-accent-fg" /> entry
      </span>
      <span className="flex items-center gap-1">
        <Icon name="arrowDown" size={12} className="text-down" /> exit
      </span>
      <span className="flex items-center gap-1">
        <span aria-hidden="true" className="h-1.5 w-1.5 rounded-full bg-warn" /> announcement
      </span>
    </span>
  );
}

export default function Market() {
  const [params, setParams] = useSearchParams();
  const [range, setRange] = useState<RangeId>("6m");
  const [kind, setKind] = useState<ChartKind>("candles");
  const [volume, setVolume] = useState(true);
  const [markersOn, setMarkersOn] = useState(true);
  const [adjusted, setAdjusted] = useState(true);
  const [picker, setPicker] = useState(false);
  const [allEvents, setAllEvents] = useState(false);

  const days = RANGES.find((r) => r.value === range)?.days ?? 130;

  const watch = useQuery({
    queryKey: ["watchlist"],
    queryFn: ({ signal }) => get("/api/market/watchlist", { signal }),
    refetchInterval: 15_000,
  });
  const live = useStream((s) => s.quotes);
  const rows = useMemo(
    () =>
      (watch.data ?? []).map((r) => {
        const q = live[r.instrument_key]; // the stream's conflated quote is fresher
        return q ? { ...r, ltp: q.ltp, prev_close: q.prev_close, change_pct: q.change_pct } : r;
      }),
    [watch.data, live],
  );
  const symbol = params.get("symbol") ?? rows[0]?.symbol ?? "";
  const row = rows.find((r) => r.symbol === symbol);

  const bars = useQuery({
    queryKey: keys.bars(symbol, days, adjusted),
    queryFn: ({ signal }) =>
      get("/api/market/{symbol}/bars", { params: { symbol }, query: { days, adjusted }, signal }),
    enabled: !!symbol,
  });
  const events = useQuery({
    queryKey: keys.typed({ symbol: allEvents ? undefined : symbol, limit: 100 }),
    queryFn: ({ signal }) =>
      get("/api/events/typed", { query: { symbol: allEvents ? undefined : symbol || undefined, limit: 100 }, signal }),
  });

  const active = useIndicators((s) => s.active);
  const remove = useIndicators((s) => s.remove);

  const history: Bar[] = useMemo(
    () => (bars.data?.bars ?? []).map((b) => ({ date: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })),
    [bars.data],
  );
  const candles = useMemo(
    () => history.map((b) => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })),
    [history],
  );
  const markers = useMemo(
    () => (markersOn ? (bars.data?.markers ?? []).map((m) => ({ time: m.date, kind: m.kind, text: m.text })) : []),
    [bars.data, markersOn],
  );
  const built = useMemo(() => buildIndicatorSeries(history, active, volume), [history, active, volume]);
  const height = useChartHeight(built.panes.length);

  const span = history.length
    ? `${dateIST(history[0]!.date)} – ${dateIST(history[history.length - 1]!.date)}`
    : null;
  const shortOfRequest = history.length > 0 && history.length < days;
  const source = bars.data?.source ?? "none";

  return (
    <Page>
      <PageHeader
        title="Market"
        description="Research one symbol: its quote, daily history, indicators over that history, and the entries, exits and announcements this system recorded for it."
        actions={
          <Button variant="secondary" size="md" onClick={() => setPicker(true)}>
            <Icon name="sliders" size={16} />
            Indicators
            {active.length > 0 && (
              <span className="num ml-1 rounded-full bg-bg-3 px-1.5 text-2xs text-fg-1">{active.length}</span>
            )}
          </Button>
        }
      />

      {!symbol ? (
        <EmptyState
          title="No symbol selected"
          hint="Pick one from the watchlist, or press Ctrl K and type a symbol."
        />
      ) : (
        <QuoteSummary symbol={symbol} row={row} loading={watch.isLoading} />
      )}

      <div className="grid min-w-0 gap-5 xl:grid-cols-[minmax(280px,330px)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-5">
          <Watchlist
            rows={rows}
            selected={symbol}
            loading={watch.isLoading}
            error={watch.error}
            onSelect={(next) => setParams({ symbol: next })}
          />

          {active.length > 0 && (
            <Panel title="Indicators on this chart" description={`${active.length} active · computed from these bars`}>
              <ul className="flex flex-col gap-1.5 p-3">
                {active.map((item, index) => {
                  const def = BY_ID.get(item.id);
                  if (!def) return null;
                  const label = instanceLabel(def, clampParams(def, item.params));
                  const tooShort = built.tooShort.some((t) => t.key === item.key);
                  return (
                    <li key={item.key} className="flex items-center gap-2 rounded-md bg-bg-2 px-2.5 py-2">
                      <span aria-hidden="true" className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ background: instanceColor(index) }} />
                      <span className="min-w-0 flex-1 truncate text-sm text-fg-0">{label}</span>
                      {tooShort && (
                        <span className="shrink-0 text-2xs text-warn" title="This window is shorter than the indicator's warm-up">
                          needs more history
                        </span>
                      )}
                      <LinkButton tone="danger" onClick={() => remove(item.key)} aria-label={`Remove ${label}`}>
                        <Icon name="close" size={14} />
                      </LinkButton>
                    </li>
                  );
                })}
              </ul>
            </Panel>
          )}
        </div>

        <div className="flex min-w-0 flex-col gap-5">
          <Panel
            title={symbol ? `${symbol} · daily` : "Chart"}
            description={
              bars.data
                ? `${history.length} sessions${span ? ` · ${span}` : ""} · ${adjusted ? "dividend-adjusted" : "raw"} · source ${source}`
                : "Daily bars only - the API does not serve intraday timeframes."
            }
            actions={
              <div className="flex flex-wrap items-center gap-2">
                <Segmented
                  label="Chart type"
                  size="xs"
                  value={kind}
                  onChange={setKind}
                  options={[
                    { value: "candles", label: "Candles", title: "Candlestick" },
                    { value: "line", label: "Line", title: "Close as a line" },
                    { value: "area", label: "Area", title: "Close as a filled area" },
                  ]}
                />
                <Segmented
                  label="History range"
                  size="xs"
                  value={range}
                  onChange={setRange}
                  options={RANGES.map((r) => ({ value: r.value, label: r.label, title: `Last ${r.days} sessions` }))}
                />
              </div>
            }
            bodyClassName="flex flex-col"
          >
            <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5">
              <Toggle checked={volume} onChange={setVolume}>Volume</Toggle>
              <Toggle checked={markersOn} onChange={setMarkersOn} title="This system's entries, exits and announcements">Markers</Toggle>
              <Toggle checked={adjusted} onChange={setAdjusted} title="Dividend-adjusted closes, as the features use them">Adjusted</Toggle>
              <span className="ml-auto"><MarkerKey /></span>
            </div>

            {bars.isLoading ? (
              <div className="p-4">
                <Skeleton rows={8} />
              </div>
            ) : bars.error ? (
              <div className="p-4">
                <ErrorState message={`Could not load the history: ${bars.error instanceof Error ? bars.error.message : "request failed"}`} />
              </div>
            ) : candles.length === 0 ? (
              <EmptyState
                title={source === "none" ? `No daily history for ${symbol}` : "No bars in this window"}
                hint={
                  source === "none"
                    ? "Nothing has been taped for this symbol yet. History appears once the engine has polled it, or from the demo tape."
                    : "Try a longer range."
                }
              />
            ) : (
              <>
                <Chart
                  label={`${symbol} daily ${kind === "candles" ? "candles" : "close line"} in rupees${volume ? " with volume" : ""}${markersOn ? ", with entries, exits and announcements" : ""}`}
                  kind={kind}
                  candles={candles}
                  markers={markers}
                  lines={built.lines}
                  panes={built.panes}
                  volume={volume}
                  legend
                  height={height}
                  axis="inr"
                />
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-2 text-2xs text-fg-2">
                  {shortOfRequest && (
                    <span className="text-warn">
                      Only {history.length} sessions are stored for {symbol}; the {range.toUpperCase()} range asked for {days}.
                    </span>
                  )}
                  {built.tooShort.length > 0 && (
                    <span>
                      {built.tooShort.map((t) => t.label).join(", ")} need{built.tooShort.length === 1 ? "s" : ""} a longer
                      window than these {history.length} sessions.
                    </span>
                  )}
                  <span className="ml-auto">Daily bars · {adjusted ? "adjusted" : "raw"} closes</span>
                </div>
              </>
            )}
          </Panel>
        </div>
      </div>

      <Panel
        title={allEvents ? "Announcements" : `Announcements${symbol ? ` · ${symbol}` : ""}`}
        description="Typed by the announcement model: type, direction and materiality, as recorded."
        actions={<Toggle checked={allEvents} onChange={setAllEvents}>All symbols</Toggle>}
      >
        {events.error ? (
          <div className="p-4">
            <ErrorState message={`Could not load announcements: ${events.error instanceof Error ? events.error.message : "request failed"}`} />
          </div>
        ) : !events.data?.length ? (
          <Empty>
            No classified announcements{allEvents || !symbol ? "" : ` for ${symbol}`}. The feed is polled during a session.
          </Empty>
        ) : (
          <ul className="divide-y divide-line">
            {events.data.map((e) => {
              const d = e.data as { title?: string; url?: string | null; model?: string };
              return (
                <li key={e.event_id} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 px-4 py-3">
                  <span className="num w-32 shrink-0 text-xs text-fg-2">
                    <DateIST value={e.published_at} /> <TimeIST value={e.published_at} />
                  </span>
                  <span className="w-16 shrink-0 text-sm font-medium text-fg-0">{e.symbol}</span>
                  <span className="flex shrink-0 flex-wrap items-center gap-1">
                    {e.announcement_type && <Badge size="sm">{e.announcement_type}</Badge>}
                    {e.direction && <Badge size="sm" tone={e.direction === "negative" ? "warn" : "neutral"}>{e.direction}</Badge>}
                    {e.materiality && <Badge size="sm">{e.materiality}</Badge>}
                  </span>
                  {d.url ? (
                    <a
                      href={d.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="min-w-0 flex-1 truncate text-sm text-accent-fg hover:underline"
                    >
                      {d.title}
                    </a>
                  ) : (
                    <span className="min-w-0 flex-1 truncate text-sm text-fg-0">{d.title}</span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </Panel>

      <IndicatorPanel open={picker} onOpenChange={setPicker} />
    </Page>
  );
}
