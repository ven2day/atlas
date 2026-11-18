"""
The web API's response models (plan M9.2). They are the contract: FastAPI turns them into the
OpenAPI document that ``frontend/src/api/types.gen.ts`` is generated from (M9.5).

Money and prices are exact decimals, serialised as strings; timestamps are timezone-aware.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, StrictBool, StringConstraints, model_validator


class ApiModel(BaseModel):
    # Responses always carry every field, defaults included: say so in the schema, so the
    # generated TypeScript types are exact (request bodies keep their defaults optional).
    model_config = ConfigDict(frozen=True, json_schema_serialization_defaults_required=True)


# -- summary ---------------------------------------------------------------------------------


class SessionInfo(ApiModel):
    date: date
    state: str


class BookSummary(ApiModel):
    book_id: str
    advisor: str
    equity: Decimal | None
    cash: Decimal | None
    unrealized_pnl: Decimal | None
    day_pnl: Decimal | None
    valued_at: datetime | None
    valuation: Literal["live", "last_mark", "none"]
    realized_pnl_today: Decimal
    open_positions: int
    open_orders: int
    trades_today: int
    kill_switch: str  # the book's global switch: ARMED / HALT_NEW / FLATTEN
    day_return_pct: float | None  # day P&L over start-of-day equity, in percent


class ScheduleStep(ApiModel):
    state: str
    at: datetime


class Summary(ApiModel):
    environment: str
    mode: Literal["paper"] = "paper"  # the v2 engine has no broker path
    demo: bool
    running: bool
    experiment: str
    session: SessionInfo | None
    market_open: bool
    now: datetime
    last_seq: int
    books: list[BookSummary]
    schedule: list[ScheduleStep]  # when each state after PRE_OPEN starts (IST)


# -- blotter ---------------------------------------------------------------------------------


class PositionRow(ApiModel):
    book_id: str
    instrument_key: str
    symbol: str
    product: str
    quantity: int
    avg_price: Decimal | None
    realized_pnl: Decimal
    mark: Decimal | None
    unrealized_pnl: Decimal | None
    updated_ts: datetime
    strategy: str | None  # from the exit manager: who opened it, and its exits
    entry_decision_id: str | None
    stop_price: Decimal | None  # the resting protective stop (else the planned one)
    target_price: Decimal | None
    entered_on: date | None
    held_sessions: int | None


class OrderRow(ApiModel):
    client_order_id: str
    book_id: str
    decision_id: str
    intent_id: str
    instrument_key: str
    symbol: str
    strategy: str
    side: str
    product: str
    order_type: str
    kind: str
    reason: str
    quantity: int
    status: str
    filled_qty: int
    avg_fill_price: Decimal | None
    broker_order_id: str | None
    last_message: str | None
    ist_date: date
    updated_ts: datetime


class FillRow(ApiModel):
    fill_id: str
    client_order_id: str
    book_id: str
    decision_id: str
    instrument_key: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    charges: Decimal
    ts: datetime


class TradeRow(ApiModel):
    trade_id: str
    book_id: str
    decision_id: str
    exit_decision_id: str | None
    instrument_key: str
    symbol: str
    strategy: str
    side: str
    quantity: int
    entry_price: Decimal
    exit_price: Decimal
    entry_ts: datetime
    exit_ts: datetime
    gross_pnl: Decimal
    charges: Decimal
    net_pnl: Decimal
    exit_reason: str


# -- decisions ---------------------------------------------------------------------------------


class DecisionRow(ApiModel):
    """What happened to one signal in one book (a ``SignalDisposition``)."""

    seq: int
    ts: datetime
    decision_id: str
    signal_id: str
    book_id: str
    instrument_key: str
    symbol: str
    strategy: str
    disposition: str
    detail: str
    client_order_id: str | None


class LineageEvent(ApiModel):
    seq: int
    ts: datetime
    type: str
    decision_id: str | None
    book_id: str | None
    source: str
    data: dict[str, Any]


class Execution(ApiModel):
    """One order of the decision. Slippage is adverse-positive, in bps, vs the decision price
    (the signal bar's close; a stop's trigger) and vs the arrival price (the quote at submission
    - none for a resting stop, whose submission can be sessions before it triggers)."""

    book_id: str
    client_order_id: str
    kind: str
    side: str
    quantity: int
    filled_qty: int
    status: str
    decision_price: Decimal | None
    arrival_price: Decimal | None
    avg_fill_price: Decimal | None
    slippage_vs_decision_bps: float | None
    slippage_vs_arrival_bps: float | None
    charges: Decimal


class Lineage(ApiModel):
    """Everything recorded under a decision, plus its exits' decisions, in ``seq`` order."""

    decision_id: str
    exit_decision_ids: list[str]
    events: list[LineageEvent]
    regime: str | None  # the regime label of the decision's session
    executions: list[Execution]  # per order: prices, slippage, charges (server-computed)


