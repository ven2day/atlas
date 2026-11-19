// The trader-facing reading of a lineage: five stages, per-book outcomes and next actions, all
// derived from recorded events. Fixtures mirror the shapes the backend actually emits
// (src/domain/events.py payloads via GET /api/decisions/{id}) - no live data, no network.

import { describe, expect, it } from "vitest";

import type { Schemas } from "../api/client";
import { bookOutcomes, deriveStages, nextActions } from "./decisionSummary";
import type { LineageEvent } from "./lineage";

type Lineage = Schemas["Lineage"];
type Execution = Schemas["Execution"];

const TS = "2026-10-05T04:05:00+00:00";
let seq = 0;

function ev(type: string, data: Record<string, unknown>, book: string | null = null): LineageEvent {
  seq += 1;
  return { seq, ts: TS, type, decision_id: "d1", book_id: book, source: "test", data };
}

const SIGNAL = {
  signal_id: "s1",
  strategy: "momentum",
  side: "BUY",
  instrument_key: "NSE:EQ:INFY",
  agreement_score: 0.8,
  bar_date: "2026-10-04",
  generated_at: TS,
  is_shadow: false,
  reasons: [
    { name: "rsi_14", value: 42.1, detail: "below 50: room to run" },
    { name: "macd_hist", value: 1.7, detail: "positive: bullish momentum" },
  ],
};

function risk(outcome: string, reasons: { code: string; outcome: string; message?: string }[] = []) {
  return {
    outcome,
    kind: "open",
    qty_requested: 59,
    qty_approved: outcome === "REJECTED" || outcome === "HALTED" ? 0 : 59,
    notional: "90000",
    risk_amount: "9000",
    risk_pct_equity: 0.009,
    ref_price: "1530.00",
    stop_price: "1480.00",
    target_price: "1680.00",
    limits_hash: "abc123",
    signal_id: "s1",
    reasons: reasons.map((r) => ({ level: "order", observed: null, limit: null, max_qty: null, message: r.message ?? "", ...r })),
  };
}

function execution(overrides: Partial<Execution> = {}): Execution {
  return {
    book_id: "A",
    client_order_id: "co1",
    kind: "open",
    side: "BUY",
    quantity: 59,
    filled_qty: 59,
    status: "FILLED",
    decision_price: "1525.00",
    arrival_price: "1530.00",
    avg_fill_price: "1530.50",
    slippage_vs_decision_bps: 3.6,
    slippage_vs_arrival_bps: 0.3,
    charges: "42.10",
    ...overrides,
  };
}

function lineage(events: LineageEvent[], executions: Execution[] = [], exits: string[] = []): Lineage {
  return { decision_id: "d1", exit_decision_ids: exits, events, regime: "trending_up", executions };
}

const DISPO = (book: string, disposition: string, detail = "") =>
  ev("SignalDisposition", { signal_id: "s1", strategy: "momentum", disposition, detail }, book);

