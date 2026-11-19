// 2. Decision Inspector (/decisions/:id): what happened to one decision, for a trader first and
// an engineer second. The top of the page is the plain-language reading - a five-stage journey
// (Signal -> AI review -> Risk check -> Order -> Position/Exit) and one outcome line per book -
// all derived from recorded events by ./decisionSummary. Below it, the full technical timeline
// keeps every event, price and model detail, with the most technical parts collapsible.

import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { ApiError, get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { BOOKS, DISPOSITIONS } from "../lib/explain";
import { cx } from "../lib/cx";
import { num, symbolOf } from "../lib/format";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Collapsible } from "../ui/Collapsible";
import { Bps, DateIST, Money, Pct, Price, Qty, TimeIST } from "../ui/Num";
import { Page } from "../ui/PageHeader";
import { Empty, EmptyState, ErrorState, Field, FieldGrid, Panel } from "../ui/Panel";
import { describeError } from "../ui/QueryState";
import { Table } from "../ui/Table";
import { Timeline, type Step } from "../ui/Timeline";
import { DecisionLink, DISPOSITION } from "./common";
import { bookOutcomes, deriveStages, nextActions, type Stage } from "./decisionSummary";
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

// -- the plain-language summary ------------------------------------------------------------------

const STAGE_TONE: Record<Stage["status"], { badge: "neutral" | "up" | "warn" | "accent"; label: string; card: string }> = {
  done: { badge: "up", label: "done", card: "border-line bg-bg-2" },
  blocked: { badge: "warn", label: "blocked", card: "border-warn bg-warn-soft" },
  skipped: { badge: "neutral", label: "skipped", card: "border-line bg-bg-1" },
  pending: { badge: "neutral", label: "not reached", card: "border-line bg-bg-1" },
  active: { badge: "accent", label: "in progress", card: "border-accent-line bg-accent-soft" },
};

