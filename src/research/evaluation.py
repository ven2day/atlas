"""Event studies and forecast calibration, both strictly point in time.

Two measurements, neither of which is allowed to flatter the system:

**Event study.** For each mapped event, the instrument's return from the first settled session
that ends *after* the event became known, less the benchmark's return over the same window. Day
zero is never the session the event was published in unless that session had already closed, so a
study cannot profit from knowing something before it was knowable. Where a cell describes a
*trade setup* rather than a bare reaction, the round-trip NSE charges from the same schedule the
simulated broker uses are deducted, because a gross abnormal return is not a return anyone keeps.

**Calibration.** Forecasts are scored against outcomes they did not know: a multi-class Brier
score over the up/sideways/down buckets, plus a reliability table of predicted ``p_up`` against
the observed frequency of ``up``, broken out by horizon and regime. Every cell carries its sample
size, and a cell with too few observations is reported as such rather than as a percentage.

Nothing here computes a profit claim. A calibration report says whether the system's stated
probabilities matched reality; whether the resulting decisions would have made money is a
different question, and :mod:`src.backtesting.edge` already owns the gate for it.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

import numpy as np
import pandas as pd

from src.brokers.simulated.costs import NSECostSchedule
from src.domain.types import Product, Side
from src.marketdata.history import DailySeries
from src.research.mapping import EventImpact, ImpactScope
from src.research.store import ResearchStore
from src.research.timing import first_close_after

EVENT_STUDY_HORIZONS: tuple[int, ...] = (1, 3, 5, 10)
# Below this many observations a cell reports counts but no percentage, because a mean of three
# samples is a story, not a statistic.
MIN_CELL_SAMPLES = 8
CALIBRATION_BINS = 10
# A round-trip cost is measured on this notional; the schedule's brokerage cap makes the result
# notional-dependent, so the figure is labelled with the notional it was computed on.
COST_PROBE_NOTIONAL = Decimal("100000")


@dataclass(frozen=True, slots=True)
class EventStudyCell:
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
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CalibrationCell:
    bucket: str
    n: int
    predicted: float | None
    realized: float | None


@dataclass(frozen=True, slots=True)
class CalibrationReport:
    n: int
    brier: float | None
    cells: tuple[CalibrationCell, ...]
    by_horizon: Mapping[str, tuple[CalibrationCell, ...]]
    by_regime: Mapping[str, tuple[CalibrationCell, ...]]
    notes: tuple[str, ...] = ()


def round_trip_cost_pct(
    schedule: NSECostSchedule,
    product: Product = Product.CNC,
    notional: Decimal = COST_PROBE_NOTIONAL,
) -> float:
    """Buy-plus-sell charges as a fraction of notional, on the schedule the broker itself uses."""
    if notional <= 0:
        raise ValueError("the cost probe notional must be positive")
    buy = schedule.charges(product=product, side=Side.BUY, notional=notional)
    sell = schedule.charges(product=product, side=Side.SELL, notional=notional, dp_applies=True)
    return float((buy.total + sell.total) / notional)


def _session_dates(series: DailySeries) -> list[date]:
    """The frame's index as plain dates, whatever pandas type it holds."""
    out: list[date] = []
    for stamp in series.frame.index:
        out.append(
            stamp.date()
            if isinstance(stamp, pd.Timestamp | datetime)
            else date.fromisoformat(str(stamp))
        )
    return out


def _first_session_on_or_after(dates: Sequence[date], target: date) -> int | None:
    for index, day in enumerate(dates):
        if day >= target:
            return index
    return None


def _log_return(close: pd.Series, start: int, end: int) -> float | None:
    if start < 0 or end >= len(close):
        return None
    base, last = float(close.iloc[start]), float(close.iloc[end])
    if base <= 0 or last <= 0 or not (math.isfinite(base) and math.isfinite(last)):
        return None
    return math.log(last / base)