# -- risk ------------------------------------------------------------------------------------


class KillSwitchRow(ApiModel):
    scope: str
    name: str
    state: str
    reason: str
    actor: str
    since: datetime


class Utilisation(ApiModel):
    key: str  # the reason code the limit is enforced with
    label: str
    used: Decimal
    limit: Decimal
    unit: Literal["inr", "ratio", "count"]
    fraction: float | None


class EventBlockRow(ApiModel):
    instrument_key: str
    symbol: str
    code: str
    start: date
    end: date
    event_id: str
    reason: str


class BookRisk(ApiModel):
    book_id: str
    kill_switches: list[KillSwitchRow]
    daily_state: dict[str, Any] | None
    rejections_today: dict[str, int]  # reason code -> blocked checks today
    equity: Decimal | None
    valuation: Literal["live", "last_mark", "none"]
    utilisation: list[Utilisation]  # the RiskEngine's own limit definitions
    sectors: list[Utilisation]  # live only (needs marks): exposure per sector vs its cap
    event_blocks: list[EventBlockRow]


class RiskView(ApiModel):
    limits_hash: str
    limits: dict[str, Any]
    books: list[BookRisk]


# -- books (the paired experiment) -------------------------------------------------------------


class VetoPrecision(ApiModel):
    vetoes: int
    settled: int
    correct: int
    precision: float | None
    ci95: tuple[float, float] | None
    loss_avoided_inr: float
    gain_forgone_inr: float


class BookComparison(ApiModel):
    book_id: str
    advisor: str
    capital: Decimal
    equity: Decimal | None
    valuation: Literal["live", "last_mark", "none"]
    return_pct: float | None
    realized_net_to_date: Decimal
    closed_trades: int
    win_rate: float | None
    ai_spend_inr_to_date: Decimal
    vs: str | None  # the baseline book (None for the baseline itself)
    equity_difference_inr: Decimal | None
    net_ai_value_inr: Decimal | None
    veto_precision: VetoPrecision


class BooksView(ApiModel):
    experiment: str
    baseline: str
    books: list[BookComparison]


# -- AI ----------------------------------------------------------------------------------------


class LLMCallRow(ApiModel):
    seq: int
    ts: datetime
    decision_id: str | None
    book_id: str | None
    role: str
    provider: str
    model: str
    attempt: int
    prompt_version: str
    outcome: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    cost_usd: Decimal | None
    cost_inr: Decimal | None
    cache_hit: bool


class SpendRow(ApiModel):
    key: str
    calls: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal
    cost_inr: Decimal


class SpendView(ApiModel):
    group_by: Literal["role", "book", "day", "model"]
    rows: list[SpendRow]
    total_inr: Decimal


class ModelHealth(ApiModel):
    model: str
    calls_today: int
    errors_today: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    last_outcome: str | None
    last_call: datetime | None


class RoleModels(ApiModel):
    role: str
    enabled: bool
    chain: list[str]  # provider:model, first = primary
    models: list[ModelHealth]


class DecisionModelStats(ApiModel):
    task: str
    model: str
    checkpoint: str
    calls: int
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    escalation_rate: float | None
    calibrated_rate: float | None
    shadow_calls: int
    outcomes: dict[str, int]


# -- market, events, reports, system, config ------------------------------------------------------


class BarRow(ApiModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: int


class BarMarker(ApiModel):
    date: date
    kind: Literal["entry", "exit", "event"]
    text: str


class Bars(ApiModel):
    symbol: str
    instrument_key: str
    adjusted: bool
    source: Literal["engine", "tape", "none"]
    bars: list[BarRow]
    markers: list[BarMarker]  # this instrument's fills and announcements


class TypedEventRow(ApiModel):
    event_id: str
    instrument_key: str
    symbol: str
    published_at: datetime
    relevant: bool
    announcement_type: str | None
    direction: str | None
    materiality: str | None
    data: dict[str, Any]


class ReconcileRow(ApiModel):
    book_id: str
    scope: str
    in_sync: bool
    diffs: list[str]
    ts: datetime


class SystemView(ApiModel):
    environment: str
    running: bool
    pid: int
    tasks: list[str]
    uptime_s: float | None
    loop_lag_ms: float | None
    last_heartbeat: datetime | None
    last_loop_lag: datetime | None
    quote_age_s: dict[str, float]  # data source -> age of its freshest quote
    store_bytes: int
    store_path: str
    last_seq: int
    schema_version: int
    last_reconcile: list[ReconcileRow]
    versions: dict[str, str]
    prompts: dict[str, str]  # template -> its identity (name_vN@sha12)


class ConfigView(ApiModel):
    """Read-only and redacted: names and switches, never a key, URL credential or token."""

    environment: str
    mode: Literal["paper"] = "paper"
    execution_mode_requested: str
    execution_mode_note: str | None
    experiment: dict[str, Any]
    llm_roles: dict[str, list[str]]
    decision_models: dict[str, Any]
    announcements_enabled: bool
    telegram_configured: bool
    limits_hash: str
    read_only: bool
    llm_budget_daily_inr: Decimal  # 0 = no daily cap


# -- controls (plan M9.3) ----------------------------------------------------------------------


class ControlResult(ApiModel):
    action: str
    outcome: Literal["applied", "no_change", "refused"]
    books: list[str]
    detail: str


class StrictBody(BaseModel):
    """Request bodies: unknown fields and loose types (``"false"`` for a bool) are a 422."""

    model_config = ConfigDict(extra="forbid", frozen=True)


BookId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9]{1,16}$")]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=200)]


