// 2. Decision Inspector (/decisions/:id): one decision's full lineage as a timeline - market
// context → signal → each book's advisor → each book's risk decision → orders and fills → the
// position's exit → the counterfactual. Everything shown is a recorded event.

import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { ApiError, get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { cx } from "../lib/cx";
import { num, symbolOf } from "../lib/format";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Bps, DateIST, Money, Pct, Price, Qty, TimeIST } from "../ui/Num";
import { Page } from "../ui/PageHeader";
import { Empty, EmptyState, ErrorState, Field, FieldGrid, Panel } from "../ui/Panel";
import { Table } from "../ui/Table";
import { Timeline, type Step } from "../ui/Timeline";
import { DecisionLink, DISPOSITION } from "./common";
import {
  booksOf,
  data,
  ofType,
  type AlphaData,
  type DispositionData,
  type FillData,
  type LineageEvent,
  type LLMCallData,
  type ModelCallData,
  type RiskData,
  type ShadowData,
  type SignalData,
  type TradeData,
  type VerdictData,
} from "./lineage";

type Execution = Schemas["Execution"];

// -- the steps ---------------------------------------------------------------------------------

function MarketContext({ signal, risk, regime, executions, highlight }: {
  signal: SignalData | undefined;
  risk: RiskData | undefined;
  regime: string | null;
  executions: Execution[];
  highlight: string | null;
}) {
  const entry = executions.find((x) => x.kind === "open");
  return (
    <>
      <FieldGrid>
        <Field label="Signal bar (settled)">{signal ? <DateIST value={signal.bar_date} /> : "—"}</Field>
        <Field label="Regime">{regime ?? "—"}</Field>
        <Field label="Decision price">
          <Price value={entry?.decision_price} />
        </Field>
        <Field label="Arrival price">
          <Price value={entry?.arrival_price ?? risk?.ref_price} />
        </Field>
        <Field label="Quote age at decision">
          {risk?.snapshot?.quote_age_s != null ? `${num(risk.snapshot.quote_age_s, 0)} s` : "—"}
        </Field>
        <Field label="Data source">{risk?.snapshot?.data_source ?? "—"}</Field>
      </FieldGrid>
      {signal && signal.reasons.length > 0 && (
        <div className="mt-4 rounded-md border border-line bg-bg-1">
          <Table
            head={["Feature", "Value", "Reading"]}
            align={["l", "r", "l"]}
            rows={signal.reasons.map((r) => [
              <span key="n" className={cx("font-mono text-xs", highlight && highlight.endsWith(r.name) && "rounded-sm bg-accent-soft px-1 text-accent-fg")}>
                {r.name}
              </span>,
              <span key="v">{typeof r.value === "number" ? num(r.value, 4) : (r.value ?? "—")}</span>,
              <span key="d" className="text-fg-1">{r.detail || "—"}</span>,
            ])}
          />
        </div>
      )}
    </>
  );
}

function Signals({ signals, dispositions }: { signals: SignalData[]; dispositions: LineageEvent[] }) {
  return (
    <div className="flex flex-col gap-4">
      {signals.map((s) => (
        <div key={s.signal_id} className="rounded-md border border-line bg-bg-1 p-3">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <Badge>{s.side}</Badge>
            <span className="font-medium text-fg-0">{s.strategy}</span>
            {s.is_shadow && <Badge tone="neutral">shadow: recorded only</Badge>}
            <span className="text-fg-2">
              agreement <span className="num text-fg-0">{num(s.agreement_score, 2)}</span>
            </span>
            <span className="text-fg-2">
              stop/target <span className="num text-fg-0">{s.stop_atr_mult ?? "—"}× / {s.target_atr_mult ?? "—"}× ATR</span>
            </span>
            <span className="ml-auto text-fg-2">
              generated <TimeIST value={s.generated_at} label />
            </span>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {dispositions
              .filter((d) => data<DispositionData>(d).signal_id === s.signal_id)
              .map((d) => {
                const p = data<DispositionData>(d);
                return (
                  <Badge key={d.seq} tone={p.disposition === "submitted" ? "neutral" : undefined} title={p.detail}>
                    {`${d.book_id} ${DISPOSITION[p.disposition] ?? p.disposition}`}
                  </Badge>
                );
              })}
          </div>
        </div>
      ))}
    </div>
  );
}

