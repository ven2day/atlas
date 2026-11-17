"""Event-to-asset mapping: which instruments, sectors and assets an event can reach, and how.

Deliberately **rules first**. The table below is the whole of the initial mapping: it is short,
reviewable, and every row says which channel an event type acts through, what scope it can reach
and whether the direction is knowable from the type alone. An LLM (the existing Laya/Jev
cascade, via ``TypedEvent.direction``) may refine a direction, but it may not invent one: where
the rule says the direction is unknowable and the model is uncalibrated or unsure, the impact is
labelled ``uncertain`` rather than resolved to a guess. Where the rule and the model disagree the
answer is ``MIXED``, never a silent pick.

Two things this module refuses to do:

* It never concludes "positive sounding, therefore good for the stock". A fundraise is
  ``MIXED`` by rule (growth capital versus dilution); an insider filing is ``UNKNOWN`` by rule
  because the direction lives in the text, not the category.
* It never emits a market-wide impact. NSE's corporate RSS is issuer-level, so there is no
  evidence in this repository for a market-scope channel; claiming one would be fabrication.
  ``ImpactScope.MARKET`` exists so an authorized macro source can be added later.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum

from src.domain.types import AnnouncementType, EventDirection, Materiality
from src.research.events import AssetClass, EventChannel, ResearchEvent


class ImpactDirection(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    MIXED = "mixed"
    NEUTRAL = "neutral"
    UNKNOWN = "unknown"


class ImpactScope(StrEnum):
    """How far an effect can reach: the issuer, its sector, or the market."""

    ISSUER = "issuer"
    SECTOR = "sector"
    MARKET = "market"


@dataclass(frozen=True, slots=True)
class Evidence:
    """One cited input. A mapping without evidence is not a mapping."""

    field: str  # what was read: "exchange_subject", "classified_direction", "rule", ...
    value: str  # the value it held
    note: str = ""


@dataclass(frozen=True, slots=True)
class EventImpact:
    """One way one event can reach one asset."""

    event_id: str
    instrument_key: str | None
    symbol: str
    asset: AssetClass
    sector: str | None
    scope: ImpactScope
    channel: EventChannel
    direction: ImpactDirection
    materiality: Materiality | None
    confidence: float | None
    basis: str  # "rule" | "model" | "rule+model" | "unsupported"
    uncertain: bool
    evidence: tuple[Evidence, ...]
    reason: str


# Announcement type -> (channel, direction knowable from the type alone, sector spillover).
# ``None`` for the direction means "the type does not tell you; ask the text or stay unknown".
_RULES: Mapping[AnnouncementType, tuple[EventChannel, ImpactDirection | None, bool]] = {
    AnnouncementType.RESULTS: (EventChannel.DEMAND, None, True),
    AnnouncementType.RESULTS_DATE: (EventChannel.RISK_SENTIMENT, None, False),
    AnnouncementType.DIVIDEND: (EventChannel.CAPITAL_STRUCTURE, ImpactDirection.POSITIVE, False),
    AnnouncementType.SPLIT_BONUS: (EventChannel.CAPITAL_STRUCTURE, ImpactDirection.NEUTRAL, False),
    AnnouncementType.PLEDGE: (EventChannel.GOVERNANCE, ImpactDirection.NEGATIVE, False),
    AnnouncementType.INSIDER_OR_PROMOTER: (EventChannel.RISK_SENTIMENT, None, False),
    AnnouncementType.ORDER_WIN: (EventChannel.DEMAND, ImpactDirection.POSITIVE, True),
    AnnouncementType.LITIGATION_OR_REGULATORY: (
        EventChannel.POLICY,
        ImpactDirection.NEGATIVE,
        True,
    ),
    AnnouncementType.MANAGEMENT_CHANGE: (EventChannel.GOVERNANCE, None, False),
    AnnouncementType.RATING_CHANGE: (EventChannel.COSTS, None, False),
    AnnouncementType.FUNDRAISE: (EventChannel.CAPITAL_STRUCTURE, ImpactDirection.MIXED, False),
    AnnouncementType.OTHER: (EventChannel.UNKNOWN, None, False),
}

# The model's own direction vocabulary, mapped onto ours. "unclear" is *unknown*, not neutral.
_MODEL_DIRECTION: Mapping[EventDirection, ImpactDirection] = {
    EventDirection.POSITIVE: ImpactDirection.POSITIVE,
    EventDirection.NEGATIVE: ImpactDirection.NEGATIVE,
    EventDirection.NEUTRAL: ImpactDirection.NEUTRAL,
    EventDirection.UNCLEAR: ImpactDirection.UNKNOWN,
}

# Below this classification confidence a model-only direction is reported as uncertain.
MODEL_CONFIDENCE_FLOOR = 0.6
# A sector spillover is a weaker claim than the issuer effect it is inferred from.
SECTOR_CONFIDENCE_FACTOR = 0.5


def _confidence(
    source: float | None, classification: float | None, *, uses_model: bool
) -> float | None:
    """Source trust, times the reading's confidence when the reading is what carries the claim."""
    if source is None:
        return None
    if not uses_model:
        return round(source, 4)
    if classification is None:
        return None  # the claim rests on a reading we have no confidence for
    return round(source * classification, 4)


