// The trader-facing reading of one decision's lineage: a five-stage journey
// (Signal -> AI review -> Risk check -> Order -> Position/Exit), one plain sentence per book,
// and what to do next. Pure functions over the recorded events only - nothing is inferred that
// the event store did not record, and every technical term keeps its original value alongside
// the plain label (src/lib/explain.ts).

import type { Schemas } from "../api/client";
import {
  DISPOSITIONS,
  EXIT_REASONS,
  ORDER_STATUSES,
  REASON_CODES,
  RISK_OUTCOMES,
  STRATEGIES,
  VERDICTS,
  plain,
} from "../lib/explain";
import { inr, num, symbolOf, timeIST } from "../lib/format";
import {
  booksOf,
  data,
  ofType,
  type DispositionData,
  type FillData,
  type RiskData,
  type SignalData,
  type TradeData,
  type VerdictData,
} from "./lineage";

import type { LineageEvent } from "./lineage";

type Lineage = Schemas["Lineage"];
type Execution = Schemas["Execution"];

export type StageStatus = "done" | "blocked" | "skipped" | "pending" | "active";

export interface Stage {
  id: "signal" | "ai" | "risk" | "order" | "exit";
  title: string;
  /** Anchor id of the matching step in the technical timeline below. */
  anchor: string;
  status: StageStatus;
  headline: string;
  details: string[];
}

export type OutcomeTone = "neutral" | "up" | "down" | "warn" | "accent";

export interface BookOutcome {
  book: string;
  /** The raw disposition value, for the badge (original term kept). */
  disposition: string | null;
  label: string;
  sentence: string;
  next: string;
  tone: OutcomeTone;
}

interface FallbackData {
  advisor: string;
  reason: string;
  error: string | null;
}

function entryRisk(events: LineageEvent[], book?: string): RiskData | null {
  const found = ofType(events, "RiskDecision", book)
    .map(data<RiskData>)
    .filter((r) => r.kind === "open");
  return found[0] ?? null;
}

function blockingCode(risk: RiskData | null): { code: string; text: string } | null {
  if (!risk) return null;
  const block = risk.reasons.find((c) => c.outcome === "block");
  if (!block) return null;
  return { code: block.code, text: REASON_CODES[block.code] ?? block.message };
}

function entryExecution(executions: Execution[], book: string): Execution | undefined {
  return executions.find((x) => x.book_id === book && x.kind === "open");
}

function tradeOf(events: LineageEvent[], book: string): TradeData | null {
  const trades = ofType(events, "TradeClosed", book).map(data<TradeData>);
  return trades[trades.length - 1] ?? null;
}

