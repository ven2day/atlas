"""The price and outcome dataset: provenance first, numbers second.

Every outcome here is a **close-to-close reaction measurement**, not a trade. It answers "what did
the price do between the first close after the information was available and the close ``h``
sessions later, relative to the benchmark over those identical endpoints". Whether entering and
exiting on those prints would have been profitable is a different question that needs fills,
slippage, latency and an execution policy; :mod:`src.backtesting` owns that, and this module does
not borrow its language.

What it refuses to hide:

* **Provenance.** Provider, retrieval time, the range asked for, the range actually received,
  missing sessions, the adjustment method and benchmark coverage are all recorded, so a number can
  always be traced to the data that produced it.
* **Incomplete horizons stay ``pending``.** An event whose ``h`` sessions have not all settled has
  no outcome. It is never extrapolated, and never counted as zero.
* **Survivorship.** Evaluating today's constituents over past events is biased, and saying so is
  cheaper than pretending. ``universe_is_current`` turns that into a recorded note on every
  affected dataset.
* **Endpoint alignment.** The benchmark return spans the *same two session dates* as the
  instrument. A benchmark missing either endpoint discards the observation rather than
  substituting zero.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd

from src.domain.calendar import NSECalendar
from src.marketdata.history import DailySeries
from src.research.forecast import SIDE_K
from src.research.provenance import EvaluationMode, EventTimestamps
from src.research.timing import first_close_after

OUTCOME_HORIZONS: tuple[int, ...] = (1, 5, 10, 20)
VOL_WINDOW = 20

# Outcome statuses. Only ``settled`` carries numbers; the rest are explicit non-answers.
STATUS_SETTLED = "settled"
STATUS_PENDING = "pending"  # the horizon has not fully elapsed yet
STATUS_NO_ANCHOR = "no_anchor"  # no session closed after the information was available
STATUS_NO_PRICES = "no_prices"
STATUS_NO_BENCHMARK = "no_benchmark"
STATUS_UNUSABLE = "unusable"  # a non-positive or non-finite price inside the window


@dataclass(frozen=True, slots=True)
class PriceProvenance:
    """Where one instrument's bars came from, and what was missing."""

    dataset_id: str
    instrument_key: str
    provider: str
    retrieved_at: datetime
    requested_from: date | None
    requested_to: date | None
    available_from: date | None
    available_to: date | None
    sessions: int
    missing_sessions: tuple[str, ...]
    adjustment: str
    benchmark_key: str | None
    benchmark_sessions: int | None
    notes: tuple[str, ...] = ()

    def as_row(self) -> Mapping[str, object]:
        return {
            "dataset_id": self.dataset_id,
            "instrument_key": self.instrument_key,
            "provider": self.provider,
            "retrieved_at": self.retrieved_at,
            "requested_from": self.requested_from,
            "requested_to": self.requested_to,
            "available_from": self.available_from,
            "available_to": self.available_to,
            "sessions": self.sessions,
            "missing_sessions": list(self.missing_sessions),
            "adjustment": self.adjustment,
            "benchmark_key": self.benchmark_key,
            "benchmark_sessions": self.benchmark_sessions,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class EventOutcome:
    """One event at one horizon. ``status`` says whether the numbers mean anything."""

    outcome_id: str
    record_id: str
    instrument_key: str
    horizon_sessions: int
    evaluation_mode: EvaluationMode
    status: str
    computed_at: datetime
    anchor_date: date | None = None
    outcome_date: date | None = None
    instrument_return: float | None = None
    benchmark_return: float | None = None
    abnormal_return: float | None = None
    bucket: str | None = None
    sideways_band: float | None = None
    notes: tuple[str, ...] = ()

    @property
    def is_settled(self) -> bool:
        return self.status == STATUS_SETTLED

    def as_row(self) -> Mapping[str, object]:
        return {
            "outcome_id": self.outcome_id,
            "record_id": self.record_id,
            "instrument_key": self.instrument_key,
            "horizon_sessions": self.horizon_sessions,
            "evaluation_mode": self.evaluation_mode.value,
            "status": self.status,
            "anchor_date": self.anchor_date,
            "outcome_date": self.outcome_date,
            "instrument_return": self.instrument_return,
            "benchmark_return": self.benchmark_return,
            "abnormal_return": self.abnormal_return,
            "bucket": self.bucket,
            "computed_at": self.computed_at,
            "notes": list(self.notes),
        }


def session_dates(series: DailySeries) -> list[date]:
    """The frame's index as plain dates, whatever pandas type it holds."""
    out: list[date] = []
    for stamp in series.frame.index:
        out.append(
            stamp.date()
            if isinstance(stamp, pd.Timestamp | datetime)
            else date.fromisoformat(str(stamp))
        )
    return out


def missing_sessions(
    calendar: NSECalendar, dates: Sequence[date], start: date, end: date
) -> tuple[str, ...]:
    """Calendar sessions inside ``[start, end]`` that have no bar. Special and half days are
    sessions too, so they are asked of the calendar rather than assumed to be weekdays."""
    try:
        expected = calendar.trading_days(start, end)
    except Exception:  # the calendar fails closed outside its covered years
        return ()
    have = set(dates)
    return tuple(day.isoformat() for day in expected if day not in have)


def build_provenance(
    series: DailySeries,
    *,
    provider: str,
    retrieved_at: datetime,
    calendar: NSECalendar,
    requested_from: date | None = None,
    requested_to: date | None = None,
    benchmark: DailySeries | None = None,
    benchmark_key: str | None = None,
    adjustment: str = "dividend-adjusted for indicators, raw for prices",
    universe_is_current: bool = False,
) -> PriceProvenance:
    """Record what was asked for, what arrived, and what is missing."""
    dates = session_dates(series)
    notes: list[str] = []
    available_from = dates[0] if dates else None
    available_to = dates[-1] if dates else None
    gaps: tuple[str, ...] = ()
    if available_from is not None and available_to is not None:
        gaps = missing_sessions(calendar, dates, available_from, available_to)
        if gaps:
            notes.append(f"{len(gaps)} calendar session(s) have no bar in the delivered range")
    if (
        requested_from is not None
        and available_from is not None
        and available_from > requested_from
    ):
        notes.append(
            f"history starts {available_from.isoformat()}, later than the requested "
            f"{requested_from.isoformat()}: the provider does not go back that far"
        )
    if requested_to is not None and available_to is not None and available_to < requested_to:
        notes.append(
            f"history ends {available_to.isoformat()}, before the requested "
            f"{requested_to.isoformat()}"
        )
    if series.source.is_fabricated:
        notes.append(
            f"source is {series.source.value}: simulated or synthetic, unusable for real analysis"
        )
    if universe_is_current:
        notes.append(
            "SURVIVORSHIP: this instrument is in today's universe, which is not the point-in-time "
            "constituency on the event dates; results conditioned on it are biased towards "
            "survivors"
        )
    benchmark_sessions = len(session_dates(benchmark)) if benchmark is not None else None
    if benchmark is None:
        notes.append("no benchmark series: abnormal returns cannot be computed")
    dataset_id = hashlib.sha256(
        f"{series.instrument_key}|{provider}|{retrieved_at.isoformat()}".encode()
    ).hexdigest()[:24]
    return PriceProvenance(
        dataset_id=dataset_id,
        instrument_key=series.instrument_key,
        provider=provider,
        retrieved_at=retrieved_at,
        requested_from=requested_from,
        requested_to=requested_to,
        available_from=available_from,
        available_to=available_to,
        sessions=len(dates),
        missing_sessions=gaps,
        adjustment=adjustment,
        benchmark_key=benchmark_key,
        benchmark_sessions=benchmark_sessions,
        notes=tuple(notes),
    )


def sideways_band(close: pd.Series, horizon: int) -> float | None:
    """The same sideways definition the forecaster uses, so buckets are comparable."""
    log_close = pd.Series(math.log(v) if v > 0 else float("nan") for v in close.to_numpy(float))
    sigma = float(log_close.diff().rolling(VOL_WINDOW).std().iloc[-1])
    if not math.isfinite(sigma) or sigma <= 0:
        return None
    return SIDE_K * sigma * math.sqrt(horizon)


def _log_return(close: pd.Series, start: int, end: int) -> float | None:
    if start < 0 or end >= len(close):
        return None
    base, last = float(close.iloc[start]), float(close.iloc[end])
    if base <= 0 or last <= 0 or not (math.isfinite(base) and math.isfinite(last)):
        return None
    return math.log(last / base)


def bucket_of(realized: float, band: float | None) -> str:
    """up / side / down against the sideways half-width. Without a band, zero is the boundary."""
    if band is None or band <= 0:
        return "up" if realized > 0 else "down" if realized < 0 else "side"
    if realized > band:
        return "up"
    if realized < -band:
        return "down"
    return "side"


def event_outcome(
    *,
    record_id: str,
    instrument_key: str,
    horizon: int,
    timestamps: EventTimestamps,
    mode: EvaluationMode,
    series: DailySeries | None,
    benchmark: DailySeries | None,
    computed_at: datetime,
    ingestion_lag: timedelta | None = None,
    classification_lag: timedelta | None = None,
) -> EventOutcome:
    """One event at one horizon, anchored at the first close after the information was available.

    Returns ``pending`` - not zero, not dropped - when the horizon has not fully elapsed, so an
    incomplete observation can never flatter the average.
    """
    identity = f"{record_id}|{horizon}|{mode.value}"
    outcome_id = hashlib.sha256(identity.encode()).hexdigest()[:24]
    kwargs: dict[str, object] = {}
    if ingestion_lag is not None:
        kwargs["ingestion_lag"] = ingestion_lag
    if classification_lag is not None:
        kwargs["classification_lag"] = classification_lag
    available_at = timestamps.availability(mode, **kwargs)  # type: ignore[arg-type]

    def make(status: str, note: str, **extra: object) -> EventOutcome:
        return EventOutcome(
            outcome_id=outcome_id, record_id=record_id, instrument_key=instrument_key,
            horizon_sessions=horizon, evaluation_mode=mode, status=status,
            computed_at=computed_at, notes=(note,) if note else (), **extra,  # type: ignore[arg-type]
        )  # fmt: skip

    if series is None or series.frame.empty:
        return make(STATUS_NO_PRICES, "no daily bars for this instrument")
    if series.source.is_fabricated:
        return make(STATUS_UNUSABLE, f"price source is {series.source.value}, which is synthetic")

    dates = session_dates(series)
    close = series.adjusted()["close"].astype(float)
    start = first_close_after(dates, available_at)
    if start is None:
        return make(STATUS_NO_ANCHOR, "no session closed after the information was available")
    end = start + horizon
    if end >= len(dates):
        return make(
            STATUS_PENDING,
            f"only {len(dates) - start - 1} of {horizon} session(s) have settled since the anchor",
            anchor_date=dates[start],
        )
    if dates[end] > computed_at.date():
        return make(
            STATUS_PENDING,
            f"the {horizon}-session window ends {dates[end].isoformat()}, after the evaluation "
            f"date {computed_at.date().isoformat()}",
            anchor_date=dates[start],
        )

    own = _log_return(close, start, end)
    if own is None:
        return make(STATUS_UNUSABLE, "a price inside the window is non-positive or non-finite",
                    anchor_date=dates[start], outcome_date=dates[end])  # fmt: skip

    against: float | None = None
    if benchmark is not None and not benchmark.source.is_fabricated:
        bench_dates = session_dates(benchmark)
        bench_close = benchmark.adjusted()["close"].astype(float)
        # Identical endpoints: both must exist in the benchmark's own calendar, or there is no
        # comparable window and the observation is dropped rather than assumed to be zero.
        if dates[start] in bench_dates and dates[end] in bench_dates:
            against = _log_return(
                bench_close, bench_dates.index(dates[start]), bench_dates.index(dates[end])
            )
    if against is None and benchmark is not None:
        return make(
            STATUS_NO_BENCHMARK,
            f"the benchmark does not cover {dates[start].isoformat()}.."
            f"{dates[end].isoformat()}: abnormal return is undefined",
            anchor_date=dates[start], outcome_date=dates[end],
            instrument_return=round(own, 8),
        )  # fmt: skip

    band = sideways_band(close.iloc[: end + 1], horizon)
    abnormal = own if against is None else own - against
    return EventOutcome(
        outcome_id=outcome_id,
        record_id=record_id,
        instrument_key=instrument_key,
        horizon_sessions=horizon,
        evaluation_mode=mode,
        status=STATUS_SETTLED,
        computed_at=computed_at,
        anchor_date=dates[start],
        outcome_date=dates[end],
        instrument_return=round(own, 8),
        benchmark_return=None if against is None else round(against, 8),
        abnormal_return=round(abnormal, 8),
        bucket=bucket_of(abnormal, band),
        sideways_band=None if band is None else round(band, 8),
        notes=()
        if against is not None
        else ("no benchmark: the abnormal return is the raw return",),
    )


def build_outcomes(
    records: Iterable[Mapping[str, object]],
    series: Mapping[str, DailySeries],
    *,
    benchmark: DailySeries | None,
    computed_at: datetime,
    mode: EvaluationMode = EvaluationMode.RECONSTRUCTED,
    horizons: Sequence[int] = OUTCOME_HORIZONS,
    timestamps_by_record: Mapping[str, EventTimestamps],
    ingestion_lag: timedelta | None = None,
    classification_lag: timedelta | None = None,
) -> list[EventOutcome]:
    """Outcomes for every (record, horizon) pair, pending ones included."""
    out: list[EventOutcome] = []
    for record in records:
        record_id = str(record.get("record_id") or "")
        instrument_key = str(record.get("instrument_key") or "")
        timestamps = timestamps_by_record.get(record_id)
        if not record_id or timestamps is None:
            continue
        bars = series.get(instrument_key)
        for horizon in horizons:
            out.append(
                event_outcome(
                    record_id=record_id,
                    instrument_key=instrument_key,
                    horizon=horizon,
                    timestamps=timestamps,
                    mode=mode,
                    series=bars,
                    benchmark=benchmark,
                    computed_at=computed_at,
                    ingestion_lag=ingestion_lag,
                    classification_lag=classification_lag,
                )
            )
    return out


def summarize_outcomes(outcomes: Iterable[EventOutcome]) -> dict[str, int]:
    """Counts by status, so a report always shows how much was measurable and how much was not."""
    counts: dict[str, int] = {}
    for outcome in outcomes:
        counts[outcome.status] = counts.get(outcome.status, 0) + 1
    return counts


def now_utc() -> datetime:
    return datetime.now(UTC)
