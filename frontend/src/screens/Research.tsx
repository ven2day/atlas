// Research: event-aware candidates, each with a probabilistic forecast and the evidence behind it.
//
// Decision support only. Nothing on this screen places, sizes, prices or routes an order, and no
// status is an instruction to trade - CONFIRMED means the catalyst, the market and the historical
// distribution all point the same way, not "buy". Every candidate matching the filters is listed;
// there is deliberately no "top five" cut, because hiding the weak-evidence cases is exactly what
// makes a research screen misleading.

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { Icon } from "../shell/icons";
import { Badge, type Tone } from "../ui/Badge";
import { Collapsible } from "../ui/Collapsible";
import { Page, PageHeader, Toolbar } from "../ui/PageHeader";
import { ErrorState, Panel, Skeleton } from "../ui/Panel";
import { describeError } from "../ui/QueryState";
import { Select, SymbolLink, TextFilter } from "./common";

type Candidate = Schemas["ResearchCandidate"];
type CardEvent = Schemas["ResearchCardEvent"];

const STATUSES = ["", "CONFIRMED", "SETUP_FORMING", "WATCH", "INVALIDATED", "NO_TRADE"];
const ASSETS = ["", "equity", "index", "sector", "unsupported"];
const EVENT_TYPES = [
  "",
  "results",
  "results_date",
  "dividend",
  "split_bonus",
  "pledge",
  "insider_or_promoter",
  "order_win",
  "litigation_or_regulatory",
  "management_change",
  "rating_change",
  "fundraise",
  "other",
];
const CONFIDENCES: [string, string][] = [
  ["", "Any confidence"],
  ["0.5", "≥ 0.50"],
  ["0.7", "≥ 0.70"],
  ["0.85", "≥ 0.85"],
];
const UNCERTAIN: [string, string][] = [
  ["true", "Include uncertain mappings"],
  ["false", "Only certain mappings"],
];

// Amber marks a blocked or degraded state; green and red stay reserved for price direction, so a
// status never borrows a directional colour.
const STATUS_TONE: Record<string, Tone> = {
  CONFIRMED: "accent",
  SETUP_FORMING: "neutral",
  WATCH: "neutral",
  INVALIDATED: "warn",
  NO_TRADE: "neutral",
};

const DIRECTION_TONE: Record<string, Tone> = {
  positive: "up",
  negative: "down",
  mixed: "warn",
  neutral: "neutral",
  unknown: "neutral",
};

const pct = (value: number | null | undefined, digits = 1): string =>
  value === null || value === undefined ? "—" : `${(value * 100).toFixed(digits)}%`;

