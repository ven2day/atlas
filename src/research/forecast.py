"""Horizon-specific probabilistic forecasts from historical analogs.

Every number here is **empirical**. A forecast is the distribution of what actually happened over
the next ``h`` sessions, in the past, from bars whose trailing state resembled today's. No model
is asked for a probability, and nothing extrapolates a price target: the output is a return
range plus the share of analogs that went up, sideways or down.

How an analog is chosen. Each historical bar gets a coarse state from data available *at that
bar only*:

* **trend** - close above or below its own 50-session mean;
* **volatility band** - the 20-session realized volatility, annualized, against *fixed*
  thresholds. Fixed rather than sample-relative on purpose: a percentile taken over the whole
  sample would let future bars decide the bucket a past bar sits in, which is look-ahead.

Bars in the same state as the latest settled bar are the analogs. When too few exist, the
forecast falls back to the unconditional distribution over the same history and says so in
``baseline``. When even that is too thin, the answer is ``INSUFFICIENT_EVIDENCE`` - a missing
forecast is honest, an invented 62% is not.

Two hard rules:

* Fabricated prices are refused outright (``MarketDataSource.is_fabricated``). The demo tape must
  never produce a forecast that could be read as a view on the real market.
* "Sideways" is a stated band, not a residual: ``|r| <= SIDE_K * sigma_daily * sqrt(h)``. Without
  saying what counts as sideways, ``p_up`` and ``p_down`` are not comparable across horizons.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

import numpy as np
import pandas as pd

from src.domain.types import Regime
from src.marketdata.history import DailySeries

HORIZONS: tuple[int, ...] = (1, 5, 10, 20)  # trading sessions
DEFAULT_HORIZON = 5

# Half-width of the "sideways" band, in daily sigmas scaled to the horizon.
SIDE_K = 0.5
# Annualized realized-volatility bands. Fixed thresholds: no look-ahead in analog selection.
VOL_BANDS: tuple[tuple[str, float], ...] = (("low", 0.20), ("mid", 0.40))
VOL_BAND_HIGH = "high"

MIN_ANALOG_SAMPLES = 20  # fewer regime-conditioned analogs than this and we fall back
MIN_UNCONDITIONAL_SAMPLES = 40  # fewer than this in total and there is no forecast at all
TREND_WINDOW = 50
VOL_WINDOW = 20
SESSIONS_PER_YEAR = 252
QUANTILES = (0.10, 0.50, 0.90)


class ForecastStatus(StrEnum):
    OK = "ok"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNSUPPORTED_DATA = "unsupported_data"


@dataclass(frozen=True, slots=True)
class Forecast:
    """One instrument, one horizon, with everything needed to judge whether to trust it."""

    instrument_key: str
    horizon_sessions: int
    status: ForecastStatus
    as_of: datetime
    bar_date: date | None
    regime: Regime | None
    baseline: str  # "regime_analogs" | "unconditional" | "none"
    p_up: float | None = None
    p_side: float | None = None
    p_down: float | None = None
    q10: float | None = None  # forward log-return quantiles over the horizon
    q50: float | None = None
    q90: float | None = None
    p_underperform_benchmark: float | None = None
    sideways_band: float | None = None
    n_samples: int = 0
    n_unconditional: int = 0
    trend: str | None = None
    vol_band: str | None = None
    annualized_vol: float | None = None
    sigma_daily: float | None = None
    data_quality: str = ""
    notes: tuple[str, ...] = ()

    @property
    def is_usable(self) -> bool:
        return self.status is ForecastStatus.OK


def _empty(
    series: DailySeries,
    *,
    horizon: int,
    as_of: datetime,
    regime: Regime | None,
    status: ForecastStatus,
    data_quality: str,
    notes: Sequence[str] = (),
) -> Forecast:
    return Forecast(
        instrument_key=series.instrument_key,
        horizon_sessions=horizon,
        status=status,
        as_of=as_of,
        bar_date=None,
        regime=regime,
        baseline="none",
        data_quality=data_quality,
        notes=tuple(notes),
    )


def _vol_band(annualized: float) -> str:
    for name, ceiling in VOL_BANDS:
        if annualized <= ceiling:
            return name
    return VOL_BAND_HIGH


def _states(close: pd.Series) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Per-bar (trend, annualized vol, daily sigma), each using only data up to that bar."""
    log_close = np.log(close.where(close > 0))
    sigma = log_close.diff().rolling(VOL_WINDOW).std()
    annualized = sigma * math.sqrt(SESSIONS_PER_YEAR)
    sma = close.rolling(TREND_WINDOW).mean()
    # A bar with no 50-session mean yet has no trend claim: "?" rather than a guessed direction.
    trend = pd.Series("?", index=close.index, dtype="object")
    trend[close > sma] = "up"
    trend[close <= sma] = "down"
    return trend, annualized, sigma


