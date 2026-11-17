"""Market context, kept separate from the event thesis.

The features and the regime already exist (``src.features``); this module only reshapes them
into the five things a research card has to show - trend, volatility, volume, relative strength
and regime - and then answers one question: does the market **confirm** or **disagree** with the
direction an event mapping claimed?

That separation is the point. An event thesis and a market confirmation are different kinds of
claim with different evidence, and blending them into one score is how a system starts sounding
confident about nothing. ``agreement`` returns a reason string alongside its verdict so a card
can show *why* the market agrees, not just that it does.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

import pandas as pd

from src.domain.types import Regime
from src.features.technical import Features
from src.marketdata.history import DailySeries
from src.research.mapping import ImpactDirection

RS_LOOKBACKS: tuple[int, ...] = (20, 60)
# A trend is "flat" rather than up/down inside this band around the 50-session mean, so a
# hair's-breadth crossing does not read as a directional market.
FLAT_BAND_PCT = 0.01


class TrendState(StrEnum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"
    UNKNOWN = "unknown"


class Confirmation(StrEnum):
    """Whether the market context supports the event's claimed direction."""

    CONFIRMS = "confirms"
    DISAGREES = "disagrees"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class MarketContext:
    instrument_key: str
    bar_date: date | None
    bars: int
    regime: Regime | None
    trend: TrendState
    close: float | None
    rsi_14: float | None
    adx_14: float | None
    atr_pct: float | None
    bb_percent: float | None
    sigma_daily: float | None
    annualized_vol: float | None
    volume_ratio: float | None  # the last bar's volume over its 20-session average
    adv20_inr: float | None
    relative_strength: Mapping[int, float | None]  # lookback sessions -> excess log return
    data_quality: str
    notes: tuple[str, ...] = ()


def relative_strength(
    series: DailySeries | None,
    benchmark: DailySeries | None,
    lookbacks: tuple[int, ...] = RS_LOOKBACKS,
) -> dict[int, float | None]:
    """Excess log return of the instrument over the benchmark, per lookback, aligned by session.

    ``None`` for a lookback the history cannot support, rather than a number computed off the
    front of the sample.
    """
    out: dict[int, float | None] = dict.fromkeys(lookbacks)
    if series is None or benchmark is None:
        return out
    if series.source.is_fabricated or benchmark.source.is_fabricated:
        return out  # no relative-strength claim on synthetic prices
    left = series.adjusted()["close"].astype(float)
    right = benchmark.adjusted()["close"].astype(float)
    pair = pd.concat([left.rename("s"), right.rename("b")], axis=1, join="inner").dropna()
    if len(pair) < 2:
        return out
    for window in lookbacks:
        if len(pair) <= window:
            continue
        base = pair.iloc[-window - 1]
        last = pair.iloc[-1]
        if base["s"] <= 0 or base["b"] <= 0:
            continue
        out[window] = round(
            math.log(float(last["s"]) / float(base["s"]))
            - math.log(float(last["b"]) / float(base["b"])),
            6,
        )
    return out


def _trend_of(features: Features | None) -> TrendState:
    if features is None or features.sma.get(50) is None or features.close <= 0:
        return TrendState.UNKNOWN
    mean = float(features.sma[50])
    if mean <= 0:
        return TrendState.UNKNOWN
    deviation = (features.close - mean) / mean
    if abs(deviation) <= FLAT_BAND_PCT:
        return TrendState.FLAT
    return TrendState.UP if deviation > 0 else TrendState.DOWN


def market_context(
    instrument_key: str,
    features: Features | None,
    *,
    regime: Regime | None = None,
    relative_strength_by_lookback: Mapping[int, float | None] | None = None,
    notes: tuple[str, ...] = (),
) -> MarketContext:
    """The market side of a card. ``features is None`` yields an honest UNKNOWN context."""
    if features is None:
        return MarketContext(
            instrument_key=instrument_key, bar_date=None, bars=0, regime=regime,
            trend=TrendState.UNKNOWN, close=None, rsi_14=None, adx_14=None, atr_pct=None,
            bb_percent=None, sigma_daily=None, annualized_vol=None, volume_ratio=None,
            adv20_inr=None, relative_strength=dict.fromkeys(RS_LOOKBACKS),
            data_quality="no settled daily bars",
            notes=(*notes, "no features: every market-context field is unknown"),
        )  # fmt: skip
    sigma = features.sigma_daily
    volume_ratio = None
    if features.adv20_shares and features.adv20_shares > 0:
        volume_ratio = round(features.volume / features.adv20_shares, 4)
    return MarketContext(
        instrument_key=instrument_key,
        bar_date=features.bar_date,
        bars=features.bars,
        regime=regime,
        trend=_trend_of(features),
        close=features.close,
        rsi_14=features.rsi_14,
        adx_14=features.adx_14,
        atr_pct=features.atr_pct,
        bb_percent=features.bb_percent,
        sigma_daily=sigma,
        annualized_vol=None if sigma is None else round(sigma * math.sqrt(252), 6),
        volume_ratio=volume_ratio,
        adv20_inr=features.adv20_inr,
        relative_strength=dict(relative_strength_by_lookback or dict.fromkeys(RS_LOOKBACKS)),
        data_quality=f"bars={features.bars}, last_bar={features.bar_date.isoformat()}",
        notes=notes,
    )


def agreement(context: MarketContext, direction: ImpactDirection) -> tuple[Confirmation, str]:
    """Does the market context support the event's claimed direction, and why."""
    if direction in (ImpactDirection.UNKNOWN, ImpactDirection.NEUTRAL):
        return (
            Confirmation.UNKNOWN,
            "the mapping claims no direction, so there is nothing to confirm",
        )
    if context.trend is TrendState.UNKNOWN:
        return Confirmation.UNKNOWN, "no settled bars: the market context is unknown"

    parts: list[str] = [f"trend {context.trend.value}"]
    if context.regime is not None:
        parts.append(f"regime {context.regime.value}")
    strongest = context.relative_strength.get(RS_LOOKBACKS[0])
    if strongest is not None:
        parts.append(f"{RS_LOOKBACKS[0]}-session relative strength {strongest:+.4f}")
    detail = ", ".join(parts)

    positive = direction is ImpactDirection.POSITIVE
    trend_supports = (
        context.trend is TrendState.UP if positive else context.trend is TrendState.DOWN
    )
    regime_supports = context.regime is None or context.regime not in (
        Regime.TRENDING_DOWN if positive else Regime.TRENDING_UP
    )
    rs_supports = strongest is None or ((strongest > 0) if positive else (strongest < 0))

    if context.trend is TrendState.FLAT:
        return (
            Confirmation.NEUTRAL,
            f"the market is flat, so it neither confirms nor denies: {detail}",
        )
    if trend_supports and regime_supports and rs_supports:
        return Confirmation.CONFIRMS, f"the market points the same way as the event: {detail}"
    if not trend_supports:
        return Confirmation.DISAGREES, f"the trend points against the event: {detail}"
    return Confirmation.NEUTRAL, f"the trend agrees but the wider context does not: {detail}"