def event_study(
    impacts: Iterable[EventImpact],
    series: Mapping[str, DailySeries],
    *,
    benchmark: DailySeries | None = None,
    horizons: Sequence[int] = EVENT_STUDY_HORIZONS,
    known_at: Mapping[str, datetime],
    regime_by_date: Mapping[date, str] | None = None,
    cost_pct: float | None = None,
) -> list[EventStudyCell]:
    """Abnormal returns grouped by event type, claimed direction, horizon and regime."""
    regimes = regime_by_date or {}
    bench_dates = _session_dates(benchmark) if benchmark is not None else []
    bench_close = benchmark.adjusted()["close"].astype(float) if benchmark is not None else None

    grouped: dict[tuple[str, str, int, str | None], list[float]] = defaultdict(list)
    for impact in impacts:
        if impact.scope is not ImpactScope.ISSUER or not impact.instrument_key:
            continue  # a sector spillover has no single price series to measure
        bars = series.get(impact.instrument_key)
        if bars is None or bars.source.is_fabricated:
            continue
        moment = known_at.get(impact.event_id)
        if moment is None:
            continue
        dates = _session_dates(bars)
        start = first_close_after(dates, moment)
        if start is None:
            continue  # the event is not yet followed by any settled session
        close = bars.adjusted()["close"].astype(float)
        event_type = _event_type_of(impact)
        regime = regimes.get(dates[start])
        for horizon in horizons:
            end = start + horizon
            own = _log_return(close, start, end)
            if own is None:
                continue
            against = 0.0
            if bench_close is not None:
                if dates[start] not in bench_dates or dates[end] not in bench_dates:
                    continue
                bench_return = _log_return(
                    bench_close, bench_dates.index(dates[start]), bench_dates.index(dates[end])
                )
                if bench_return is None:
                    continue  # no benchmark over the same window: skip rather than assume zero
                against = bench_return
            grouped[(event_type, impact.direction.value, horizon, regime)].append(own - against)

    cells: list[EventStudyCell] = []
    for (event_type, direction, horizon, regime), returns in sorted(grouped.items()):
        values = np.asarray(returns, dtype=float)
        enough = len(values) >= MIN_CELL_SAMPLES
        notes: list[str] = []
        if not enough:
            notes.append(
                f"only {len(values)} observation(s), below {MIN_CELL_SAMPLES}: percentages are "
                "not reported"
            )
        net = None
        if enough and cost_pct is not None:
            net = round(float(np.mean(values)) - cost_pct, 6)
        elif cost_pct is not None:
            notes.append("round-trip costs are shown but not applied to an undersized cell")
        cells.append(
            EventStudyCell(
                event_type=event_type,
                direction=direction,
                horizon_sessions=horizon,
                regime=regime,
                n=len(values),
                mean_abnormal_return=round(float(np.mean(values)), 6) if enough else None,
                median_abnormal_return=round(float(np.median(values)), 6) if enough else None,
                hit_rate=round(float(np.mean(values > 0)), 6) if enough else None,
                round_trip_cost_pct=None if cost_pct is None else round(cost_pct, 6),
                mean_net_abnormal_return=net,
                notes=tuple(notes),
            )
        )
    return cells


def _event_type_of(impact: EventImpact) -> str:
    """The event type is carried on the impact's evidence, not on the impact itself."""
    for item in impact.evidence:
        if item.field == "event_type":
            return item.value
    return "unclassified"


def settle_forecasts(
    store: ResearchStore,
    series: Mapping[str, DailySeries],
    *,
    benchmark: DailySeries | None = None,
    as_of: datetime,
    limit: int = 1000,
) -> int:
    """Score every published forecast whose horizon has since fully elapsed. Idempotent."""
    rows = store.unsettled_forecasts(limit=limit)
    bench_dates = _session_dates(benchmark) if benchmark is not None else []
    bench_close = benchmark.adjusted()["close"].astype(float) if benchmark is not None else None
    settled = 0
    for row in rows:
        bars = series.get(str(row["instrument_key"]))
        if bars is None or bars.source.is_fabricated:
            continue
        raw_bar_date = row.get("bar_date")
        if not isinstance(raw_bar_date, str) or not raw_bar_date:
            continue
        dates = _session_dates(bars)
        try:
            anchor = date.fromisoformat(raw_bar_date)
        except ValueError:
            continue
        if anchor not in dates:
            continue
        start = dates.index(anchor)
        horizon = int(row["horizon_sessions"])
        end = start + horizon
        if end >= len(dates) or dates[end] > as_of.date():
            continue  # the horizon has not elapsed yet: leave it unsettled, never guess
        close = bars.adjusted()["close"].astype(float)
        realized = _log_return(close, start, end)
        if realized is None:
            continue
        against: float | None = None
        if bench_close is not None:
            if dates[start] in bench_dates and dates[end] in bench_dates:
                against = _log_return(
                    bench_close, bench_dates.index(dates[start]), bench_dates.index(dates[end])
                )
        band = row.get("sideways_band")
        bucket = _bucket(realized, None if band is None else float(band))
        store.record_outcome(
            str(row["forecast_id"]),
            outcome_return=round(realized, 6),
            outcome_benchmark_return=None if against is None else round(against, 6),
            outcome_bucket=bucket,
            settled_at=as_of,
        )
        settled += 1
    return settled