class SessionStartBody(StrictBody):
    demo: StrictBool = False


class SessionStopBody(StrictBody):
    pass


class HaltBody(StrictBody):
    reason: Reason
    book: BookId | None = None  # None = every book


class _Confirmed(StrictBody):
    """A destructive control: ``confirm`` must be literally ``true`` and the phrase typed."""

    confirm: StrictBool
    reason: Reason
    book: BookId | None = None

    @model_validator(mode="after")
    def _confirmed(self) -> _Confirmed:
        if self.confirm is not True:
            raise ValueError("confirm must be true")
        return self


class ResumeBody(_Confirmed):
    phrase: Literal["RESUME"]
    scope: Literal["global", "strategy", "broker"] = "global"
    name: Annotated[str, StringConstraints(pattern=r"^[a-z_]{1,32}$")] | None = None


class FlattenBody(_Confirmed):
    phrase: Literal["FLATTEN"]


# -- the WebSocket stream (plan M9.4; in the OpenAPI components for the generated types) --------

StreamTopic = Literal["orders", "positions", "decisions", "risk", "ai", "market", "system",
                      "summary", "quotes", "events"]  # fmt: skip


class StreamSubscribe(StrictBody):
    """Client → server: (re)subscribe; stored events after ``since_seq`` are replayed first."""

    subscribe: list[StreamTopic]
    since_seq: int = 0


class StreamEnvelope(ApiModel):
    """Server → client. ``seq`` is set for stored events; ``type`` is the event type, or one of
    ``subscribed``, ``heartbeat``, ``resync``, ``summary``, ``quotes``, ``stopped``, ``error``."""

    v: Literal[1]
    seq: int | None
    type: str
    topic: str
    ts: datetime
    decision_id: str | None
    book_id: str | None
    data: dict[str, Any]


class ErrorBody(ApiModel):
    """Every error response: generic, never echoing input or exception text."""

    error: str
    fields: list[str] | None = None  # 422 only: the invalid fields' paths


# -- screens (plan M10.4) ---------------------------------------------------------------------------


class AlertRow(ApiModel):
    seq: int
    ts: datetime
    level: str
    key: str
    message: str
    book_id: str | None


class EquityPoint(ApiModel):
    date: date
    equity: Decimal
    peak: Decimal
    return_pct: float  # vs the experiment's capital
    drawdown_pct: float  # below the running peak


class EquitySeries(ApiModel):
    book_id: str
    points: list[EquityPoint]


class BenchmarkPoint(ApiModel):
    date: date
    close: float
    return_pct: float  # vs the close of the session before the first book point (0 there)


class EquityView(ApiModel):
    capital: Decimal
    books: list[EquitySeries]
    benchmark: str
    benchmark_points: list[BenchmarkPoint]


class WatchRow(ApiModel):
    instrument_key: str
    symbol: str
    sector: str | None
    ltp: float | None
    prev_close: float | None
    change_pct: float | None
    quote_age_s: float | None
    source: str | None
    signals_today: list[str]  # "momentum BUY", ...
    events: list[str]  # recent announcement types
    held: bool


class ReportBook(ApiModel):
    end_equity: float | None
    day_return_pct: float | None
    cumulative_return_pct: float | None
    net_ai_value_inr: float | None
    exits: int | None


class ReportSummary(ApiModel):
    date: date
    experiment: str
    books: dict[str, ReportBook]


class Document(ApiModel):
    title: str
    markdown: str


class LogLine(ApiModel):
    ts: datetime
    level: str
    logger: str
    message: str
    decision_id: str | None


class CalibrationView(ApiModel):
    fitted: bool
    temperatures: dict[str, float]
    meta: dict[str, Any]


# -- research (event-aware candidates; decision support, never orders) --------------------------
#
# These mirror the frozen dataclasses in src.research field for field, so a card persisted by
# scripts/run_research.py round-trips through model_validate unchanged. Probabilities and log
# returns are floats (statistics, not money); timestamps are timezone-aware.


