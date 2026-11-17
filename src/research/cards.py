"""Recommendation cards: the explainable unit the research UI shows.

A card is deliberately *not* a trade. It is one instrument, one horizon, and five separated
blocks of evidence - catalyst, market context, forecast, status, and what is missing - so a
reader can disagree with any single block without losing the rest.

Two invariants this module holds:

* **A positive event interpretation never becomes a recommendation on its own.** ``status`` comes
  from :mod:`src.research.status`, which requires the market context and the historical
  distribution to agree before anything reaches ``CONFIRMED``.
* **Every candidate that matches is returned.** There is no top-N cut anywhere in this package;
  ranking is a presentation choice made by the caller, and truncating here would hide exactly the
  weak-evidence cases a reviewer most needs to see.

The whole path is synchronous and pure: it takes already-fetched events, bars and features and
returns cards, so the CLI, the web read model and the evaluation harness all compute identical
output from identical input.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from src.domain.types import Instrument, Regime
from src.features.technical import Features
from src.marketdata.history import DailySeries
from src.research.context import (
    Confirmation,
    MarketContext,
    agreement,
    market_context,
    relative_strength,
)
from src.research.events import AssetClass, ResearchEvent
from src.research.forecast import DEFAULT_HORIZON, Forecast, ForecastStatus, forecast
from src.research.mapping import EventImpact, Evidence, ImpactDirection, ImpactScope, map_events
from src.research.status import CandidateStatus, StatusDecision, decide_status

# Presentation only: the order statuses are listed in, not a ranking of merit.
STATUS_RANK: Mapping[CandidateStatus, int] = {
    CandidateStatus.CONFIRMED: 0,
    CandidateStatus.SETUP_FORMING: 1,
    CandidateStatus.WATCH: 2,
    CandidateStatus.INVALIDATED: 3,
    CandidateStatus.NO_TRADE: 4,
}
EXCERPT_CHARS = 240
# A settled bar older than this many sessions is stale enough to say so on the card.
STALE_AFTER_SESSIONS = 3


@dataclass(frozen=True, slots=True)
class CardEvent:
    """One catalyst, with the evidence that produced the reading of it."""

    event_id: str
    published_at: datetime
    known_at: datetime
    source: str
    url: str | None
    event_type: str | None
    subject: str
    excerpt: str
    direction: str | None
    materiality: str | None
    channel: str
    scope: str
    confidence: float | None
    basis: str
    uncertain: bool
    reason: str
    evidence: tuple[Evidence, ...]
    updates: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class DataFreshness:
    """How old every input is, so a stale card is visibly stale."""

    last_bar_date: date | None
    bar_age_sessions: int | None
    newest_event_at: datetime | None
    event_age_days: float | None
    stale: bool
    notes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CandidateCard:
    instrument_key: str
    symbol: str
    company: str
    sector: str | None
    asset: AssetClass
    horizon_sessions: int
    status: CandidateStatus
    as_of: datetime
    events: tuple[CardEvent, ...]
    forecast: Forecast | None
    context: MarketContext
    confirmation: Confirmation
    confirmation_reason: str
    positive_factors: tuple[str, ...]
    negative_factors: tuple[str, ...]
    reasons: tuple[str, ...]
    confirm_if: tuple[str, ...]
    weaken_if: tuple[str, ...]
    invalidate_if: tuple[str, ...]
    unsupported: tuple[str, ...]
    freshness: DataFreshness

    @property
    def confidence(self) -> float | None:
        """The weakest catalyst confidence; a card is only as good as its least certain input."""
        values = [e.confidence for e in self.events if e.confidence is not None]
        return min(values) if values else None


@dataclass(frozen=True, slots=True)
class CandidateFilter:
    """Everything optional: an all-default filter keeps every candidate."""

    asset: AssetClass | None = None
    sector: str | None = None
    event_type: str | None = None
    horizon: int | None = None
    status: CandidateStatus | None = None
    min_confidence: float | None = None
    include_uncertain: bool = True


def filter_cards(
    cards: Sequence[CandidateCard], filters: CandidateFilter | None = None
) -> list[CandidateCard]:
    """Every card matching ``filters``, in status order then by symbol. Never truncated."""
    wanted = filters or CandidateFilter()
    out: list[CandidateCard] = []
    for card in cards:
        if wanted.asset is not None and card.asset is not wanted.asset:
            continue
        if wanted.sector is not None and (card.sector or "") != wanted.sector:
            continue
        if wanted.horizon is not None and card.horizon_sessions != wanted.horizon:
            continue
        if wanted.status is not None and card.status is not wanted.status:
            continue
        if wanted.event_type is not None and not any(
            e.event_type == wanted.event_type for e in card.events
        ):
            continue
        if wanted.min_confidence is not None:
            confidence = card.confidence
            if confidence is None or confidence < wanted.min_confidence:
                continue
        if not wanted.include_uncertain and any(e.uncertain for e in card.events):
            continue
        out.append(card)
    out.sort(key=lambda c: (STATUS_RANK.get(c.status, 9), c.symbol, c.instrument_key))
    return out


def _card_event(event: ResearchEvent, impact: EventImpact) -> CardEvent:
    return CardEvent(
        event_id=event.event_id,
        published_at=event.published_at,
        known_at=event.known_at,
        source=event.source,
        url=event.source_url,
        event_type=event.event_type.value if event.event_type is not None else None,
        subject=event.subject,
        excerpt=event.text[:EXCERPT_CHARS],
        direction=impact.direction.value,
        materiality=event.materiality.value if event.materiality is not None else None,
        channel=impact.channel.value,
        scope=impact.scope.value,
        confidence=impact.confidence,
        basis=impact.basis,
        uncertain=impact.uncertain,
        reason=impact.reason,
        evidence=impact.evidence,
        updates=event.updates,
    )


def _factors(
    impacts: Sequence[EventImpact],
    context: MarketContext,
    prediction: Forecast | None,
    confirmation: Confirmation,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Positive and negative factors, each naming the field it came from."""
    positive: list[str] = []
    negative: list[str] = []
    for impact in impacts:
        if impact.scope is not ImpactScope.ISSUER:
            continue
        text = f"{impact.channel.value} channel reads {impact.direction.value} ({impact.basis})"
        if impact.direction is ImpactDirection.POSITIVE:
            positive.append(text)
        elif impact.direction is ImpactDirection.NEGATIVE:
            negative.append(text)
        elif impact.direction is ImpactDirection.MIXED:
            negative.append(f"{text}; the evidence is contradictory")
        if impact.uncertain:
            negative.append(f"{impact.channel.value} channel is flagged uncertain: {impact.reason}")
    if context.trend.value == "up":
        positive.append("price above its 50-session mean")
    elif context.trend.value == "down":
        negative.append("price below its 50-session mean")
    if context.regime is Regime.TRENDING_UP:
        positive.append("market regime trending up")
    elif context.regime is Regime.TRENDING_DOWN:
        negative.append("market regime trending down")
    elif context.regime is Regime.VOLATILE:
        negative.append("market regime volatile: wider outcomes than the analogs suggest")
    strength = context.relative_strength.get(20)
    if strength is not None:
        if strength > 0:
            positive.append(f"leading the benchmark by {strength:+.4f} log return over 20 sessions")
        else:
            negative.append(f"lagging the benchmark by {strength:+.4f} log return over 20 sessions")
    if context.volume_ratio is not None and context.volume_ratio >= 1.5:
        positive.append(f"last session traded {context.volume_ratio:.2f}x its 20-session average")
    if confirmation is Confirmation.DISAGREES:
        negative.append("the market context points against the catalyst")
    if prediction is not None and prediction.status is ForecastStatus.OK:
        if prediction.p_underperform_benchmark is not None:
            if prediction.p_underperform_benchmark >= 0.5:
                negative.append(
                    "the historical analogs underperformed the benchmark in "
                    f"{prediction.p_underperform_benchmark:.0%} of cases"
                )
            else:
                positive.append(
                    "the historical analogs beat the benchmark in "
                    f"{1 - prediction.p_underperform_benchmark:.0%} of cases"
                )
        if prediction.baseline == "unconditional":
            negative.append(
                "the forecast fell back to an unconditional base: too few regime analogs"
            )
        for note in prediction.notes:
            negative.append(f"forecast caveat: {note}")
    return tuple(dict.fromkeys(positive)), tuple(dict.fromkeys(negative))