/** The five stages of the journey, with a status and a headline derived from recorded events. */
export function deriveStages(lineage: Lineage): Stage[] {
  const events = lineage.events;
  const signals = ofType(events, "SignalGenerated").map((e) => data<{ signal: SignalData }>(e).signal);
  const main = signals.find((s) => !s.is_shadow) ?? signals[0];
  const symbol = main ? symbolOf(main.instrument_key) : "—";

  // -- 1. signal --------------------------------------------------------------------------------
  const signalStage: Stage = !main
    ? {
        id: "signal",
        title: "Signal",
        anchor: "step-signal",
        status: "pending",
        headline: "No signal was recorded for this decision.",
        details: [],
      }
    : {
        id: "signal",
        title: "Signal",
        anchor: "step-signal",
        status: main.is_shadow ? "skipped" : "done",
        headline: main.is_shadow
          ? `${main.strategy} flagged ${main.side} ${symbol} - shadow strategy: recorded, never traded.`
          : `${main.strategy} proposed ${main.side} ${symbol} at ${timeIST(main.generated_at)} IST.`,
        details: [
          ...(STRATEGIES[main.strategy]?.plain ? [`Why it fired: ${STRATEGIES[main.strategy]?.plain}`] : []),
          ...main.reasons
            .filter((r) => r.detail)
            .map((r) => `${r.name}: ${r.detail}${typeof r.value === "number" ? ` (${num(r.value, 2)})` : ""}`),
        ],
      };

  // -- 2. AI review ------------------------------------------------------------------------------
  const requested = ofType(events, "AdvisorRequested");
  const books = booksOf(requested);
  let aiStage: Stage;
  if (!books.length) {
    aiStage = {
      id: "ai",
      title: "AI review",
      anchor: "step-advisors",
      status: "skipped",
      headline: "No AI reviewer looked at this decision (book A trades the raw strategy, by design).",
      details: ["Books B and C each add a veto-only AI reviewer; this decision involved neither, or their reviews are recorded per book below."],
    };
    const anyVerdicts = ofType(events, "AdvisorVerdict");
    if (anyVerdicts.length) {
      aiStage = {
        id: "ai",
        title: "AI review",
        anchor: "step-advisors",
        status: anyVerdicts.some((e) => data<VerdictData>(e).verdict === "VETO") ? "blocked" : "done",
        headline: summarizeVerdicts(anyVerdicts),
        details: [],
      };
    }
  } else {
    const verdicts = ofType(events, "AdvisorVerdict");
    const fallbacks = ofType(events, "AdvisorFallback").map(data<FallbackData>);
    const vetoed = verdicts.filter((e) => data<VerdictData>(e).verdict === "VETO");
    aiStage = {
      id: "ai",
      title: "AI review",
      anchor: "step-advisors",
      status: vetoed.length ? "blocked" : "done",
      headline: summarizeVerdicts(verdicts),
      details: [
        ...verdicts.map((e) => {
          const v = data<VerdictData>(e);
          const said = plain(VERDICTS, v.verdict);
          const why = v.reasons[0]?.claim ?? v.abstain_reason ?? "";
          return `Book ${e.book_id} (${v.advisor}): ${said.label}${why ? ` - ${why}` : ""}`;
        }),
        ...fallbacks.map((f) => `Book fallback: the AI advisor could not answer (${f.reason}); the deterministic decision stood.`),
        "An AI reviewer can only veto; it never changes price, size, stop or target. 'No opinion' (abstain) is not an approval.",
      ],
    };
  }

  // -- 3. risk check ------------------------------------------------------------------------------
  const risks = ofType(events, "RiskDecision").map((e) => ({ book: e.book_id, risk: data<RiskData>(e) }));
  const entries = risks.filter((r) => r.risk.kind === "open");
  let riskStage: Stage;
  if (!entries.length) {
    riskStage = {
      id: "risk",
      title: "Risk check",
      anchor: "step-risk",
      status: "skipped",
      headline: "Risk checks were never reached - the signal was gated, skipped or vetoed before an order was proposed.",
      details: [],
    };
  } else {
    const blocked = entries.filter((e) => e.risk.outcome === "REJECTED" || e.risk.outcome === "HALTED");
    const resized = entries.filter((e) => e.risk.outcome === "RESIZED");
    const firstBlocked = blocked[0];
    const firstResized = resized[0];
    const firstBlock = firstBlocked ? blockingCode(firstBlocked.risk) : null;
    riskStage = {
      id: "risk",
      title: "Risk check",
      anchor: "step-risk",
      status: blocked.length ? "blocked" : "done",
      headline: firstBlocked
        ? `Blocked for book ${firstBlocked.book}: ${firstBlock ? `${firstBlock.code} - ${firstBlock.text}` : plain(RISK_OUTCOMES, firstBlocked.risk.outcome).label}.`
        : firstResized
          ? `Passed with a smaller size for book ${firstResized.book} (a limit resized the order).`
          : "Every risk check passed for the proposed size.",
      details: [
        ...entries.map((e) => {
          const outcome = plain(RISK_OUTCOMES, e.risk.outcome);
          const block = blockingCode(e.risk);
          return `Book ${e.book}: ${outcome.label}${block ? ` (${block.code}: ${block.text})` : ""}`;
        }),
        "Risk rejections are hard limits (capital, exposure, stale data, kill switches) - a different decision from an AI veto.",
      ],
    };
  }

  // -- 4. order -----------------------------------------------------------------------------------
  const executions = lineage.executions;
  const fills = ofType(events, "FillReceived").map(data<FillData>);
  let orderStage: Stage;
  if (!executions.length) {
    orderStage = {
      id: "order",
      title: "Order",
      anchor: "step-orders",
      status: "skipped",
      headline: "No order was sent for this decision.",
      details: [],
    };
  } else {
    const entry = (executions.find((x) => x.kind === "open") ?? executions[0])!;
    const status = plain(ORDER_STATUSES, entry.status);
    const rejected = executions.some((x) => x.status === "REJECTED");
    const working = executions.some((x) => ["SUBMITTED", "OPEN", "TRIGGER_PENDING", "PARTIALLY_FILLED", "UNKNOWN"].includes(x.status));
    orderStage = {
      id: "order",
      title: "Order",
      anchor: "step-orders",
      status: rejected ? "blocked" : working ? "active" : "done",
      headline:
        entry.filled_qty > 0
          ? `Filled ${entry.filled_qty} of ${entry.quantity} ${entry.side === "BUY" ? "bought" : "sold"} at ${inr(entry.avg_fill_price)} (broker: simulated).`
          : `Order ${status.label}${entry.status === "REJECTED" ? " - the broker refused it" : ""}.`,
      details: [
        `Planned prices are not fills: decision ${inr(entry.decision_price)} (the signal bar's close), arrival ${inr(entry.arrival_price)} (the quote at submission); the fill above is what actually executed.`,
        ...executions
          .filter((x) => x !== entry)
          .map((x) => `${x.kind} leg: ${plain(ORDER_STATUSES, x.status).label}${x.filled_qty ? ` (${x.filled_qty} @ ${inr(x.avg_fill_price)})` : ""}`),
        fills.length ? `${fills.length} fill(s) recorded.` : "",
      ].filter((d): d is string => !!d),
    };
  }

  // -- 5. position / exit -------------------------------------------------------------------------
  const trades = ofType(events, "TradeClosed").map((e) => ({ book: e.book_id, trade: data<TradeData>(e) }));
  const entryFilled = executions.some((x) => x.kind === "open" && x.filled_qty > 0);
  let exitStage: Stage;
  if (trades.length) {
    const t = trades[trades.length - 1]!;
    const net = Number(t.trade.net_pnl);
    exitStage = {
      id: "exit",
      title: "Position / Exit",
      anchor: "step-exits",
      status: "done",
      headline: `Closed (${plain(EXIT_REASONS, t.trade.exit_reason).label}) for book ${t.book}: net ${inr(net, { sign: true })} after charges.`,
      details: trades.map((x) => `Book ${x.book}: entry ${inr(x.trade.entry_price)} -> exit ${inr(x.trade.exit_price)}, ${plain(EXIT_REASONS, x.trade.exit_reason).plain}`),
    };
  } else if (entryFilled) {
    const risk = entryRisk(events);
    exitStage = {
      id: "exit",
      title: "Position / Exit",
      anchor: "step-exits",
      status: "active",
      headline: "Position open - it is being managed by its stop, target and the 10-session time exit.",
      details: [
        risk?.stop_price ? `Stop: ${inr(risk.stop_price)} (protective stop order resting at the broker).` : "",
        risk?.target_price ? `Target: ${inr(risk.target_price)}.` : "",
      ].filter((d): d is string => !!d),
    };
  } else {
    exitStage = {
      id: "exit",
      title: "Position / Exit",
      anchor: "step-exits",
      status: "pending",
      headline: "No position was opened from this decision.",
      details: [],
    };
  }

  return [signalStage, aiStage, riskStage, orderStage, exitStage];
}