describe("an executed and closed decision", () => {
  const l = lineage(
    [
      ev("SignalGenerated", { signal: SIGNAL }),
      ev("AdvisorRequested", { advisor: "typed_veto" }, "B"),
      ev("AdvisorVerdict", { advisor: "typed_veto", verdict: "APPROVE", confidence: 0.8, reasons: [], abstain_reason: null }, "B"),
      ev("RiskDecision", risk("APPROVED"), "A"),
      ev("OrderSubmitted", { order: { client_order_id: "co1" } }, "A"),
      ev("FillReceived", { fill: { client_order_id: "co1", price: "1530.50", quantity: 59, charges: "42.10", ts: TS } }, "A"),
      DISPO("A", "submitted"),
      DISPO("B", "submitted"),
      ev("TradeClosed", {
        trade_id: "t1", exit_reason: "target", entry_price: "1530.50", exit_price: "1680.00",
        quantity: 59, gross_pnl: "8820.50", charges: "90.00", net_pnl: "8730.50",
        entry_ts: TS, exit_ts: TS, exit_decision_id: "d2",
      }, "A"),
    ],
    [execution()],
    ["d2"],
  );
  const stages = deriveStages(l);

  it("walks all five stages to completion", () => {
    expect(stages.map((s) => s.id)).toEqual(["signal", "ai", "risk", "order", "exit"]);
    expect(stages.map((s) => s.status)).toEqual(["done", "done", "done", "done", "done"]);
  });

  it("names the strategy, side and symbol in the signal headline", () => {
    expect(stages[0]!.headline).toMatch(/momentum proposed BUY INFY/);
    expect(stages[0]!.details.join(" ")).toMatch(/room to run/);
  });

  it("reports the fill as an actual execution, not a plan", () => {
    expect(stages[3]!.headline).toMatch(/Filled 59 of 59/);
    expect(stages[3]!.details.join(" ")).toMatch(/Planned prices are not fills/);
  });

  it("explains the exit with its reason and net P&L", () => {
    expect(stages[4]!.headline).toMatch(/Target hit/);
    expect(stages[4]!.headline).toMatch(/8,730\.50/);
  });

  it("gives one outcome line per book", () => {
    const outcomes = bookOutcomes(l);
    expect(outcomes.map((o) => o.book)).toEqual(["A", "B"]);
    expect(outcomes[0]!.sentence).toMatch(/closed by target hit/i);
    expect(outcomes[0]!.tone).toBe("up");
    expect(outcomes[0]!.disposition).toBe("submitted");
  });
});