def _bucket(realized: float, sideways_band: float | None) -> str:
    if sideways_band is None or sideways_band <= 0:
        return "up" if realized > 0 else "down" if realized < 0 else "side"
    if realized > sideways_band:
        return "up"
    if realized < -sideways_band:
        return "down"
    return "side"


def calibration(rows: Iterable[Mapping[str, object]]) -> CalibrationReport:
    """Brier score and reliability table over settled forecasts.

    ``rows`` are ``research_forecasts`` rows that already have an ``outcome_bucket``. The Brier
    score is multi-class over up/sideways/down, so it penalises a confident wrong call as much as
    it rewards a confident right one; lower is better and 2/3 is what always guessing the middle
    bucket would score.
    """
    usable = [r for r in rows if r.get("outcome_bucket") in ("up", "side", "down")]
    notes: list[str] = []
    if not usable:
        return CalibrationReport(
            n=0, brier=None, cells=(), by_horizon={}, by_regime={},
            notes=("no settled forecasts yet: nothing to calibrate against",),
        )  # fmt: skip
    if len(usable) < MIN_CELL_SAMPLES:
        notes.append(
            f"only {len(usable)} settled forecast(s), below {MIN_CELL_SAMPLES}: the reliability "
            "table is indicative, not a measurement"
        )

    def brier_of(subset: Sequence[Mapping[str, object]]) -> float | None:
        if not subset:
            return None
        total = 0.0
        for row in subset:
            outcome = str(row.get("outcome_bucket"))
            for bucket, key in (("up", "p_up"), ("side", "p_side"), ("down", "p_down")):
                value = row.get(key)
                if not isinstance(value, int | float):
                    return None  # no sideways band was defined for this forecast
                probability = float(value)
                total += (probability - (1.0 if bucket == outcome else 0.0)) ** 2
        return round(total / len(subset), 6)

    by_horizon: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    by_regime: dict[str, list[Mapping[str, object]]] = defaultdict(list)
    for row in usable:
        by_horizon[f"{row.get('horizon_sessions')}"].append(row)
        by_regime[str(row.get("regime") or "any")].append(row)

    return CalibrationReport(
        n=len(usable),
        brier=brier_of(usable),
        cells=_reliability(usable),
        by_horizon={k: _reliability(v) for k, v in sorted(by_horizon.items())},
        by_regime={k: _reliability(v) for k, v in sorted(by_regime.items())},
        notes=tuple(notes),
    )


def _reliability(rows: Sequence[Mapping[str, object]]) -> tuple[CalibrationCell, ...]:
    """Predicted ``p_up`` against the observed frequency of ``up``, in decile bins."""
    pairs: list[tuple[float, float]] = []
    for row in rows:
        predicted = row.get("p_up")
        if not isinstance(predicted, int | float):
            continue
        pairs.append((float(predicted), 1.0 if row.get("outcome_bucket") == "up" else 0.0))
    if not pairs:
        return ()
    pairs.sort()
    cells: list[CalibrationCell] = []
    for index in range(CALIBRATION_BINS):
        low, high = index / CALIBRATION_BINS, (index + 1) / CALIBRATION_BINS
        selected = [
            (p, r)
            for p, r in pairs
            if (low <= p < high or (index == CALIBRATION_BINS - 1 and p == 1.0))
        ]
        if not selected:
            continue
        predicted = [p for p, _ in selected]
        realized = [r for _, r in selected]
        enough = len(selected) >= MIN_CELL_SAMPLES
        cells.append(
            CalibrationCell(
                bucket=f"{low:.1f}-{high:.1f}",
                n=len(selected),
                predicted=round(float(np.mean(predicted)), 4) if enough else None,
                realized=round(float(np.mean(realized)), 4) if enough else None,
            )
        )
    return tuple(cells)
