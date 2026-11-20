// The plain-language glossary. Every entry maps a real backend vocabulary value (dispositions,
// verdicts, risk outcomes, order statuses, exit reasons, reason codes, session states, books,
// strategies) to a short trader-facing label and one honest sentence. The original technical
// term always stays available (tooltip / Details) - nothing here renames what the engine said,
// it only explains it. All copy is derived from the code that emits each value:
// src/domain/events.py (Disposition), src/domain/types.py (Verdict, RiskOutcome, OrderStatus,
// ReasonCode, SessionState, IntentReason), src/config/experiment.yaml (books), src/strategies/*.

export interface Explain {
  /** Short label for badges and dense cells. */
  label: string;
  /** One plain sentence for tooltips and summaries. */
  plain: string;
}

function explain(label: string, plain: string): Explain {
  return { label, plain };
}

/** What a book did with one signal (the SignalDisposition's `disposition`). */
export const DISPOSITIONS: Record<string, Explain> = {
  submitted: explain(
    "Order sent",
    "This book acted on the signal: it passed the AI review (if any) and every risk check, and an order went to the simulated broker.",
  ),
  vetoed: explain(
    "AI veto",
    "This book's AI reviewer rejected the trade, so no order was sent. A veto is the AI's decision - not a risk-limit breach. Other books may have traded the same signal.",
  ),
  risk_rejected: explain(
    "Risk block",
    "A hard risk check (size, exposure, a limit or a kill switch) blocked the order. This is the rule engine, not the AI.",
  ),
  broker_rejected: explain(
    "Broker reject",
    "The order passed every internal check but the (simulated) broker refused it.",
  ),
  regime_gated: explain(
    "Regime gate",
    "The current NIFTY market regime (trending / ranging / volatile) is outside what this strategy is allowed to trade in, so the signal was recorded but not acted on.",
  ),
  policy_skipped: explain(
    "Policy skip",
    "The trade policy did not turn this signal into an entry - for example the book is long-only and this is a SELL, or there is no ATR to place a stop.",
  ),
  shadow_strategy: explain(
    "Shadow",
    "A recorded-only strategy: the signal is tracked so it can be studied, and is never traded.",
  ),
  unknown: explain(
    "Unknown",
    "The order's outcome is not confirmed yet. Reconciliation will adopt or reject it; entries in this symbol stay blocked until then.",
  ),
};

/** An advisor's verdict (books B and C; book A has no advisor). */
export const VERDICTS: Record<string, Explain> = {
  APPROVE: explain("Approved", "The AI reviewer saw nothing that made it reject the trade."),
  VETO: explain(
    "Vetoed",
    "The AI reviewer rejected this trade with enough confidence to act. The book's deterministic decision is overridden - no order is sent.",
  ),
  ABSTAIN: explain(
    "No opinion",
    "The AI reviewer was unsure, unavailable or degraded, so it declined to answer. An abstention is NOT an approval: the deterministic decision simply stands.",
  ),
};

/** The RiskEngine's outcome for one proposed order. */
export const RISK_OUTCOMES: Record<string, Explain> = {
  APPROVED: explain("Passed", "Every risk check passed at the requested size."),
  RESIZED: explain(
    "Passed smaller",
    "Risk checks passed, but the position was sized down so a limit is respected. The reason list shows which check resized it.",
  ),
  REJECTED: explain(
    "Blocked",
    "A risk check blocked the order outright. The blocking code and its observed-vs-limit numbers are listed with the decision.",
  ),
  HALTED: explain(
    "Halted",
    "A kill switch is latched for this book, so new entries are stopped until an operator resumes it with the typed phrase.",
  ),
};

