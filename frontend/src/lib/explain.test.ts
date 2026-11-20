// The glossary must cover every value the backend can emit - an unmapped status would render as
// a raw code with no explanation, which is exactly what the redesign removes.

import { describe, expect, it } from "vitest";

import {
  BOOKS,
  CONFIDENCE_BASIS,
  DISPOSITIONS,
  EXIT_REASONS,
  FEED_STATUS,
  NEWS_CATEGORIES,
  NEWS_DIRECTIONS,
  NEWS_HORIZONS,
  NEWS_IMPACTS,
  NEWS_SOURCE_TYPES,
  ORDER_STATUSES,
  READING_STATUS,
  REASON_CODES,
  RISK_OUTCOMES,
  SESSION_STATES,
  STRATEGIES,
  VERDICTS,
  dataFreshness,
  plain,
} from "./explain";

// Copied from the backend vocabularies (src/domain/types.py, src/domain/events.py) so a new
// enum value without a glossary entry fails here rather than rendering raw in the console.
const DISPOSITION_VALUES = [
  "shadow_strategy",
  "regime_gated",
  "policy_skipped",
  "vetoed",
  "risk_rejected",
  "submitted",
  "broker_rejected",
  "unknown",
];
const VERDICT_VALUES = ["APPROVE", "VETO", "ABSTAIN"];
const RISK_OUTCOME_VALUES = ["APPROVED", "RESIZED", "REJECTED", "HALTED"];
const ORDER_STATUS_VALUES = [
  "PENDING_NEW",
  "SUBMITTED",
  "UNKNOWN",
  "OPEN",
  "TRIGGER_PENDING",
  "PARTIALLY_FILLED",
  "PENDING_CANCEL",
  "PENDING_MODIFY",
  "FILLED",
  "CANCELLED",
  "REJECTED",
  "EXPIRED",
];
const EXIT_REASON_VALUES = ["entry", "stop", "target", "trail", "partial", "time", "square_off", "flatten", "operator", "unknown"];
const SESSION_STATE_VALUES = ["HOLIDAY", "PRE_OPEN", "OPEN", "ENTRY_WINDOW", "MONITOR", "CLOSE", "REPORT", "EXIT"];
const REASON_CODE_VALUES = [
  "ORD_QTY_NONPOS", "ORD_NOTIONAL_MAX", "ORD_RISK_PER_TRADE", "ORD_STOP_WRONG_SIDE", "ORD_STOP_TOO_WIDE",
  "ORD_STOP_TOO_TIGHT", "ORD_RR_MIN", "ORD_PRICE_COLLAR", "ORD_ADV_PCT", "ORD_CIRCUIT_BAND", "ORD_TICK",
  "ORD_SHORT_NOT_ALLOWED", "ORD_REDUCE_EXCEEDS_POS", "EVT_RESULTS_WINDOW", "EVT_ADVERSE_MAJOR",
  "STR_HALTED", "STR_NOT_VALIDATED", "STR_CAPITAL_ALLOC", "STR_DAILY_LOSS", "STR_CONSEC_LOSSES",
  "STR_ORDER_RATE", "PF_MAX_POSITIONS", "PF_DUPLICATE", "PF_REENTRY_SAME_DAY", "PF_GROSS", "PF_NET",
  "PF_SECTOR", "PF_HEAT", "PF_CASH", "PF_DAILY_LOSS_MTM", "PF_DRAWDOWN", "PF_ENTRIES_PER_DAY",
  "SYS_KILL_GLOBAL", "SYS_KILL_BROKER", "SYS_SESSION_CLOSED", "SYS_HOLIDAY", "SYS_ENTRY_CUTOFF",
  "SYS_DATA_STALE", "SYS_DATA_SIMULATED", "SYS_MAX_OPEN_ORDERS", "SYS_ORDER_RATE", "SYS_REJECT_STORM",
  "SYS_UNKNOWN_ORDER", "SYS_LLM_DEGRADED", "SYS_JOURNAL_NOT_DURABLE", "SYS_RECON_DRIFT", "SYS_CHECK_ERROR",
];