def _freshness(
    context: MarketContext,
    events: Sequence[ResearchEvent],
    as_of: datetime,
    sessions_between: int | None,
) -> DataFreshness:
    notes: list[str] = []
    newest = max((e.known_at for e in events), default=None)
    age_days = None if newest is None else round((as_of - newest).total_seconds() / 86400, 2)
    stale = sessions_between is not None and sessions_between > STALE_AFTER_SESSIONS
    if stale:
        notes.append(
            f"the last settled bar is {sessions_between} session(s) old (more than "
            f"{STALE_AFTER_SESSIONS}): the forecast is not current"
        )
    if context.bar_date is None:
        notes.append("no settled daily bars for this instrument")
    if age_days is not None and age_days > 30:
        notes.append(f"the newest catalyst is {age_days} days old")
    return DataFreshness(
        last_bar_date=context.bar_date,
        bar_age_sessions=sessions_between,
        newest_event_at=newest,
        event_age_days=age_days,
        stale=stale,
        notes=tuple(notes),
    )


def _unsupported(
    card_asset: AssetClass,
    context: MarketContext,
    prediction: Forecast | None,
    benchmark: DailySeries | None,
    sector: str | None,
) -> tuple[str, ...]:
    """Everything this card could not produce, named. An empty tuple means nothing is missing."""
    missing: list[str] = []
    if card_asset is AssetClass.UNSUPPORTED:
        missing.append("asset class unsupported: no configured data source or instrument mapping")
    if context.bars == 0:
        missing.append("no settled daily bars, so no features, forecast or market context")
    if benchmark is None:
        missing.append("no benchmark series: relative strength and underperformance are unknown")
    if not sector:
        missing.append("no sector: this instrument shares the risk engine's 'Unknown' bucket")
    if prediction is None:
        missing.append("no forecast was produced")
    elif prediction.status is ForecastStatus.INSUFFICIENT_EVIDENCE:
        missing.append("insufficient historical evidence for a forecast")
    elif prediction.status is ForecastStatus.UNSUPPORTED_DATA:
        missing.append("forecast refused: the price source is simulated or synthetic")
    if prediction is not None and prediction.p_underperform_benchmark is None:
        missing.append("no estimate of underperformance versus the benchmark")
    return tuple(missing)