def _vol_bands(annualized: pd.Series) -> pd.Series:
    """The fixed volatility band of each bar, as a series of "low"/"mid"/"high"/"?"."""
    return annualized.map(
        lambda v: _vol_band(float(v)) if pd.notna(v) else "?", na_action=None
    ).astype("object")


def _forward_returns(close: pd.Series, horizon: int) -> pd.Series:
    log_close = np.log(close.where(close > 0))
    return (log_close.shift(-horizon) - log_close).dropna()


def _quantiles(returns: np.ndarray) -> tuple[float, float, float]:
    q10, q50, q90 = (float(np.quantile(returns, q)) for q in QUANTILES)
    return q10, q50, q90


def forecast(
    series: DailySeries,
    *,
    horizon: int = DEFAULT_HORIZON,
    as_of: datetime,
    regime: Regime | None = None,
    benchmark: DailySeries | None = None,
    min_analog_samples: int = MIN_ANALOG_SAMPLES,
    min_unconditional_samples: int = MIN_UNCONDITIONAL_SAMPLES,
) -> Forecast:
    """The forward-return distribution for ``series`` over ``horizon`` sessions.

    ``benchmark`` (normally the index) is only used for ``p_underperform_benchmark``, aligned by
    session date; without it that field is ``None`` rather than assumed to be zero.
    """
    if horizon not in HORIZONS:
        raise ValueError(f"horizon must be one of {HORIZONS}, got {horizon}")
    if series.source.is_fabricated:
        return _empty(
            series, horizon=horizon, as_of=as_of, regime=regime,
            status=ForecastStatus.UNSUPPORTED_DATA,
            data_quality=f"source={series.source.value}",
            notes=("refusing to forecast on simulated or synthetic prices",),
        )  # fmt: skip

    frame = series.adjusted()
    close = frame["close"].astype(float)
    bars = int(len(close))
    quality = f"bars={bars}, source={series.source.value}, last_bar={series.last_date.isoformat()}"
    if bars < min(TREND_WINDOW, VOL_WINDOW) + horizon + 1:
        return _empty(
            series, horizon=horizon, as_of=as_of, regime=regime,
            status=ForecastStatus.INSUFFICIENT_EVIDENCE, data_quality=quality,
            notes=(f"only {bars} settled bars; need at least "
                   f"{min(TREND_WINDOW, VOL_WINDOW) + horizon + 1}",),
        )  # fmt: skip

    trend, annualized, sigma = _states(close)
    fwd = _forward_returns(close, horizon)
    if len(fwd) < min_unconditional_samples:
        return _empty(
            series, horizon=horizon, as_of=as_of, regime=regime,
            status=ForecastStatus.INSUFFICIENT_EVIDENCE, data_quality=quality,
            notes=(f"only {len(fwd)} usable {horizon}-session forward returns; "
                   f"need {min_unconditional_samples}",),
        )  # fmt: skip

    notes: list[str] = []
    current_trend = str(trend.iloc[-1])
    current_vol = float(annualized.iloc[-1]) if pd.notna(annualized.iloc[-1]) else None
    current_sigma = float(sigma.iloc[-1]) if pd.notna(sigma.iloc[-1]) else None
    current_band = _vol_band(current_vol) if current_vol is not None else "?"

    # The analog mask is aligned onto the forward-return index, so a bar counts as an analog only
    # if it also has a realized outcome h sessions later.
    band = _vol_bands(annualized).reindex(fwd.index)
    analog_trend = trend.reindex(fwd.index).astype("object")
    unconditional = fwd.to_numpy(dtype=float)
    analog_mask = (analog_trend == current_trend) & (band == current_band)
    analogs = fwd[analog_mask.to_numpy(dtype=bool)].to_numpy(dtype=float)

    if len(analogs) >= min_analog_samples and current_trend != "?" and current_band != "?":
        returns, baseline = analogs, "regime_analogs"
    else:
        returns, baseline = unconditional, "unconditional"
        if len(analogs) < min_analog_samples:
            notes.append(
                f"only {len(analogs)} analogs in state (trend={current_trend}, vol={current_band})"
                f"; fell back to all {len(unconditional)} forward returns"
            )
        if current_trend == "?" or current_band == "?":
            notes.append("the trailing state is not yet determined; using the unconditional base")

    if current_sigma is None or current_sigma <= 0:
        sideways = None
        notes.append("no daily sigma: the sideways band is undefined, so p_up/p_down are not set")
    else:
        sideways = SIDE_K * current_sigma * math.sqrt(horizon)

    if sideways is None:
        p_up = p_side = p_down = None
    else:
        p_up = float(np.mean(returns > sideways))
        p_down = float(np.mean(returns < -sideways))
        p_side = float(1.0 - p_up - p_down)
    q10, q50, q90 = _quantiles(returns)

    p_under = _underperformance(series, benchmark, horizon, fwd, analog_mask, notes)

    return Forecast(
        instrument_key=series.instrument_key,
        horizon_sessions=horizon,
        status=ForecastStatus.OK,
        as_of=as_of,
        bar_date=series.last_date,
        regime=regime,
        baseline=baseline,
        p_up=p_up,
        p_side=p_side,
        p_down=p_down,
        q10=round(q10, 6),
        q50=round(q50, 6),
        q90=round(q90, 6),
        p_underperform_benchmark=p_under,
        sideways_band=None if sideways is None else round(sideways, 6),
        n_samples=int(len(returns)),
        n_unconditional=int(len(unconditional)),
        trend=current_trend,
        vol_band=current_band,
        annualized_vol=None if current_vol is None else round(current_vol, 6),
        sigma_daily=None if current_sigma is None else round(current_sigma, 6),
        data_quality=quality,
        notes=tuple(notes),
    )