describe("the glossary covers every backend value", () => {
  it("dispositions", () => {
    for (const v of DISPOSITION_VALUES) expect(DISPOSITIONS[v], v).toBeDefined();
  });
  it("verdicts", () => {
    for (const v of VERDICT_VALUES) expect(VERDICTS[v], v).toBeDefined();
  });
  it("risk outcomes", () => {
    for (const v of RISK_OUTCOME_VALUES) expect(RISK_OUTCOMES[v], v).toBeDefined();
  });
  it("order statuses", () => {
    for (const v of ORDER_STATUS_VALUES) expect(ORDER_STATUSES[v], v).toBeDefined();
  });
  it("exit reasons", () => {
    for (const v of EXIT_REASON_VALUES) expect(EXIT_REASONS[v], v).toBeDefined();
  });
  it("session states", () => {
    for (const v of SESSION_STATE_VALUES) expect(SESSION_STATES[v], v).toBeDefined();
  });
  it("every risk reason code", () => {
    for (const v of REASON_CODE_VALUES) expect(REASON_CODES[v], v).toBeTruthy();
  });
  it("the experiment's books and strategies", () => {
    for (const b of ["A", "B", "C"]) expect(BOOKS[b], b).toBeDefined();
    for (const s of ["momentum", "mean_reversion", "breakout", "trend_following"]) {
      expect(STRATEGIES[s], s).toBeDefined();
    }
  });
  it("the news taxonomy (NewsCategory/Direction/Impact/Horizon)", () => {
    const categories = [
      "results_guidance", "contract_order", "acquisition", "corporate_action", "board_meeting",
      "regulatory_legal", "credit_rating", "promoter_insider", "macro_policy", "market_wide",
      "rumor", "other", "unknown",
    ];
    for (const v of categories) expect(NEWS_CATEGORIES[v], v).toBeDefined();
    for (const v of ["bullish", "bearish", "mixed", "neutral", "unknown"]) {
      expect(NEWS_DIRECTIONS[v], v).toBeDefined();
    }
    for (const v of ["low", "medium", "high", "unknown"]) expect(NEWS_IMPACTS[v], v).toBeDefined();
    for (const v of ["intraday", "sessions_1_3", "swing", "unknown"]) {
      expect(NEWS_HORIZONS[v], v).toBeDefined();
    }
    for (const v of ["media_news", "macro", "exchange_filing"]) {
      expect(NEWS_SOURCE_TYPES[v], v).toBeDefined();
    }
  });
  it("feed health states and reading statuses", () => {
    for (const v of ["active", "quiet", "failing", "empty", "disabled", "unverified", "owned", "invalid"]) {
      expect(FEED_STATUS[v], v).toBeDefined();
    }
    for (const v of ["catalyst_watch", "unclear", "not_relevant"]) {
      expect(READING_STATUS[v], v).toBeDefined();
    }
    for (const v of ["rule", "model", "model_calibrated", "none"]) {
      expect(CONFIDENCE_BASIS[v], v).toBeDefined();
    }
  });
  it("every entry has a label and a plain sentence", () => {
    for (const table of [
      DISPOSITIONS, VERDICTS, RISK_OUTCOMES, ORDER_STATUSES, EXIT_REASONS, SESSION_STATES,
      BOOKS, STRATEGIES, NEWS_CATEGORIES, NEWS_DIRECTIONS, NEWS_IMPACTS, NEWS_HORIZONS,
      NEWS_SOURCE_TYPES, CONFIDENCE_BASIS, FEED_STATUS, READING_STATUS,
    ]) {
      for (const [key, entry] of Object.entries(table)) {
        expect(entry.label, key).toBeTruthy();
        expect(entry.plain.length, key).toBeGreaterThan(20);
      }
    }
  });
});

describe("abstention is never described as approval", () => {
  it("says so explicitly", () => {
    expect(VERDICTS.ABSTAIN!.plain).toMatch(/NOT an approval/i);
    expect(DISPOSITIONS.vetoed!.plain).toMatch(/not a risk-limit breach/i);
    expect(DISPOSITIONS.risk_rejected!.plain).toMatch(/not the AI/i);
  });
});

describe("data freshness", () => {
  it("reads a fresh quote as live", () => {
    expect(dataFreshness(12).state).toBe("live");
    expect(dataFreshness(12).label).toBe("LIVE");
  });
  it("reads an old-but-arriving quote as delayed", () => {
    expect(dataFreshness(300).state).toBe("delayed");
  });
  it("reads a 30-minute-old quote as stale, naming the risk block", () => {
    const reading = dataFreshness(1801);
    expect(reading.state).toBe("stale");
    expect(reading.detail).toMatch(/SYS_DATA_STALE/);
  });
  it("reads no quotes as no data, not as stale", () => {
    expect(dataFreshness(null).state).toBe("none");
    expect(dataFreshness(undefined).state).toBe("none");
  });
});

describe("unknown terms pass through unchanged", () => {
  it("falls back to the raw value", () => {
    expect(plain(DISPOSITIONS, "some_future_code").label).toBe("some_future_code");
  });
});