class ResearchEvidence(ApiModel):
    field: str
    value: str
    note: str


class ResearchCardEvent(ApiModel):
    event_id: str
    published_at: datetime
    known_at: datetime
    source: str
    url: str | None
    event_type: str | None
    subject: str
    excerpt: str
    direction: str
    materiality: str | None
    channel: str
    scope: str
    confidence: float | None
    basis: str
    uncertain: bool
    reason: str
    evidence: list[ResearchEvidence]
    updates: list[str]


class ResearchContext(ApiModel):
    instrument_key: str
    bar_date: date | None
    bars: int
    regime: str | None
    trend: str
    close: float | None
    rsi_14: float | None
    adx_14: float | None
    atr_pct: float | None
    bb_percent: float | None
    sigma_daily: float | None
    annualized_vol: float | None
    volume_ratio: float | None
    adv20_inr: float | None
    relative_strength: dict[str, float | None]
    data_quality: str
    notes: list[str]


class ResearchForecast(ApiModel):
    instrument_key: str
    horizon_sessions: int
    status: str  # ok | insufficient_evidence | unsupported_data
    as_of: datetime
    bar_date: date | None
    regime: str | None
    baseline: str  # regime_analogs | unconditional | none
    p_up: float | None
    p_side: float | None
    p_down: float | None
    q10: float | None
    q50: float | None
    q90: float | None
    p_underperform_benchmark: float | None
    sideways_band: float | None
    n_samples: int
    n_unconditional: int
    trend: str | None
    vol_band: str | None
    annualized_vol: float | None
    sigma_daily: float | None
    data_quality: str
    notes: list[str]


class ResearchFreshness(ApiModel):
    last_bar_date: date | None
    bar_age_sessions: int | None
    newest_event_at: datetime | None
    event_age_days: float | None
    stale: bool
    notes: list[str]


class ResearchCandidate(ApiModel):
    instrument_key: str
    symbol: str
    company: str
    sector: str | None
    asset: str  # equity | index | sector | unsupported
    horizon_sessions: int
    status: str  # WATCH | SETUP_FORMING | CONFIRMED | INVALIDATED | NO_TRADE
    as_of: datetime
    events: list[ResearchCardEvent]
    forecast: ResearchForecast | None
    context: ResearchContext
    confirmation: str  # confirms | disagrees | neutral | unknown
    confirmation_reason: str
    positive_factors: list[str]
    negative_factors: list[str]
    reasons: list[str]
    confirm_if: list[str]
    weaken_if: list[str]
    invalidate_if: list[str]
    unsupported: list[str]
    freshness: ResearchFreshness


class ResearchCalibrationCell(ApiModel):
    bucket: str
    n: int
    predicted: float | None
    realized: float | None


class ResearchEventStudyCell(ApiModel):
    event_type: str
    direction: str
    horizon_sessions: int
    regime: str | None
    n: int
    mean_abnormal_return: float | None
    median_abnormal_return: float | None
    hit_rate: float | None
    round_trip_cost_pct: float | None
    mean_net_abnormal_return: float | None
    notes: list[str]


class ResearchEvaluation(ApiModel):
    settled: int
    brier: float | None
    cells: list[ResearchCalibrationCell]
    by_horizon: dict[str, list[ResearchCalibrationCell]]
    by_regime: dict[str, list[ResearchCalibrationCell]]
    study: list[ResearchEventStudyCell]
    notes: list[str]


class ResearchView(ApiModel):
    as_of: datetime
    horizon: int
    counts: dict[str, int]
    candidates: list[ResearchCandidate]
    evaluation: ResearchEvaluation | None
    data_gaps: list[str]
    disclaimer: str


class ResearchApproachSummary(ApiModel):
    """One forecasting approach's walk-forward score. ``None`` means "not measurable yet"."""

    approach: str
    n: int
    brier: float | None
    brier_ci: list[float] | None
    interval_coverage: float | None
    interval_width: float | None
    mean_abnormal_return: float | None
    fallback_count: int


class ResearchComparisonSummary(ApiModel):
    approach: str
    baseline: str
    n_paired: int
    mean_loss_difference: float | None
    ci: list[float] | None
    verdict: str


class ResearchWalkForwardSummary(ApiModel):
    """The latest persisted walk-forward evaluation.

    ``available=False`` means no evaluation has been run yet. That is reported explicitly rather
    than rendered as an empty table, which could be misread as "measured and found no skill".
    """

    available: bool
    run_id: str | None
    evaluation_mode: str | None
    created_at: datetime | None
    totals: dict[str, int]
    fallback_rate: float | None
    approaches: list[ResearchApproachSummary]
    comparisons: list[ResearchComparisonSummary]
    notes: list[str]