def _underperformance(
    series: DailySeries,
    benchmark: DailySeries | None,
    horizon: int,
    fwd: pd.Series,
    analog_mask: pd.Series,
    notes: list[str],
) -> float | None:
    """P(instrument underperforms the benchmark) over the same sessions, or None without one."""
    if benchmark is None:
        notes.append("no benchmark series supplied: relative underperformance is not estimated")
        return None
    if benchmark.source.is_fabricated:
        notes.append(
            "the benchmark series is synthetic: relative underperformance is not estimated"
        )
        return None
    bench_close = benchmark.adjusted()["close"].astype(float)
    bench_fwd = _forward_returns(bench_close, horizon)
    pair = pd.concat([fwd.rename("r"), bench_fwd.rename("b")], axis=1, join="inner").dropna()
    if len(pair) < MIN_ANALOG_SAMPLES:
        notes.append(
            f"only {len(pair)} sessions overlap the benchmark; underperformance is not estimated"
        )
        return None
    mask = analog_mask.reindex(pair.index).to_numpy(dtype=bool)
    subset = pair[mask]
    if len(subset) < MIN_ANALOG_SAMPLES:
        notes.append(
            f"only {len(subset)} benchmark-overlapping analogs; using all {len(pair)} "
            "overlapping sessions instead"
        )
        subset = pair
    worse = subset["r"].to_numpy(dtype=float) < subset["b"].to_numpy(dtype=float)
    return round(float(np.mean(worse)), 6)


def forecast_all(
    series: Sequence[DailySeries],
    *,
    horizon: int = DEFAULT_HORIZON,
    as_of: datetime,
    regime: Regime | None = None,
    benchmark: DailySeries | None = None,
) -> dict[str, Forecast]:
    """One forecast per instrument, keyed by instrument key."""
    return {
        item.instrument_key: forecast(
            item, horizon=horizon, as_of=as_of, regime=regime, benchmark=benchmark
        )
        for item in series
    }