const signed = (value: number | null | undefined, digits = 4): string =>
  value === null || value === undefined ? "—" : `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;

const num = (value: number | null | undefined, digits = 2): string =>
  value === null || value === undefined ? "—" : value.toFixed(digits);

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-0.5 text-[12px]">
      <span className="w-44 shrink-0 text-fg-2">{label}</span>
      <span className="min-w-0 flex-1 text-fg-1">{children}</span>
    </div>
  );
}

function EventBlock({ event }: { event: CardEvent }) {
  return (
    <div className="border-line border-t py-2 first:border-t-0">
      <div className="flex flex-wrap items-center gap-2 text-[12px]">
        <Badge tone={DIRECTION_TONE[event.direction] ?? "neutral"}>{event.direction}</Badge>
        <span className="text-fg-0">{event.event_type ?? "unclassified"}</span>
        <span className="text-fg-2">via {event.channel}</span>
        {event.materiality ? <span className="text-fg-2">· {event.materiality}</span> : null}
        {event.uncertain ? <Badge tone="warn">uncertain</Badge> : null}
        <span className="text-fg-2">· confidence {num(event.confidence, 3)}</span>
        <span className="text-fg-2">· basis {event.basis}</span>
      </div>
      <p className="mt-1 text-[13px] text-fg-1">{event.excerpt}</p>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-0.5 text-[11px] text-fg-2">
        <span>published {event.published_at}</span>
        <span>known to us {event.known_at}</span>
        <span>source {event.source}</span>
        {event.url ? (
          <a className="text-accent-fg inline-flex items-center gap-1 hover:underline" href={event.url} target="_blank" rel="noreferrer">
            <Icon name="external" size={11} />
            exchange announcement
          </a>
        ) : null}
      </div>
      <p className="mt-1 text-[12px] text-fg-2">
        <span className="text-fg-1">why: </span>
        {event.reason}
      </p>
      {event.updates.length > 0 ? (
        <p className="mt-1 text-[11px] text-warn">supersedes {event.updates.join(", ")}</p>
      ) : null}
      <details className="mt-1">
        <summary className="cursor-pointer text-[11px] text-fg-2 hover:text-fg-1">
          evidence cited ({event.evidence.length})
        </summary>
        <ul className="mt-1 space-y-0.5 pl-4 text-[11px] text-fg-2">
          {event.evidence.map((item) => (
            <li key={`${item.field}-${item.value.slice(0, 24)}`}>
              <span className="text-fg-1">{item.field}</span> = {item.value}
              {item.note ? <span className="text-fg-2"> ({item.note})</span> : null}
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}

function CandidateCard({ card }: { card: Candidate }) {
  const prediction = card.forecast;
  const context = card.context;
  return (
    <Collapsible
      title={`${card.symbol} ${card.status}`}
      // Summary first: the figures a trader scans for are in the header, so a long list of
      // candidates stays readable. The actionable ones start open; the rest start closed.
      defaultOpen={card.status === "CONFIRMED" || card.status === "SETUP_FORMING"}
      summary={
        <span className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
          <span>{card.company || card.instrument_key}</span>
          <span aria-hidden="true">·</span>
          <span>{card.sector ?? "sector unknown"}</span>
          <span aria-hidden="true">·</span>
          <span>{card.horizon_sessions}-session horizon</span>
          <span aria-hidden="true">·</span>
          {prediction ? (
            <span className="num">
              up {pct(prediction.p_up)} / side {pct(prediction.p_side)} / down{" "}
              {pct(prediction.p_down)} · n={prediction.n_samples}
            </span>
          ) : (
            <span>no forecast</span>
          )}
          <span aria-hidden="true">·</span>
          <span>market {card.confirmation}</span>
        </span>
      }
      badge={
        <>
          <Badge tone={STATUS_TONE[card.status] ?? "neutral"}>{card.status}</Badge>
          {card.freshness.stale ? <Badge tone="warn">stale data</Badge> : null}
        </>
      }
      bodyClassName="flex flex-col"
    >
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-2">
        <SymbolLink symbol={card.symbol} />
        <span className="font-mono text-[11px] text-fg-2">{card.instrument_key}</span>
        <span className="text-[11px] text-fg-2">{card.asset}</span>
        <span className="ml-auto text-[11px] text-fg-2">
          {card.freshness.stale ? "inputs are stale" : "live inputs"}
        </span>
      </div>
      <div className="grid gap-4 p-4 lg:grid-cols-2">
        <div>
          <h4 className="mb-1 text-[11px] tracking-wide text-fg-2 uppercase">Forecast</h4>
          {prediction === null ? (
            <p className="text-[12px] text-warn">
              No forecast. A missing forecast is reported rather than filled in.
            </p>
          ) : (
            <>
              <div className="flex flex-wrap gap-2 text-[12px]">
                <Badge tone="up">up {pct(prediction.p_up)}</Badge>
                <Badge>sideways {pct(prediction.p_side)}</Badge>
                <Badge tone="down">down {pct(prediction.p_down)}</Badge>
              </div>
              <div className="mt-2">
                <Row label="return range q10–q90">
                  {signed(prediction.q10)} … {signed(prediction.q90)} (median {signed(prediction.q50)})
                </Row>
                <Row label="p(underperform index)">{pct(prediction.p_underperform_benchmark)}</Row>
                <Row label="sideways band">±{pct(prediction.sideways_band, 2)}</Row>
                <Row label="analog sample">
                  {prediction.n_samples} of {prediction.n_unconditional} ({prediction.baseline})
                </Row>
                <Row label="state / regime">
                  trend {prediction.trend ?? "—"}, vol {prediction.vol_band ?? "—"}
                  {prediction.regime ? `, regime ${prediction.regime}` : ", regime unknown"}
                </Row>
                <Row label="status">{prediction.status}</Row>
                <Row label="data">{prediction.data_quality}</Row>
              </div>
              {prediction.notes.length > 0 ? (
                <ul className="mt-1 space-y-0.5 text-[11px] text-warn">
                  {prediction.notes.map((note) => (
                    <li key={note}>! {note}</li>
                  ))}
                </ul>
              ) : null}
            </>
          )}

          <h4 className="mt-3 mb-1 text-[11px] tracking-wide text-fg-2 uppercase">Market context</h4>
          <Row label="trend / regime">
            {context.trend}
            {context.regime ? ` · ${context.regime}` : " · regime unknown"}
          </Row>
          <Row label="close / RSI / ADX">
            {num(context.close)} · {num(context.rsi_14)} · {num(context.adx_14)}
          </Row>
          <Row label="ATR% / daily sigma">{pct(context.atr_pct, 2)} · {num(context.sigma_daily, 4)}</Row>
          <Row label="volume vs 20-session">{num(context.volume_ratio, 2)}x</Row>
          <Row label="relative strength">
            {Object.entries(context.relative_strength)
              .map(([lookback, value]) => `${lookback}s ${signed(value)}`)
              .join("  ·  ") || "—"}
          </Row>
          <Row label="agrees with catalyst">
            <span className={card.confirmation === "disagrees" ? "text-warn" : "text-fg-1"}>
              {card.confirmation}
            </span>{" "}
            — {card.confirmation_reason}
          </Row>
        </div>

        <div>
          <h4 className="mb-1 text-[11px] tracking-wide text-fg-2 uppercase">
            Why this status ({card.reasons.length})
          </h4>
          <ul className="space-y-0.5 text-[12px] text-fg-1">
            {card.reasons.map((reason) => (
              <li key={reason}>· {reason}</li>
            ))}
          </ul>

          <div className="mt-3 grid gap-3 sm:grid-cols-2">
            <div>
              <h4 className="mb-1 text-[11px] tracking-wide text-up uppercase">Positive</h4>
              <ul className="space-y-0.5 text-[12px] text-fg-1">
                {card.positive_factors.length === 0 ? <li className="text-fg-2">none recorded</li> : null}
                {card.positive_factors.map((factor) => (
                  <li key={factor}>+ {factor}</li>
                ))}
              </ul>
            </div>
            <div>
              <h4 className="mb-1 text-[11px] tracking-wide text-down uppercase">Negative</h4>
              <ul className="space-y-0.5 text-[12px] text-fg-1">
                {card.negative_factors.length === 0 ? <li className="text-fg-2">none recorded</li> : null}
                {card.negative_factors.map((factor) => (
                  <li key={factor}>− {factor}</li>
                ))}
              </ul>
            </div>
          </div>

          <div className="mt-3 grid gap-3 sm:grid-cols-3">
            {([
              ["Would confirm", card.confirm_if],
              ["Would weaken", card.weaken_if],
              ["Would invalidate", card.invalidate_if],
            ] as const).map(([heading, items]) => (
              <div key={heading}>
                <h4 className="mb-1 text-[11px] tracking-wide text-fg-2 uppercase">{heading}</h4>
                <ul className="space-y-0.5 text-[11px] text-fg-1">
                  {items.length === 0 ? <li className="text-fg-2">—</li> : null}
                  {items.map((item) => (
                    <li key={item}>· {item}</li>
                  ))}
                </ul>
              </div>
            ))}
          </div>

          {card.unsupported.length > 0 ? (
            <div className="mt-3">
              <h4 className="mb-1 text-[11px] tracking-wide text-warn uppercase">
                Unsupported or missing
              </h4>
              <ul className="space-y-0.5 text-[11px] text-fg-1">
                {card.unsupported.map((item) => (
                  <li key={item}>! {item}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <p className="mt-3 text-[11px] text-fg-2">
            Freshness: last bar {card.freshness.last_bar_date ?? "—"}
            {card.freshness.bar_age_sessions !== null && card.freshness.bar_age_sessions !== undefined
              ? ` (${card.freshness.bar_age_sessions} session(s) old)`
              : ""}
            {card.freshness.event_age_days !== null && card.freshness.event_age_days !== undefined
              ? ` · newest catalyst ${card.freshness.event_age_days} day(s) ago`
              : ""}
          </p>
          {card.freshness.notes.map((note) => (
            <p key={note} className="text-[11px] text-warn">
              ! {note}
            </p>
          ))}
        </div>
      </div>

      <div className="border-t border-line px-4 py-3">
        <h4 className="mb-1 text-[11px] tracking-wide text-fg-2 uppercase">
          Catalysts ({card.events.length})
        </h4>
        {card.events.length === 0 ? (
          <p className="text-[12px] text-fg-2">
            No current catalyst: an earlier one was superseded by a correction.
          </p>
        ) : null}
        {card.events.map((event) => (
          <EventBlock key={event.event_id} event={event} />
        ))}
      </div>
    </Collapsible>
  );
}

export default function Research() {
  const [status, setStatus] = useState("");
  const [asset, setAsset] = useState("");
  const [sector, setSector] = useState("");
  const [eventType, setEventType] = useState("");
  const [minConfidence, setMinConfidence] = useState("");
  const [includeUncertain, setIncludeUncertain] = useState("true");

  const query = {
    status: status || undefined,
    asset: asset || undefined,
    sector: sector.trim() || undefined,
    event_type: eventType || undefined,
    min_confidence: minConfidence || undefined,
    include_uncertain: includeUncertain,
    limit: 500,
  };
  const research = useQuery({
    queryKey: keys.research(query),
    queryFn: ({ signal }) => get("/api/research/candidates", { query, signal }),
    refetchInterval: 60_000,
  });
  const view = research.data;
  const candidates = view?.candidates ?? [];
  const evaluation = view?.evaluation ?? null;
  const counts = view?.counts ?? {};
  // The walk-forward evaluation is published by scripts/research_history.py, not by this screen:
  // it is read-only here, and an absent evaluation is shown as absent rather than as zeros.
  const walkForward = useQuery({
    queryKey: keys.researchEvaluation,
    queryFn: ({ signal }) => get("/api/research/evaluation", { signal }),
    staleTime: 60_000,
  });
  const wf = walkForward.data;

  return (
    <Page>
      <PageHeader
        title="Research"
        description="Event-aware candidates with probabilistic, horizon-specific forecasts and the evidence behind each. Decision support only: nothing here places an order, and no status is an instruction to trade."
      />

      <Toolbar>
        <Select
          label="Status"
          value={status}
          onChange={setStatus}
          options={STATUSES.map((s) => ({ value: s, label: s || "All statuses" }))}
        />
        <Select
          label="Asset"
          value={asset}
          onChange={setAsset}
          options={ASSETS.map((a) => ({ value: a, label: a || "All assets" }))}
        />
        <Select
          label="Event type"
          value={eventType}
          onChange={setEventType}
          options={EVENT_TYPES.map((t) => ({ value: t, label: t || "All event types" }))}
        />
        <TextFilter label="Sector" value={sector} onChange={setSector} placeholder="e.g. Financial Services" width="w-52" />
        <Select
          label="Confidence"
          value={minConfidence}
          onChange={setMinConfidence}
          options={CONFIDENCES.map(([value, label]) => ({ value, label }))}
        />
        <Select
          label="Uncertainty"
          value={includeUncertain}
          onChange={setIncludeUncertain}
          options={UNCERTAIN.map(([value, label]) => ({ value, label }))}
        />
      </Toolbar>

      {research.isLoading ? (
        <Panel title="Research" description="Reading the latest published run">
          <Skeleton rows={4} className="px-4 py-4" />
        </Panel>
      ) : null}
      {research.isError ? (
        <Panel title="Research unavailable">
          <div className="flex flex-col gap-2.5 p-4">
            <ErrorState message={describeError(research.error)} />
            <p className="text-xs leading-relaxed text-fg-2">
              This screen only reads the snapshot that{" "}
              <code className="font-mono text-fg-1">scripts/run_research.py</code> publishes; it
              never triggers a run. If the launch token has expired, reopen the console from the
              link the server printed when it started.
            </p>
          </div>
        </Panel>
      ) : null}

      {view ? (
        <Panel
          title={`Latest run · ${candidates.length} candidate(s)`}
          description={`As of ${view.as_of}, ${view.horizon}-session horizon. Published by scripts/run_research.py; this screen never triggers a run.`}
          footer={view.disclaimer}
        >
          <div className="flex flex-wrap gap-2">
            {Object.entries(counts).map(([name, count]) => (
              <Badge key={name} tone={STATUS_TONE[name] ?? "neutral"}>
                {name} {count}
              </Badge>
            ))}
            {Object.keys(counts).length === 0 ? <Badge>no candidates in this run</Badge> : null}
          </div>
          {view.data_gaps.length > 0 ? (
            <div className="mt-3">
              <h4 className="mb-1 text-[11px] tracking-wide text-warn uppercase">
                Data gaps and unsupported inputs
              </h4>
              <ul className="space-y-0.5 text-[12px] text-fg-1">
                {view.data_gaps.map((gap) => (
                  <li key={gap}>! {gap}</li>
                ))}
              </ul>
            </div>
          ) : null}
        </Panel>
      ) : null}

      {candidates.map((card) => (
        <CandidateCard key={`${card.instrument_key}-${card.horizon_sessions}`} card={card} />
      ))}

      {evaluation ? (
        <Panel
          title="Evaluation"
          description="Published forecasts scored against outcomes they did not know. Calibration is a measurement, not a claim of skill or profit."
          footer="Brier is multi-class over up / sideways / down: lower is better, and always answering the middle bucket scores about 0.667. Cells with too few observations report their count instead of a percentage."
        >
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <Row label="settled forecasts">{evaluation.settled}</Row>
              <Row label="multi-class Brier">{num(evaluation.brier, 4)}</Row>
              {evaluation.notes.map((note) => (
                <p key={note} className="mt-1 text-[11px] text-warn">
                  ! {note}
                </p>
              ))}
              {evaluation.cells.length > 0 ? (
                <table className="mt-2 w-full text-[12px]">
                  <thead>
                    <tr className="text-fg-2 text-left">
                      <th className="font-normal">predicted p(up)</th>
                      <th className="font-normal">n</th>
                      <th className="font-normal">predicted</th>
                      <th className="font-normal">realized</th>
                    </tr>
                  </thead>
                  <tbody>
                    {evaluation.cells.map((cell) => (
                      <tr key={cell.bucket} className="border-line border-t">
                        <td className="text-fg-1">{cell.bucket}</td>
                        <td className="text-fg-1">{cell.n}</td>
                        <td className="text-fg-1">{cell.predicted === null ? "too few" : num(cell.predicted, 3)}</td>
                        <td className="text-fg-1">{cell.realized === null ? "too few" : num(cell.realized, 3)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : null}
            </div>
            <div>
              <h4 className="mb-1 text-[11px] tracking-wide text-fg-2 uppercase">
                Event study ({evaluation.study.length} cells)
              </h4>
              {evaluation.study.length === 0 ? (
                <p className="text-[12px] text-fg-2">
                  No settled event windows yet: an event study needs sessions to have elapsed after
                  each catalyst, so it fills in as the horizon passes.
                </p>
              ) : (
                <div className="overflow-x-auto">
                  <table className="w-full text-[12px]">
                    <thead>
                      <tr className="text-fg-2 text-left">
                        <th className="font-normal">event type</th>
                        <th className="font-normal">dir</th>
                        <th className="font-normal">h</th>
                        <th className="font-normal">n</th>
                        <th className="font-normal">mean AR</th>
                        <th className="font-normal">hit</th>
                        <th className="font-normal">net</th>
                      </tr>
                    </thead>
                    <tbody>
                      {evaluation.study.map((cell) => (
                        <tr key={`${cell.event_type}-${cell.direction}-${cell.horizon_sessions}-${cell.regime ?? "any"}`} className="border-line border-t">
                          <td className="text-fg-1">{cell.event_type}</td>
                          <td className="text-fg-1">{cell.direction}</td>
                          <td className="text-fg-1">{cell.horizon_sessions}</td>
                          <td className="text-fg-1">{cell.n}</td>
                          <td className="text-fg-1">{signed(cell.mean_abnormal_return)}</td>
                          <td className="text-fg-1">{cell.hit_rate === null ? "—" : num(cell.hit_rate, 2)}</td>
                          <td className="text-fg-1">{signed(cell.mean_net_abnormal_return)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          </div>
        </Panel>
      ) : null}
      <Panel
        title="Walk-forward evaluation"
        description={
          wf?.available
            ? `Run ${wf.run_id ?? "—"} · mode ${wf.evaluation_mode ?? "—"} · published ${wf.created_at ?? "—"}`
            : "No walk-forward evaluation has been published yet."
        }
        footer="Frozen forecasts scored only against outcomes that completed after their origin. Intervals use a moving-block bootstrap, because overlapping event windows are not independent samples. These are close-to-close reaction measurements, not trade returns."
      >
        {wf?.available ? (
          <>
            <div className="overflow-x-auto">
              <table className="w-full text-[12px]">
                <thead>
                  <tr className="text-fg-2 text-left">
                    <th className="font-normal">approach</th>
                    <th className="font-normal">n</th>
                    <th className="font-normal">Brier</th>
                    <th className="font-normal">95% CI</th>
                    <th className="font-normal">q10–q90 coverage</th>
                    <th className="font-normal">mean AR</th>
                    <th className="font-normal">fallbacks</th>
                  </tr>
                </thead>
                <tbody>
                  {wf.approaches.map((a) => (
                    <tr key={a.approach} className="border-line border-t">
                      <td className="text-fg-1">{a.approach}</td>
                      <td className="text-fg-1">{a.n}</td>
                      <td className="text-fg-1">{a.brier === null ? "—" : num(a.brier, 4)}</td>
                      <td className="text-fg-1">
                        {a.brier_ci
                          ? `${num(a.brier_ci[0], 4)} … ${num(a.brier_ci[1], 4)}`
                          : "insufficient"}
                      </td>
                      <td className="text-fg-1">
                        {a.interval_coverage === null ? "—" : pct(a.interval_coverage)}
                      </td>
                      <td className="text-fg-1">{signed(a.mean_abnormal_return)}</td>
                      <td className="text-fg-1">{a.fallback_count}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {wf.comparisons.length > 0 ? (
              <div className="mt-3 overflow-x-auto">
                <table className="w-full text-[12px]">
                  <thead>
                    <tr className="text-fg-2 text-left">
                      <th className="font-normal">approach</th>
                      <th className="font-normal">vs baseline</th>
                      <th className="font-normal">n paired</th>
                      <th className="font-normal">mean loss diff</th>
                      <th className="font-normal">verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {wf.comparisons.map((c) => (
                      <tr key={`${c.approach}-${c.baseline}`} className="border-line border-t">
                        <td className="text-fg-1">{c.approach}</td>
                        <td className="text-fg-1">{c.baseline}</td>
                        <td className="text-fg-1">{c.n_paired}</td>
                        <td className="text-fg-1">{signed(c.mean_loss_difference, 6)}</td>
                        <td className="text-fg-1">{c.verdict}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : null}
            <ul className="mt-2 space-y-0.5 text-[11px] text-warn">
              {wf.notes.map((note) => (
                <li key={note}>! {note}</li>
              ))}
            </ul>
          </>
        ) : (
          <p className="text-[12px] text-fg-2">
            Nothing to show until an evaluation is published. Run{" "}
            <code className="text-fg-1">scripts/research_history.py walkforward</code> after
            importing history and building outcomes. An empty panel here means "not measured yet",
            never "measured and found nothing".
          </p>
        )}
      </Panel>
    </Page>
  );
}