/** Order lifecycle statuses (OrderStatus), including the resting-stop states. */
export const ORDER_STATUSES: Record<string, Explain> = {
  PENDING_NEW: explain("Pending", "Created and risk-checked; not yet handed to the broker."),
  SUBMITTED: explain("Sent", "With the broker; no confirmation back yet."),
  UNKNOWN: explain("Unknown", "The broker's answer was lost. Reconciliation will adopt or reject it."),
  OPEN: explain("Working", "Live at the broker, waiting to fill."),
  TRIGGER_PENDING: explain("Stop resting", "A protective stop order resting at the exchange until its trigger price is hit."),
  PARTIALLY_FILLED: explain("Part filled", "Some shares filled; the rest is still working."),
  PENDING_CANCEL: explain("Cancelling", "A cancel was sent; waiting for the broker to confirm it."),
  PENDING_MODIFY: explain("Modifying", "An amendment was sent; waiting for the broker to confirm it."),
  FILLED: explain("Filled", "Fully executed at the average fill price shown."),
  CANCELLED: explain("Cancelled", "Cancelled (any filled part is kept and shown separately)."),
  REJECTED: explain("Rejected", "The broker refused the order; the reason is on the order row."),
  EXPIRED: explain("Expired", "A DAY order that was still working when the session ended."),
};

/** How a position closed (TradeClosed.exit_reason - the IntentReason vocabulary). */
export const EXIT_REASONS: Record<string, Explain> = {
  stop: explain("Stop hit", "Price reached the protective stop; the position was exited to cap the loss."),
  target: explain("Target hit", "Price reached the profit target set at entry."),
  time: explain("Time exit", "Held for the maximum number of sessions (10) without hitting stop or target, so it was closed."),
  trail: explain("Trailing stop", "The trailing stop moved with the price and was then hit, locking in gains."),
  partial: explain("Partial exit", "Part of the position was booked; the rest keeps running."),
  square_off: explain("Squared off", "Closed at the end of the session because the product requires it."),
  flatten: explain("Flattened", "An operator (or the kill switch) flattened the book: every position was exited."),
  operator: explain("Operator exit", "Closed by an explicit operator action."),
  entry: explain("Entry", "The opening leg of the position (not an exit)."),
  unknown: explain("Unknown", "The exit could not be attributed to a rule; it is recorded as-is rather than guessed."),
};