describe("an AI-vetoed decision", () => {
  const l = lineage([
    ev("SignalGenerated", { signal: SIGNAL }),
    ev("AdvisorRequested", { advisor: "typed_veto" }, "B"),
    ev("AdvisorVerdict", {
      advisor: "typed_veto", verdict: "VETO", confidence: 0.9,
      reasons: [{ claim: "results week risk", evidence_ref: "signal.instrument_key" }], abstain_reason: null,
    }, "B"),
    DISPO("A", "submitted"),
    DISPO("B", "vetoed", "advisor veto"),
    ev("RiskDecision", risk("APPROVED"), "A"),
  ], [execution()]);
  const stages = deriveStages(l);

  it("blocks the AI stage and says the veto is not a risk breach", () => {
    expect(stages[1]!.status).toBe("blocked");
    expect(stages[1]!.headline).toMatch(/Book B's AI reviewer vetoed/);
    const outcomes = bookOutcomes(l);
    const b = outcomes.find((o) => o.book === "B");
    expect(b?.sentence).toMatch(/AI reviewer vetoed/);
    expect(b?.sentence).toMatch(/risk checks were never asked/i);
    expect(b?.tone).toBe("warn");
  });

  it("keeps the risk stage clear for the book that did proceed", () => {
    expect(stages[2]!.status).toBe("done");
  });
});

describe("a risk-rejected decision", () => {
  const l = lineage([
    ev("SignalGenerated", { signal: SIGNAL }),
    ev("RiskDecision", risk("REJECTED", [{ code: "PF_SECTOR", outcome: "block", message: "sector cap" }]), "A"),
    DISPO("A", "risk_rejected", "blocked by PF_SECTOR"),
  ]);
  const stages = deriveStages(l);

  it("blocks the risk stage with the code AND its plain reading", () => {
    expect(stages[2]!.status).toBe("blocked");
    expect(stages[2]!.headline).toMatch(/PF_SECTOR/);
    expect(stages[2]!.headline).toMatch(/Sector exposure cap reached/);
  });

  it("never sends an order and never opens a position", () => {
    expect(stages[3]!.status).toBe("skipped");
    expect(stages[4]!.status).toBe("pending");
  });

  it("distinguishes a risk block from an AI veto in the book outcome", () => {
    const [a] = bookOutcomes(l);
    expect(a?.label).toBe("Risk block");
    expect(a?.sentence).toMatch(/risk check blocked/i);
  });
});

describe("an abstaining AI is not an approval", () => {
  const l = lineage([
    ev("SignalGenerated", { signal: SIGNAL }),
    ev("AdvisorRequested", { advisor: "llm_veto" }, "C"),
    ev("AdvisorVerdict", { advisor: "llm_veto", verdict: "ABSTAIN", confidence: null, reasons: [], abstain_reason: "llm_role_veto_disabled" }, "C"),
    ev("AdvisorFallback", { advisor: "llm_veto", reason: "llm_role_veto_disabled", error: null }, "C"),
    DISPO("C", "submitted"),
    ev("RiskDecision", risk("APPROVED"), "C"),
  ], [execution({ book_id: "C" })]);

  it("labels the abstention as 'no opinion' and still shows the trade proceeded", () => {
    const stages = deriveStages(l);
    expect(stages[1]!.status).toBe("done");
    expect(stages[1]!.headline).toMatch(/no opinion/i);
    expect(stages[1]!.details.join(" ")).toMatch(/NOT an approval|never changes price/i);
  });

  it("flags the degraded advisor in next actions", () => {
    expect(nextActions(l, bookOutcomes(l)).join(" ")).toMatch(/AI desk/);
  });
});

describe("an open position", () => {
  const l = lineage(
    [
      ev("SignalGenerated", { signal: SIGNAL }),
      ev("RiskDecision", risk("APPROVED"), "A"),
      DISPO("A", "submitted"),
    ],
    [execution()],
  );

  it("shows the exit stage as active with the planned stop and target", () => {
    const stages = deriveStages(l);
    expect(stages[4]!.status).toBe("active");
    expect(stages[4]!.details.join(" ")).toMatch(/Stop: ₹1,480\.00/);
    expect(stages[4]!.details.join(" ")).toMatch(/Target: ₹1,680\.00/);
  });

  it("tells the trader where to watch it", () => {
    const [a] = bookOutcomes(l);
    expect(a?.tone).toBe("accent");
    expect(a?.next).toMatch(/Open positions/);
  });
});

describe("a shadow-strategy signal", () => {
  const l = lineage([ev("SignalGenerated", { signal: { ...SIGNAL, strategy: "breakout", is_shadow: true } }), DISPO("A", "shadow_strategy", "shadow strategy")]);

  it("is marked as recorded-only at the signal stage and goes no further", () => {
    const stages = deriveStages(l);
    expect(stages[0]!.status).toBe("skipped");
    expect(stages[0]!.headline).toMatch(/never traded/);
    expect(stages[2]!.status).toBe("skipped");
    expect(stages[3]!.status).toBe("skipped");
  });
});

describe("an unknown order", () => {
  const l = lineage(
    [ev("SignalGenerated", { signal: SIGNAL }), DISPO("A", "unknown"), ev("OrderUnknown", { client_order_id: "co1" }, "A")],
    [execution({ status: "UNKNOWN", filled_qty: 0, avg_fill_price: null })],
  );

  it("points the trader at reconciliation", () => {
    expect(nextActions(l, bookOutcomes(l)).join(" ")).toMatch(/Reconciliation/);
    const outcomes = bookOutcomes(l);
    expect(outcomes[0]!.tone).toBe("warn");
  });
});

describe("a decision with no AI reviewer at all (book A alone)", () => {
  const l = lineage([
    ev("SignalGenerated", { signal: SIGNAL }),
    ev("RiskDecision", risk("APPROVED"), "A"),
    DISPO("A", "submitted"),
  ], [execution()]);

  it("says the AI step was skipped by design, not failed", () => {
    const stages = deriveStages(l);
    expect(stages[1]!.status).toBe("skipped");
    expect(stages[1]!.headline).toMatch(/no AI reviewer/i);
  });
});