def build_cards(
    *,
    events: Sequence[ResearchEvent],
    instruments: Mapping[str, Instrument],
    series: Mapping[str, DailySeries],
    features: Mapping[str, Features],
    regime: Regime | None = None,
    benchmark: DailySeries | None = None,
    as_of: datetime,
    horizon: int = DEFAULT_HORIZON,
    sessions_since: Mapping[str, int] | None = None,
) -> list[CandidateCard]:
    """One card per instrument touched by an event, plus context and a forecast for each.

    ``sessions_since`` optionally gives the settled-bar age per instrument key, for freshness.
    """
    by_instrument: dict[str, list[ResearchEvent]] = defaultdict(list)
    for event in events:
        # Superseded events are dropped: the correction carries the evidence. Unsupported assets
        # are kept, because "we cannot price this" is itself something the card must show.
        if event.is_current and event.instrument_key is not None:
            by_instrument[event.instrument_key].append(event)

    cards: list[CandidateCard] = []
    ages = sessions_since or {}
    for instrument_key, own in by_instrument.items():
        instrument = instruments.get(instrument_key)
        impacts = map_events(own)
        issuer = [i for i in impacts if i.scope is ImpactScope.ISSUER]
        issuer_by_event = {i.event_id: i for i in issuer}
        asset = own[0].asset if own else AssetClass.UNSUPPORTED
        bars = series.get(instrument_key)
        context = market_context(
            instrument_key,
            features.get(instrument_key),
            regime=regime,
            relative_strength_by_lookback=relative_strength(bars, benchmark),
        )
        prediction = (
            forecast(bars, horizon=horizon, as_of=as_of, regime=regime, benchmark=benchmark)
            if bars is not None
            else None
        )
        leading = issuer[0].direction if issuer else ImpactDirection.UNKNOWN
        confirmation, why = agreement(context, leading)
        decision: StatusDecision = decide_status(
            impacts=impacts, forecast=prediction, context=context, confirmation=confirmation
        )
        positive, negative = _factors(issuer, context, prediction, confirmation)
        sector = (instrument.sector or None) if instrument is not None else None
        cards.append(
            CandidateCard(
                instrument_key=instrument_key,
                symbol=instrument.symbol if instrument is not None else own[0].symbol,
                company=own[0].company or (instrument.name if instrument is not None else ""),
                sector=sector,
                asset=asset,
                horizon_sessions=horizon,
                status=decision.status,
                as_of=as_of,
                events=_card_events(own, issuer_by_event, impacts),
                forecast=prediction,
                context=context,
                confirmation=confirmation,
                confirmation_reason=why,
                positive_factors=positive,
                negative_factors=negative,
                reasons=decision.reasons,
                confirm_if=decision.confirm_if,
                weaken_if=decision.weaken_if,
                invalidate_if=decision.invalidate_if,
                unsupported=_unsupported(asset, context, prediction, benchmark, sector),
                freshness=_freshness(context, own, as_of, ages.get(instrument_key)),
            )
        )
    cards.sort(key=lambda c: (STATUS_RANK.get(c.status, 9), c.symbol, c.instrument_key))
    return cards


def _card_events(
    events: Sequence[ResearchEvent],
    issuer_by_event: Mapping[str, EventImpact],
    impacts: Sequence[EventImpact],
) -> tuple[CardEvent, ...]:
    """One card event per research event, each paired with *its own* impact.

    Pairing by event id rather than by position: an event can produce a sector spillover as well
    as an issuer impact, and an unsupported event produces none, so index alignment would attach
    the wrong evidence to the wrong catalyst.
    """
    fallback = {i.event_id: i for i in impacts}
    out: list[CardEvent] = []
    for event in events:
        impact = issuer_by_event.get(event.event_id) or fallback.get(event.event_id)
        if impact is None:
            continue
        out.append(_card_event(event, impact))
    return tuple(out)