/** Risk reason codes (ReasonCode) - the plain reading of each block/resize cause. */
export const REASON_CODES: Record<string, string> = {
  ORD_QTY_NONPOS: "Order quantity must be positive.",
  ORD_NOTIONAL_MAX: "Order value exceeds the per-order notional cap.",
  ORD_RISK_PER_TRADE: "Money at risk (entry to stop) exceeds the per-trade risk cap.",
  ORD_STOP_WRONG_SIDE: "The stop is on the wrong side of the entry price.",
  ORD_STOP_TOO_WIDE: "The stop is further away than allowed.",
  ORD_STOP_TOO_TIGHT: "The stop is closer than allowed (noise would stop it out).",
  ORD_RR_MIN: "Reward-to-risk is below the required minimum.",
  ORD_PRICE_COLLAR: "Price is outside the collar around the last quote (fat-finger guard).",
  ORD_ADV_PCT: "Order is too large a share of the stock's daily volume.",
  ORD_CIRCUIT_BAND: "Price is beyond the exchange circuit band for this stock.",
  ORD_TICK: "Price is not on the tick-size grid.",
  ORD_SHORT_NOT_ALLOWED: "The book is long-only; short entries are not allowed.",
  ORD_REDUCE_EXCEEDS_POS: "The reduce/close quantity is larger than the open position.",
  EVT_RESULTS_WINDOW: "A results announcement window is open for this company: new entries are held.",
  EVT_ADVERSE_MAJOR: "A major adverse announcement was classified for this company: new entries are held.",
  STR_HALTED: "This strategy is halted by a kill switch.",
  STR_NOT_VALIDATED: "The strategy is not on the validated list for live paper trading.",
  STR_CAPITAL_ALLOC: "The strategy's capital allocation is exhausted.",
  STR_DAILY_LOSS: "The strategy hit its daily loss cap.",
  STR_CONSEC_LOSSES: "The strategy hit its consecutive-loss cap.",
  STR_ORDER_RATE: "The strategy is sending orders too fast.",
  PF_MAX_POSITIONS: "The book already holds the maximum number of positions.",
  PF_DUPLICATE: "There is already an open position or working order for this symbol.",
  PF_REENTRY_SAME_DAY: "Re-entering the same symbol on the same day is not allowed.",
  PF_GROSS: "Gross exposure limit reached.",
  PF_NET: "Net exposure limit reached.",
  PF_SECTOR: "Sector exposure cap reached.",
  PF_HEAT: "Portfolio heat (total open risk) is at its cap.",
  PF_CASH: "Not enough free cash for this entry.",
  PF_DAILY_LOSS_MTM: "The book hit its daily mark-to-market loss limit.",
  PF_DRAWDOWN: "Equity drawdown from the running peak hit its limit.",
  PF_ENTRIES_PER_DAY: "Maximum entries per day reached.",
  SYS_KILL_GLOBAL: "The global kill switch is halting new entries.",
  SYS_KILL_BROKER: "A broker-scoped kill switch is halting new entries.",
  SYS_SESSION_CLOSED: "The market session is closed; entries are only allowed in the entry window.",
  SYS_HOLIDAY: "Today is an exchange holiday.",
  SYS_ENTRY_CUTOFF: "Past the entry cutoff time for today.",
  SYS_DATA_STALE: "Quotes are too old: entries are blocked until the feed is fresh again.",
  SYS_DATA_SIMULATED: "The data source is simulated/synthetic, which may not create orders outside demo.",
  SYS_MAX_OPEN_ORDERS: "Too many open orders at the broker.",
  SYS_ORDER_RATE: "Order rate limit reached (system-wide).",
  SYS_REJECT_STORM: "A storm of broker rejections tripped the circuit breaker.",
  SYS_UNKNOWN_ORDER: "An order for this symbol is in an UNKNOWN state; entries are blocked until it is resolved.",
  SYS_LLM_DEGRADED: "The AI advisor could not answer; the deterministic decision stands without it.",
  SYS_JOURNAL_NOT_DURABLE: "The event journal is not durable right now; trading is paused to stay auditable.",
  SYS_RECON_DRIFT: "The OMS and the broker disagree; entries are blocked until reconciliation passes.",
  SYS_CHECK_ERROR: "A risk check errored internally; the order is blocked (fail-closed).",
};

/** Session states (SessionState) - what the engine is doing in each. */
export const SESSION_STATES: Record<string, Explain> = {
  HOLIDAY: explain("Holiday", "The exchange is closed today; the engine serves the last recorded state."),
  PRE_OPEN: explain("Pre-open", "Loading the day's settled history, computing the NIFTY regime, rolling the risk day and reconciling the books. No entries yet."),
  OPEN: explain("Open", "The market is open. Quotes are streaming; the engine waits for the entry window."),
  ENTRY_WINDOW: explain("Entry window", "The one decision cycle of the day runs now (09:20-09:45 IST): signals, AI review, risk checks and entries."),
  MONITOR: explain("Monitoring", "Positions are monitored: stops, targets, time exits, risk ticks and reconciliation every 15 minutes."),
  CLOSE: explain("Close", "The market closed. Resting DAY orders are expired and a last risk tick runs per book."),
  REPORT: explain("Report", "Each book is marked to market and the daily report is written and sent."),
  EXIT: explain("Exited", "The session finished. Recorded state remains readable; start a new session to trade again."),
};

/** The paired books of the month-1 experiment (src/config/experiment.yaml). */
export const BOOKS: Record<string, Explain> = {
  A: explain("Book A - no AI", "The deterministic baseline: strategy signals go straight through the risk checks. No AI reviewer."),
  B: explain("Book B - typed AI veto", "Adds a local typed decision model as reviewer. It can only veto an entry (at a calibrated 60% veto threshold); it never changes price, size, stop or target."),
  C: explain("Book C - LLM veto", "Adds an LLM advisor as reviewer with the same veto-only power, so the three books isolate what each AI layer is worth."),
};