function Advisors({ events, onEvidence }: { events: LineageEvent[]; onEvidence: (ref: string | null) => void }) {
  const books = booksOf(ofType(events, "AdvisorRequested"));
  if (!books.length) return <span className="text-fg-2">No advisor reviewed this decision (book A has none by design).</span>;
  return (
    <div className="flex flex-col gap-4">
      {books.map((book) => {
        const verdict = ofType(events, "AdvisorVerdict", book).map(data<VerdictData>)[0];
        const models = ofType(events, "DecisionModelCall", book).map(data<ModelCallData>);
        const llm = ofType(events, "LLMCall", book).map(data<LLMCallData>);
        return (
          <div key={book} className="rounded-md border border-line bg-bg-1 p-3">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2 text-sm">
              <span className="font-semibold text-fg-0">Book {book}</span>
              <span className="text-fg-1">{verdict?.advisor ?? "—"}</span>
              {verdict && <Badge>{verdict.verdict}</Badge>}
              {verdict?.confidence != null && (
                <span className="text-fg-2">
                  confidence <span className="num text-fg-0">{num(verdict.confidence, 2)}</span>
                </span>
              )}
              {verdict?.abstain_reason && <span className="text-warn">{verdict.abstain_reason}</span>}
              {verdict?.model && (
                <span className="ml-auto font-mono text-2xs text-fg-2">
                  {verdict.provider ? `${verdict.provider}:` : ""}{verdict.model}
                </span>
              )}
            </div>
            {verdict && verdict.reasons.length > 0 && (
              <ul className="mt-3 flex flex-col gap-2">
                {verdict.reasons.map((r, i) => (
                  <li key={i} className="flex flex-wrap items-baseline gap-2 text-sm">
                    <button
                      type="button"
                      onMouseEnter={() => onEvidence(r.evidence_ref)}
                      onMouseLeave={() => onEvidence(null)}
                      onFocus={() => onEvidence(r.evidence_ref)}
                      onBlur={() => onEvidence(null)}
                      className="rounded-md border border-line-strong bg-bg-2 px-1.5 py-0.5 font-mono text-2xs text-accent-fg transition-colors duration-150 hover:border-accent"
                      title="Highlights the referenced input above"
                    >
                      {r.evidence_ref}
                    </button>
                    <span className="min-w-0 flex-1 text-fg-0">{r.claim}</span>
                  </li>
                ))}
              </ul>
            )}
            {models.map((m, i) => (
              <div key={i} className="mt-3">
                <p className="font-mono text-2xs text-fg-2">
                  {m.model}:{m.checkpoint} · {num(m.latency_ms, 0)} ms{m.escalated ? " · escalated" : ""}
                  {m.calibrated ? " · calibrated" : " · uncalibrated"}
                </p>
                <div className="mt-1.5 rounded-md border border-line">
                  <Table
                    head={["Question", "Answer", "P(answer)"]}
                    align={["l", "l", "r"]}
                    rows={Object.entries(m.answers).map(([q, a]) => [
                      <span key="q" className="font-mono text-xs">{q}</span>,
                      String(a.value ?? "—"),
                      <span key="p">{a.confidence != null ? num(a.confidence, 2) : "—"}</span>,
                    ])}
                  />
                </div>
              </div>
            ))}
            {llm.length > 0 && (
              <div className="mt-3 rounded-md border border-line">
                <Table
                  head={["Model", "Outcome", "Tokens in/out", "Latency", "Cost"]}
                  align={["l", "l", "r", "r", "r"]}
                  rows={llm.map((call) => [
                    <span key="m" className="font-mono text-xs">{call.provider}:{call.model}</span>,
                    call.outcome,
                    <span key="t">{`${num(call.tokens_in, 0)} / ${num(call.tokens_out, 0)}`}</span>,
                    <span key="l">{`${num(call.latency_ms, 0)} ms`}</span>,
                    <Money key="c" value={call.cost_inr} />,
                  ])}
                />
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function RiskDecisions({ events }: { events: LineageEvent[] }) {
  const decided = ofType(events, "RiskDecision");
  if (!decided.length) return <span className="text-fg-2">No risk decision: nothing was proposed to the RiskEngine.</span>;
  return (
    <div className="flex flex-col gap-4">
      {decided.map((e) => {
        const r = data<RiskData>(e);
        return (
          <div key={e.seq} className="rounded-md border border-line bg-bg-1 p-3">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2 text-sm">
              <span className="font-semibold text-fg-0">Book {e.book_id}</span>
              <span className="text-fg-1">{r.kind === "open" ? "entry" : `exit (${r.kind})`}</span>
              <Badge tone={r.outcome === "APPROVED" ? "up" : undefined}>{r.outcome}</Badge>
              <span className="text-fg-2">
                qty <Qty value={r.qty_approved} className="text-fg-0" />
                {r.qty_requested != null && <> of <Qty value={r.qty_requested} /></>}
              </span>
              <span className="text-fg-2">
                notional <Money value={r.notional} className="text-fg-0" />
              </span>
              {r.kind === "open" && (
                <>
                  <span className="text-fg-2">
                    risk to stop <Money value={r.risk_amount} className="text-fg-0" />
                  </span>
                  {r.risk_pct_equity != null && (
                    <span className="text-fg-2">
                      (<Pct value={r.risk_pct_equity * 100} /> of equity)
                    </span>
                  )}
                  <span className="text-fg-2">
                    stop <Price value={r.stop_price} className="text-fg-0" /> · target <Price value={r.target_price} className="text-fg-0" />
                  </span>
                </>
              )}
              <span className="ml-auto font-mono text-2xs text-fg-2">limits {r.limits_hash}</span>
            </div>
            {r.reasons.length > 0 ? (
              <div className="mt-3 rounded-md border border-line">
                <Table
                  minWidth={720}
                  head={["Check", "Outcome", "Observed", "Limit", "Detail"]}
                  align={["l", "l", "r", "r", "l"]}
                  rows={r.reasons.map((check) => [
                    <span key="c" className="font-mono text-xs">{check.code}</span>,
                    <Badge key="o" tone={check.outcome === "block" ? "warn" : "neutral"}>{check.outcome}</Badge>,
                    <span key="ob">{check.observed ?? "—"}</span>,
                    <span key="l">{check.limit ?? "—"}</span>,
                    <span key="m" className="text-fg-1">{check.message}{check.max_qty != null ? ` (max ${check.max_qty})` : ""}</span>,
                  ])}
                />
              </div>
            ) : (
              <p className="mt-3 text-sm text-fg-2">Every check passed.</p>
            )}
          </div>
        );
      })}
    </div>
  );
}

function Orders({ executions, fills }: { executions: Execution[]; fills: FillData[] }) {
  if (!executions.length) return <span className="text-fg-2">No order was placed.</span>;
  return (
    <>
      <div className="rounded-md border border-line bg-bg-1">
        <Table
          minWidth={1180}
          head={["Book", "Leg", "Side", "Qty", "Filled", "Decision", "Arrival", "Fill", "vs decision", "vs arrival", "Charges", "Status"]}
          align={["l", "l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "l"]}
          rows={executions.map((x) => [
            x.book_id,
            x.kind,
            <Badge key="s">{x.side}</Badge>,
            <Qty key="q" value={x.quantity} />,
            <Qty key="f" value={x.filled_qty} />,
            <Price key="d" value={x.decision_price} />,
            <Price key="a" value={x.arrival_price} />,
            <Price key="p" value={x.avg_fill_price} />,
            <Bps key="sd" value={x.slippage_vs_decision_bps} pnl />,
            <Bps key="sa" value={x.slippage_vs_arrival_bps} pnl />,
            <Money key="c" value={x.charges} />,
            <Badge key="st">{x.status}</Badge>,
          ])}
          footer="Slippage is adverse-positive: paying up on a buy, selling below on a sell."
        />
      </div>
      {fills.length > 0 && (
        <div className="mt-3 rounded-md border border-line bg-bg-1">
          <Table
            minWidth={760}
            head={["Fill time", "Qty", "Price", "Charges", "Breakdown"]}
            align={["l", "r", "r", "r", "l"]}
            rows={fills.map((f) => [
              <TimeIST key="t" value={f.fill.ts} label />,
              <Qty key="q" value={f.fill.quantity} />,
              <Price key="p" value={f.fill.price} />,
              <Money key="c" value={f.fill.charges} />,
              <span key="b" className="text-fg-1">
                {Object.entries(f.fill.charges_breakdown ?? {})
                  .map(([k, v]) => `${k} ${v}`)
                  .join(" · ")}
              </span>,
            ])}
          />
        </div>
      )}
    </>
  );
}

function Exits({ events }: { events: LineageEvent[] }) {
  const trades = ofType(events, "TradeClosed");
  if (!trades.length) return <span className="text-fg-2">No position closed from this decision yet.</span>;
  return (
    <div className="rounded-md border border-line bg-bg-1">
      <Table
        minWidth={980}
        head={["Book", "Exit", "Qty", "Entry", "Exit price", "Charges", "Net P&L", "Closed", "Exit decision"]}
        align={["l", "l", "r", "r", "r", "r", "r", "l", "l"]}
        rows={trades.map((e) => {
          const t = data<TradeData>(e);
          return [
            e.book_id,
            <Badge key="r">{t.exit_reason}</Badge>,
            <Qty key="q" value={t.quantity} />,
            <Price key="e" value={t.entry_price} />,
            <Price key="x" value={t.exit_price} />,
            <Money key="c" value={t.charges} />,
            <Money key="n" value={t.net_pnl} pnl arrow />,
            <TimeIST key="t" value={t.exit_ts} label />,
            <DecisionLink key="d" id={t.exit_decision_id} />,
          ];
        })}
      />
    </div>
  );
}

function Counterfactual({ events }: { events: LineageEvent[] }) {
  const closed = ofType(events, "ShadowTradeClosed").map(data<ShadowData>);
  const opened = ofType(events, "ShadowTradeOpened").map(data<ShadowData>);
  const alpha = ofType(events, "ShadowAlphaSettled").map(data<AlphaData>);
  if (!opened.length) return <span className="text-fg-2">No counterfactual (only BUY signals with an ATR get one).</span>;
  return (
    <div className="rounded-md border border-line bg-bg-1">
      <Table
        minWidth={880}
        head={["Signal", "Entry", "Exit", "Exit reason", "Net P&L", "Net return", "Alpha vs NIFTY"]}
        align={["l", "r", "r", "l", "r", "r", "r"]}
        rows={opened.map((o) => {
          const c = closed.find((x) => x.signal_id === o.signal_id);
          const a = alpha.find((x) => x.signal_id === o.signal_id);
          return [
            <span key="s" className="font-mono text-xs">{o.strategy}{o.is_shadow_strategy ? " (shadow)" : ""}</span>,
            <Price key="e" value={o.entry_price} />,
            <Price key="x" value={c?.exit_price} />,
            c ? <Badge key="r">{c.exit_reason ?? "—"}</Badge> : <span key="r" className="text-fg-2">open</span>,
            <Money key="n" value={c?.net_pnl} pnl />,
            <Pct key="p" value={c?.net_return_pct} pnl />,
            <Pct key="a" value={a?.alpha_pct} pnl />,
          ];
        })}
      />
    </div>
  );
}

function InspectorHeader({
  symbol,
  strategy,
  side,
  id,
  exits,
}: {
  symbol: string;
  strategy: string | null;
  side: ReactNode;
  id: string;
  exits: string[];
}) {
  const [copied, setCopied] = useState(false);
  return (
    <section className="rq-card flex flex-wrap items-center gap-x-5 gap-y-3 px-5 py-4">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="text-xl font-semibold tracking-tight text-fg-0">
            {symbol} · {strategy ?? "decision"}
          </h1>
          {side}
        </div>
        <p className="mt-1 truncate font-mono text-2xs text-fg-2" title={id}>{id}</p>
      </div>
      {exits.length > 0 && (
        <span className="flex items-center gap-1.5 text-xs text-fg-2">
          exits: {exits.map((x) => <DecisionLink key={x} id={x} />)}
        </span>
      )}
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={() => {
            void navigator.clipboard?.writeText(window.location.href).then(() => setCopied(true));
          }}
        >
          <Icon name={copied ? "check" : "link"} size={14} />
          {copied ? "Link copied" : "Copy link"}
        </Button>
        <Link
          to={`/market?symbol=${encodeURIComponent(symbol)}`}
          className="inline-flex h-8 items-center gap-1.5 rounded-md border border-line-strong bg-bg-2 px-3 text-sm font-medium text-fg-0 transition-colors duration-150 hover:border-fg-2 hover:bg-bg-3"
        >
          <Icon name="market" size={14} />
          Open chart
        </Link>
      </div>
    </section>
  );
}

export default function DecisionInspector() {
  const { id = "" } = useParams();
  const [evidence, setEvidence] = useState<string | null>(null);
  const lineage = useQuery({
    queryKey: keys.decision(id),
    queryFn: ({ signal }) => get("/api/decisions/{decision_id}", { params: { decision_id: id }, signal }),
    retry: (count, error) => !(error instanceof ApiError && error.status === 404) && count < 1,
  });

  if (lineage.isLoading) {
    return (
      <Page>
        <Empty>Loading this decision's lineage…</Empty>
      </Page>
    );
  }
  if (lineage.error || !lineage.data) {
    const missing = lineage.error instanceof ApiError && lineage.error.status === 404;
    return (
      <Page>
        {missing ? (
          <EmptyState
            title="No such decision"
            hint={`No decision recorded with id ${id}. It may predate this event store, or the link may be mistyped.`}
          />
        ) : (
          <ErrorState message={`Could not load this decision: ${lineage.error instanceof Error ? lineage.error.message : "request failed"}`} />
        )}
      </Page>
    );
  }

  const { events, executions, regime, exit_decision_ids } = lineage.data;
  const signals = ofType(events, "SignalGenerated").map((e) => data<{ signal: SignalData }>(e).signal);
  const main = signals.find((s) => !s.is_shadow) ?? signals[0];
  const firstRisk = ofType(events, "RiskDecision").map(data<RiskData>)[0];
  const fills = ofType(events, "FillReceived").map(data<FillData>);
  const symbol = main ? symbolOf(main.instrument_key) : "—";

  const steps: Step[] = [
    { id: "market", title: "Market context", meta: main && <TimeIST value={main.generated_at} label />, children: <MarketContext signal={main} risk={firstRisk} regime={regime} executions={executions} highlight={evidence} /> },
    { id: "signal", title: `Signal${signals.length > 1 ? "s" : ""}`, children: <Signals signals={signals} dispositions={ofType(events, "SignalDisposition")} /> },
    { id: "advisors", title: "Advisors (per book)", tone: ofType(events, "AdvisorVerdict").some((e) => data<VerdictData>(e).verdict === "VETO") ? "warn" : "neutral", children: <Advisors events={events} onEvidence={setEvidence} /> },
    { id: "risk", title: "Risk decision (per book)", tone: ofType(events, "RiskDecision").some((e) => data<RiskData>(e).outcome !== "APPROVED") ? "warn" : "neutral", children: <RiskDecisions events={events} /> },
    { id: "orders", title: "Orders and fills", children: <Orders executions={executions} fills={fills} /> },
    { id: "exits", title: "Position and exit", children: <Exits events={events} /> },
    { id: "counterfactual", title: "Counterfactual (shadow ledger)", children: <Counterfactual events={events} /> },
  ];

  return (
    <Page>
      <InspectorHeader
        symbol={symbol}
        strategy={main?.strategy ?? null}
        side={main ? <Badge>{main.side}</Badge> : null}
        id={id}
        exits={exit_decision_ids}
      />
      <Panel title="Lineage" description="Every recorded event behind this decision, in the order it happened" bodyClassName="p-4">
        <Timeline steps={steps} />
      </Panel>
    </Page>
  );
}