def _resolve(
    rule_direction: ImpactDirection | None,
    model_direction: ImpactDirection | None,
    model_confidence: float | None,
) -> tuple[ImpactDirection, bool, str]:
    """Combine the rule's prior with the model's reading. Returns (direction, uncertain, why)."""
    if rule_direction is None:
        if model_direction is None:
            return (
                ImpactDirection.UNKNOWN,
                True,
                "neither the event type nor a reading gives a direction",
            )
        confident = model_confidence is not None and model_confidence >= MODEL_CONFIDENCE_FLOOR
        return (
            model_direction,
            not confident,
            "direction comes only from the classification"
            + ("" if confident else ", below the confidence floor"),
        )
    if model_direction is None or model_direction is ImpactDirection.UNKNOWN:
        return rule_direction, False, "direction is fixed by the rule for this event type"
    if model_direction is rule_direction:
        return rule_direction, False, "the classification agrees with the rule"
    if model_direction is ImpactDirection.NEUTRAL:
        return rule_direction, True, "the classification calls it immaterial; the rule disagrees"
    return (
        ImpactDirection.MIXED,
        True,
        f"the rule says {rule_direction.value} and the classification says {model_direction.value}",
    )


def map_event(event: ResearchEvent) -> tuple[EventImpact, ...]:
    """Every impact of one event: the issuer's, and a sector spillover where the rule allows it."""
    if event.asset is AssetClass.UNSUPPORTED:
        return (
            EventImpact(
                event_id=event.event_id,
                instrument_key=event.instrument_key,
                symbol=event.symbol,
                asset=AssetClass.UNSUPPORTED,
                sector=event.sector,
                scope=ImpactScope.ISSUER,
                channel=EventChannel.UNKNOWN,
                direction=ImpactDirection.UNKNOWN,
                materiality=event.materiality,
                confidence=None,
                basis="unsupported",
                uncertain=True,
                evidence=(
                    Evidence(
                        "instrument_key",
                        event.instrument_key or "",
                        "no configured instrument mapping or data source",
                    ),
                ),  # fmt: skip
                reason="this asset has no configured data source in the repository",
            ),
        )
    if event.event_type is None:
        channel, rule_direction, spillover = EventChannel.UNKNOWN, None, False
        basis = "none"
    else:
        channel, rule_direction, spillover = _RULES[event.event_type]
        basis = "rule"

    evidence = [
        Evidence("source", event.source, f"source confidence {event.source_confidence}"),
        Evidence("text", event.text[:300]),
    ]
    if event.subject:
        evidence.append(Evidence("exchange_subject", event.subject))
    if event.event_type is not None:
        evidence.append(Evidence("event_type", event.event_type.value,
                                 f"channel {channel.value} per the mapping rule"))  # fmt: skip

    model_direction: ImpactDirection | None = None
    if event.direction is not None:
        model_direction = _MODEL_DIRECTION[event.direction]
        evidence.append(
            Evidence(
                "classified_direction",
                event.direction.value,
                f"basis {event.classification_basis}"
                + (", calibrated" if event.calibrated else ", not calibrated"),
            )
        )
    direction, uncertain, why = _resolve(
        rule_direction, model_direction, event.classification_confidence
    )
    uses_model = model_direction is not None and model_direction is not ImpactDirection.UNKNOWN
    if uses_model:
        basis = "rule+model" if basis == "rule" else "model"
    confidence = _confidence(
        event.source_confidence, event.classification_confidence, uses_model=uses_model
    )
    if event.materiality is Materiality.MINOR:
        uncertain = True
        why = f"{why}; classified as minor materiality"

    issuer = EventImpact(
        event_id=event.event_id,
        instrument_key=event.instrument_key,
        symbol=event.symbol,
        asset=event.asset,
        sector=event.sector,
        scope=ImpactScope.ISSUER,
        channel=channel,
        direction=direction,
        materiality=event.materiality,
        confidence=confidence,
        basis=basis,
        uncertain=uncertain,
        evidence=tuple(evidence),
        reason=why,
    )
    if not spillover or not event.sector or direction is ImpactDirection.UNKNOWN:
        return (issuer,)
    # A sector spillover is inferred from the issuer effect, so it carries strictly less
    # confidence and is always flagged uncertain: it is a hint to look at the sector, not a call.
    sector_confidence = (
        None if confidence is None else round(confidence * SECTOR_CONFIDENCE_FACTOR, 4)
    )
    spillover_impact = EventImpact(
        event_id=event.event_id,
        instrument_key=None,
        symbol="",
        asset=AssetClass.SECTOR,
        sector=event.sector,
        scope=ImpactScope.SECTOR,
        channel=channel,
        direction=direction,
        materiality=event.materiality,
        confidence=sector_confidence,
        basis=basis,
        uncertain=True,
        evidence=(
            *issuer.evidence,
            Evidence(
                "rule",
                "sector_spillover",
                "inferred from the issuer effect; weaker by construction",
            ),
        ),  # fmt: skip
        reason=f"{why}; spillover to the {event.sector} sector is inferred, not observed",
    )
    return (issuer, spillover_impact)


def map_events(events: Iterable[ResearchEvent]) -> list[EventImpact]:
    """Impacts of many events in a deterministic order; superseded events are skipped, because
    the correction that replaced them carries the evidence."""
    out: list[EventImpact] = []
    for event in events:
        if not event.is_current:
            continue
        out.extend(map_event(event))
    out.sort(key=lambda i: i.event_id)
    return out