/** Strategy one-liners, from their actual conditions in src/strategies/. */
export const STRATEGIES: Record<string, Explain> = {
  momentum: explain("Momentum", "Buys when the trend is up but has room to run: a positive MACD histogram while RSI is still below 50. Sells are the mirror image (the long-only policy skips them)."),
  mean_reversion: explain("Mean reversion", "Buys when the close is at or below the lower Bollinger band (stronger when RSI is oversold), betting the price reverts to the middle of its range."),
  breakout: explain("Breakout (shadow)", "After a volatility squeeze, enters in the direction of the band the close broke. Shadow: recorded for study, never traded."),
  trend_following: explain("Trend following (shadow)", "Rides established trends using ADX and the directional indicators. Shadow: recorded for study, never traded."),
};

// -- news & events --------------------------------------------------------------------------------
// The news taxonomy from src/domain/types.py (NewsCategory/NewsDirection/NewsImpact/NewsHorizon),
// the feed-health states from GET /api/news/feeds, and what a confidence number is based on.

/** News event categories (NewsCategory). */
export const NEWS_CATEGORIES: Record<string, Explain> = {
  results_guidance: explain("Results / guidance", "Earnings, quarterly or annual results, guidance, or a profit warning."),
  contract_order: explain("Order win", "A contract, order or project win."),
  acquisition: explain("Acquisition / M&A", "An acquisition, merger or takeover - carries both opportunity and integration risk, so rules read it as mixed, never one-sided."),
  corporate_action: explain("Corporate action", "A dividend, bonus, split or buyback."),
  board_meeting: explain("Board meeting", "A board meeting is scheduled or concluded; its outcome is unknown until announced."),
  regulatory_legal: explain("Regulatory / legal", "Regulatory action, investigation, penalty or lawsuit."),
  credit_rating: explain("Credit rating", "A credit-rating action (upgrade, downgrade, affirmation)."),
  promoter_insider: explain("Promoter / insider", "Promoter or insider activity: stake changes, pledges, SAST disclosures."),
  macro_policy: explain("Macro / policy", "Macro-economic or policy news (RBI, inflation, budget) - not attributable to one company by rules alone."),
  market_wide: explain("Market-wide", "Market-wide coverage carrying no single-stock conclusion."),
  rumor: explain("Rumor", "Unconfirmed reporting. No direction is ever assigned to a rumor; it stays a watch item."),
  other: explain("Other", "Classified as other news."),
  unknown: explain("Unknown", "No rule or model reading supports a category - the item needs review."),
};

/** Expected-direction readings (NewsDirection). */
export const NEWS_DIRECTIONS: Record<string, Explain> = {
  bullish: explain("Bullish", "The reading expects this to support the share price."),
  bearish: explain("Bearish", "The reading expects this to weigh on the share price."),
  mixed: explain("Mixed", "The story carries both positive and negative effects; no one-sided call is made."),
  neutral: explain("Neutral", "No material price effect expected."),
  unknown: explain("Unknown", "No rule or model reading supports a direction - none is invented."),
};

/** Expected-impact readings (NewsImpact). */
export const NEWS_IMPACTS: Record<string, Explain> = {
  low: explain("Low impact", "Expected to matter little for the share price."),
  medium: explain("Medium impact", "Expected to matter moderately."),
  high: explain("High impact", "Expected to matter significantly."),
  unknown: explain("Unknown impact", "The reading does not claim an impact size - headline wording rarely proves magnitude."),
};

/** Claimed time horizons (NewsHorizon) - a label to be measured, not a promise. */
export const NEWS_HORIZONS: Record<string, Explain> = {
  intraday: explain("Intraday", "Effect expected within the session."),
  sessions_1_3: explain("1-3 sessions", "Effect expected over one to three sessions."),
  swing: explain("Swing", "Effect expected over more than three sessions."),
  unknown: explain("Unknown horizon", "The reading does not claim a horizon."),
};