function summarizeVerdicts(verdicts: LineageEvent[]): string {
  if (!verdicts.length) return "No AI reviewer answered this decision.";
  const rows = verdicts.map((e) => ({ book: e.book_id, v: data<VerdictData>(e) }));
  const veto = rows.find((r) => r.v.verdict === "VETO");
  if (veto) return `Book ${veto.book}'s AI reviewer vetoed this entry - no order was sent for that book.`;
  const approvals = rows.filter((r) => r.v.verdict === "APPROVE").length;
  const abstains = rows.filter((r) => r.v.verdict === "ABSTAIN").length;
  const parts: string[] = [];
  if (approvals) parts.push(`${approvals} approved`);
  if (abstains) parts.push(`${abstains} gave no opinion (abstained - the decision stands without the AI)`);
  return `AI review complete: ${parts.join(", ") || "no verdict recorded"}.`;
}

/** One plain-language outcome line per book that saw the signal. */
export function bookOutcomes(lineage: Lineage): BookOutcome[] {
  const events = lineage.events;
  const dispositions = ofType(events, "SignalDisposition");
  const books = booksOf(events);
  return books.map((book) => {
    const disp = dispositions.filter((d) => d.book_id === book).map(data<DispositionData>).pop() ?? null;
    const verdictEvent = ofType(events, "AdvisorVerdict", book)[0];
    const verdict = verdictEvent ? data<VerdictData>(verdictEvent) : null;
    const risk = entryRisk(events, book);
    const execution = entryExecution(lineage.executions, book);
    const trade = tradeOf(events, book);
    const disposition = disp?.disposition ?? null;
    const d = disposition ? plain(DISPOSITIONS, disposition) : null;

    let sentence = d?.plain ?? "No disposition was recorded for this book.";
    let next = "Nothing to do - the decision is recorded for the experiment comparison.";
    let tone: OutcomeTone = "neutral";

    if (disposition === "vetoed" && verdict) {
      const why = verdict.reasons[0]?.claim ?? verdict.abstain_reason ?? "reasons in the AI review step";
      sentence = `The AI reviewer vetoed this entry: ${why}. No order was sent, and risk checks were never asked.`;
      tone = "warn";
    } else if (disposition === "risk_rejected") {
      const block = blockingCode(risk);
      sentence = block
        ? `A risk check blocked the order: ${block.code} - ${block.text}`
        : "A risk check blocked the order (see the risk step for the code).";
      tone = "warn";
    } else if (disposition === "broker_rejected") {
      sentence = "Every internal check passed, but the simulated broker refused the order.";
      tone = "warn";
    } else if (disposition === "unknown" || execution?.status === "UNKNOWN") {
      sentence = "The order's outcome is not confirmed - it may or may not exist at the broker.";
      tone = "warn";
      next = "Review under Blotter > Orders: an UNKNOWN order blocks entries in this symbol until reconciliation resolves it.";
    } else if (disposition === "submitted" || execution) {
      if (trade) {
        const net = Number(trade.net_pnl);
        sentence = `Order filled (${execution?.filled_qty ?? trade.quantity} @ ${inr(trade.entry_price)}), later closed by ${plain(EXIT_REASONS, trade.exit_reason).label.toLowerCase()}: net ${inr(net, { sign: true })} after charges.`;
        tone = net > 0 ? "up" : net < 0 ? "down" : "neutral";
        next = "Nothing to do - see Blotter > Trades for the full record.";
      } else if (execution && execution.filled_qty > 0) {
        sentence = `Filled ${execution.filled_qty} @ ${inr(execution.avg_fill_price)}. The position is open, managed by its stop, target and time exit.`;
        tone = "accent";
        next = "Watch it under Command > Open positions; the stop order is resting at the broker.";
      } else if (execution) {
        sentence = `Order sent and ${plain(ORDER_STATUSES, execution.status).label.toLowerCase()}.`;
        tone = "accent";
        next = "Check the order's status under Blotter > Orders.";
      }
    } else if (disposition === "regime_gated" || disposition === "policy_skipped") {
      sentence = `${d?.label}: ${disp?.detail || d?.plain || ""}`.trim();
    } else if (disposition === "shadow_strategy") {
      sentence = "Shadow strategy - recorded for study only; no book trades it.";
    }

    return { book, disposition, label: d?.label ?? disposition ?? "—", sentence, next, tone };
  });
}

/** "What should I review or do next?" - derived, never invented. */
export function nextActions(lineage: Lineage, outcomes: BookOutcome[]): string[] {
  const actions = new Set<string>();
  for (const o of outcomes) if (o.next) actions.add(o.next);
  if (ofType(lineage.events, "OrderUnknown").length) {
    actions.add("An order's outcome is UNKNOWN - check System > Reconciliation; entries in the symbol stay blocked until it resolves.");
  }
  if (ofType(lineage.events, "AdvisorFallback").length) {
    actions.add("An AI advisor was unavailable - see AI desk for model health if this repeats.");
  }
  return [...actions];
}