function StageTrack({ stages }: { stages: Stage[] }) {
  const go = (anchor: string) => document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth", block: "start" });
  return (
    <ol aria-label="Decision journey" className="grid gap-2 lg:grid-cols-5">
      {stages.map((s, i) => {
        const tone = STAGE_TONE[s.status];
        return (
          <li key={s.id} className="min-w-0">
            <button
              type="button"
              onClick={() => go(s.anchor)}
              title={[`${s.title} (${tone.label}): ${s.headline}`, ...s.details].join("\n")}
              className={cx(
                "flex h-full w-full flex-col gap-1.5 rounded-lg border px-3 py-2.5 text-left transition-colors duration-150 hover:border-fg-2",
                tone.card,
              )}
            >
              <span className="flex items-center gap-2">
                <span aria-hidden="true" className="num flex h-5 w-5 shrink-0 items-center justify-center rounded-full border border-line-strong bg-bg-0 text-2xs font-semibold text-fg-1">
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs font-semibold tracking-tight text-fg-0">{s.title}</span>
                <Badge size="sm" tone={tone.badge}>{tone.label}</Badge>
              </span>
              <span className="text-2xs leading-relaxed text-fg-1">{s.headline}</span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}

function WhatHappened({ lineage }: { lineage: Schemas["Lineage"] }) {
  const stages = deriveStages(lineage);
  const outcomes = bookOutcomes(lineage);
  const actions = nextActions(lineage, outcomes);
  return (
    <Panel
      title="What happened"
      description="This decision in plain words. Every line comes from a recorded event; the technical timeline below holds the raw detail."
      bodyClassName="flex flex-col gap-4 p-4"
    >
      <StageTrack stages={stages} />
      <ul aria-label="Outcome by book" className="flex flex-col gap-2">
        {outcomes.map((o) => (
          <li key={o.book} className="flex flex-wrap items-start gap-x-3 gap-y-1 rounded-md border border-line bg-bg-1 px-3 py-2.5">
            <span className="flex shrink-0 items-center gap-2">
              <Badge tone={o.tone === "neutral" ? "neutral" : o.tone}>{`Book ${o.book}: ${o.label}`}</Badge>
              {BOOKS[o.book] && (
                <span className="hidden text-2xs text-fg-2 xl:inline" title={BOOKS[o.book]?.plain}>
                  {BOOKS[o.book]?.label.replace(/^Book [A-C] - /, "")}
                </span>
              )}
            </span>
            <span className="min-w-0 flex-1 text-sm leading-relaxed text-fg-0">{o.sentence}</span>
            <span className="w-full text-2xs text-fg-2 sm:w-auto sm:shrink-0">
              <span className="font-medium text-fg-1">Next:</span> {o.next}
            </span>
          </li>
        ))}
      </ul>
      {actions.length > 0 && (
        <div className="rounded-md border border-accent-line bg-accent-soft px-3 py-2.5">
          <p className="text-xs font-semibold tracking-tight text-accent-fg">What to review or do next</p>
          <ul className="mt-1.5 flex list-disc flex-col gap-1 pl-4 text-xs leading-relaxed text-fg-1">
            {actions.map((a) => (
              <li key={a}>{a}</li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

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
                const explain = DISPOSITIONS[p.disposition]?.plain ?? "";
                return (
                  <Badge
                    key={d.seq}
                    tone={p.disposition === "submitted" ? "neutral" : undefined}
                    title={`${p.detail ? `${p.detail} — ` : ""}${explain}`}
                  >
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
            {(models.length > 0 || llm.length > 0) && (
              <Collapsible
                className="mt-3"
                title="Model calls (metadata)"
                summary={`${models.length} typed-model call(s), ${llm.length} LLM call(s): latencies, tokens, costs and the raw answers`}
                badge={<Badge size="sm" tone="neutral">technical</Badge>}
              >
                <div className="flex flex-col gap-3 p-3">
                  {models.map((m, i) => (
                    <div key={i}>
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
                    <div className="rounded-md border border-line">
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
              </Collapsible>
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
    <Collapsible
      title="Shadow ledger detail"
      summary="Advanced: a what-if record of the same signal traded on its own rules - it never touches a real book"
      badge={<Badge size="sm" tone="neutral">technical</Badge>}
      bodyClassName="p-3"
    >
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
    </Collapsible>
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
          <ErrorState message={`Could not load this decision: ${describeError(lineage.error)}`} />
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
    { id: "market", anchor: "step-market", title: "Market context", lead: "What the market looked like when this decision was made - all prices are recorded facts, not estimates.", meta: main && <TimeIST value={main.generated_at} label />, children: <MarketContext signal={main} risk={firstRisk} regime={regime} executions={executions} highlight={evidence} /> },
    { id: "signal", anchor: "step-signal", title: `Signal${signals.length > 1 ? "s" : ""}`, lead: "What the strategy saw and why it fired. A signal is a proposal - it only becomes an order if the AI review (where configured) and the risk checks let it through.", children: <Signals signals={signals} dispositions={ofType(events, "SignalDisposition")} /> },
    { id: "advisors", anchor: "step-advisors", title: "Advisors (per book)", lead: "Which AI reviewer looked at this trade, per book. A reviewer can only veto - it never changes price, size, stop or target, and 'no opinion' (abstain) is not an approval.", tone: ofType(events, "AdvisorVerdict").some((e) => data<VerdictData>(e).verdict === "VETO") ? "warn" : "neutral", children: <Advisors events={events} onEvidence={setEvidence} /> },
    { id: "risk", anchor: "step-risk", title: "Risk decision (per book)", lead: "The rule-based safety checks every order must pass - hard limits on size, exposure, cash, data freshness and kill switches. A rejection here is not an AI decision.", tone: ofType(events, "RiskDecision").some((e) => data<RiskData>(e).outcome !== "APPROVED") ? "warn" : "neutral", children: <RiskDecisions events={events} /> },
    { id: "orders", anchor: "step-orders", title: "Orders and fills", lead: "What actually went to the (simulated) broker and came back. Decision and arrival prices are plans; only the Fill column is an execution.", children: <Orders executions={executions} fills={fills} /> },
    { id: "exits", anchor: "step-exits", title: "Position and exit", lead: "How the position closed - stop, target, time exit or an operator action - and what it made or lost after charges.", children: <Exits events={events} /> },
    { id: "counterfactual", anchor: "step-counterfactual", title: "Counterfactual (shadow ledger)", lead: "Advanced, recorded-only: a what-if copy of the signal traded on its own rules. It never touches a real book.", children: <Counterfactual events={events} /> },
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
      <WhatHappened lineage={lineage.data} />
      <Panel title="Lineage" description="Every recorded event behind this decision, in the order it happened" bodyClassName="p-4">
        <Timeline steps={steps} />
      </Panel>
    </Page>
  );
}