/** Feed source types (the registry's `type`). */
export const NEWS_SOURCE_TYPES: Record<string, Explain> = {
  media_news: explain("Media", "Financial news media - secondary reporting, less trust than the exchange's own record."),
  macro: explain("Macro", "Official / institutional macro source."),
  exchange_filing: explain("Exchange filing", "Exchange-published filing - the primary source."),
};

/** What a confidence number is based on (NewsClassified.confidence_basis). */
export const CONFIDENCE_BASIS: Record<string, Explain> = {
  rule: explain("Rule prior", "A deterministic keyword-rule reading carries a fixed prior (0.35) - a label of 'the rules recognized this', NOT a calibrated probability."),
  model: explain("Model (uncalibrated)", "A model answer whose confidence has not been calibrated for this task."),
  model_calibrated: explain("Model (calibrated)", "A model answer with task calibration applied."),
  none: explain("No reading", "Neither a rule nor a model could read this item - it stays unclear."),
};

/** Feed-health states from GET /api/news/feeds. A failing feed is never shown as a quiet one. */
export const FEED_STATUS: Record<string, Explain> = {
  active: explain("Active", "Fetching on schedule and recently delivered items."),
  quiet: explain("Quiet", "Working, but no items for 48+ hours - possibly a slow news period, possibly early failure; the last success time tells you which."),
  failing: explain("Failing", "The last fetch failed. Coverage since the last success is UNKNOWN - not 'no news'."),
  empty: explain("No items yet", "Enabled and verified, but nothing collected yet - the first poll has not returned an item."),
  disabled: explain("Disabled", "Turned off in the registry; nothing is fetched."),
  unverified: explain("Unverified", "Enabled but never passed verification - deliberately NOT fetched. Run scripts/verify_news_feeds.py, then set verified in the registry."),
  owned: explain("Owned elsewhere", "Collected by another component (the announcement ingestor); the news collector never double-fetches it."),
  invalid: explain("Registry invalid", "The feed registry could not be read; the error is shown with the row."),
};

/** The status chip of one reading: what the system decided to do about it. */
export const READING_STATUS: Record<string, Explain> = {
  catalyst_watch: explain("Catalyst watch", "A possible price catalyst: visible for research and monitoring. This is NOT an order - traded signals appear under Decisions after AI review and risk checks."),
  unclear: explain("Unclear - needs review", "No rule or model reading supports a conclusion. Nothing is guessed; a human should look at the original item."),
  not_relevant: explain("Not relevant", "The reading judged this routine or not company-specific; it stays in the archive."),
};

// -- market data freshness -------------------------------------------------------------------------

export type Freshness = "live" | "delayed" | "stale" | "none";

export interface FreshnessReading {
  state: Freshness;
  label: string;
  detail: string;
}

/** The same thresholds the top bar already used: fresh under a minute, stale past 30. */
export const FRESH_LIMIT_S = 60;
export const STALE_LIMIT_S = 1800;

export function dataFreshness(ageSeconds: number | null | undefined): FreshnessReading {
  if (ageSeconds === null || ageSeconds === undefined || !Number.isFinite(ageSeconds)) {
    return { state: "none", label: "NO DATA", detail: "No quotes in memory yet - start a session or wait for the first poll." };
  }
  if (ageSeconds <= FRESH_LIMIT_S) {
    return { state: "live", label: "LIVE", detail: "The newest quote is under a minute old." };
  }
  if (ageSeconds <= STALE_LIMIT_S) {
    return { state: "delayed", label: "DELAYED", detail: "Quotes are arriving but not every poll; newer data is expected shortly." };
  }
  return { state: "stale", label: "STALE", detail: "No quote for over 30 minutes. Entries are blocked while data is this old (SYS_DATA_STALE)." };
}

/** The plain reading of any backend term, or the term itself when the glossary has no entry. */
export function plain(table: Record<string, Explain>, key: string): Explain {
  return table[key] ?? explain(key, "");
}
