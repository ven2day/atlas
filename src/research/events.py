"""Normalized, point-in-time research events.

The announcement pipeline already does the ingestion and the classification: an
``AnnouncementReceived`` is the point-in-time record (the exchange's ``published_at`` and our
``received_at``) and a ``TypedEvent`` is its classification. This module re-implements neither.
It joins the two into one ``ResearchEvent`` and adds the three things a decision-support path
needs that the trading gate does not:

* **``known_at``** includes publication, receipt and classification availability. A run timestamped ``as_of`` sees only
  events with ``known_at <= as_of``, so a late-published or future-dated story can never leak
  into an earlier decision. This is the look-ahead guard for research and evaluation alike.
* **Supersession instead of silent deduplication.** The feed's own repeats are already
  deduplicated by ``announcement_id``; what is left is a *correction* or a re-issue, which must
  stay visible. Later events of the same type for the same instrument record the ids they
  update, and the earlier ones record what superseded them.
* **An explicit asset class**, including ``UNSUPPORTED`` for anything the repository has no
  configured source and instrument mapping for. Unsupported is reported, never papered over
  with a placeholder price.

``source_confidence`` and ``classification_confidence`` are kept apart on purpose: trusting NSE's
own feed is not the same as trusting a model's reading of one item in it.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING

from src.domain.events import AnnouncementReceived, NewsClassified, NewsItemReceived
from src.domain.types import (
    AnnouncementType,
    EventDirection,
    Instrument,
    Materiality,
    NewsCategory,
    NewsDirection,
    NewsImpact,
    TypedEvent,
)
from src.utils.market_time import IST

if TYPE_CHECKING:
    from src.store.event_store import EventStore

# How much to trust the feed itself, before any reading of an individual item. Only sources this
# repository actually reads are listed; an unlisted source is *unknown*, not silently trusted.
SOURCE_CONFIDENCE: Mapping[str, float] = {"nse_rss": 0.95}
# News feeds are secondary reporting, not the exchange's own record: less trust by construction,
# and an official macro feed slightly more than general media. These are judgment constants, not
# measurements - deliberately visible here rather than buried.
NEWS_SOURCE_CONFIDENCE: Mapping[str, float] = {"media_news": 0.6, "macro": 0.7}

# The news taxonomy is wider than the exchange's announcement types; only unambiguous
# equivalences are mapped, everything else stays None rather than being forced into a wrong type.
NEWS_CATEGORY_AS_ANNOUNCEMENT: Mapping[NewsCategory, AnnouncementType | None] = {
    NewsCategory.RESULTS_GUIDANCE: AnnouncementType.RESULTS,
    NewsCategory.CONTRACT_ORDER: AnnouncementType.ORDER_WIN,
    NewsCategory.ACQUISITION: None,
    NewsCategory.CORPORATE_ACTION: None,
    NewsCategory.BOARD_MEETING: None,
    NewsCategory.REGULATORY_LEGAL: AnnouncementType.LITIGATION_OR_REGULATORY,
    NewsCategory.CREDIT_RATING: AnnouncementType.RATING_CHANGE,
    NewsCategory.PROMOTER_INSIDER: AnnouncementType.INSIDER_OR_PROMOTER,
    NewsCategory.MACRO_POLICY: None,
    NewsCategory.MARKET_WIDE: None,
    NewsCategory.RUMOR: None,
    NewsCategory.OTHER: AnnouncementType.OTHER,
    NewsCategory.UNKNOWN: None,
}
# MIXED has no EventDirection equivalent; UNCLEAR ("cannot tell one-sided") is the honest
# mapping, and the news reading keeps its own MIXED value on the NewsClassified event.
NEWS_DIRECTION_AS_EVENT: Mapping[NewsDirection, EventDirection | None] = {
    NewsDirection.BULLISH: EventDirection.POSITIVE,
    NewsDirection.BEARISH: EventDirection.NEGATIVE,
    NewsDirection.NEUTRAL: EventDirection.NEUTRAL,
    NewsDirection.MIXED: EventDirection.UNCLEAR,
    NewsDirection.UNKNOWN: None,
}
NEWS_IMPACT_AS_MATERIALITY: Mapping[NewsImpact, Materiality | None] = {
    NewsImpact.HIGH: Materiality.MAJOR,
    NewsImpact.MEDIUM: Materiality.MODERATE,
    NewsImpact.LOW: Materiality.MINOR,
    NewsImpact.UNKNOWN: None,
}

# A re-issue that says so outright supersedes the previous event of the same type for the
# instrument, within this many days.
CORRECTION_WINDOW_DAYS = 10
_CORRECTION_WORDS = ("correction", "corrected", "revision", "revised", "amendment", "amended",
                     "update to", "restated", "clarification")  # fmt: skip


class AssetClass(StrEnum):
    """What an event or a forecast is about. ``UNSUPPORTED`` is a first-class answer."""

    EQUITY = "equity"
    INDEX = "index"
    SECTOR = "sector"
    UNSUPPORTED = "unsupported"


class EventChannel(StrEnum):
    """The mechanism by which an event can reach a price. One event may act through several,
    and they may point in opposite directions - that is ``MIXED``, not an average."""

    DEMAND = "demand"
    COSTS = "costs"
    POLICY = "policy"
    CURRENCY = "currency"
    RATES = "rates"
    RISK_SENTIMENT = "risk_sentiment"
    CAPITAL_STRUCTURE = "capital_structure"
    GOVERNANCE = "governance"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ResearchEvent:
    """One normalized event, with everything needed to explain and to audit it."""

    event_id: str
    instrument_key: str | None
    symbol: str
    company: str
    sector: str | None
    asset: AssetClass
    source: str
    source_url: str | None
    published_at: datetime  # the exchange's own timestamp (aware)
    ingested_at: datetime | None  # when we received it (aware); None if unclassified-only
    event_time: datetime | None  # when the underlying event happens, if it is scheduled
    known_at: datetime  # includes classification availability when classified
    event_type: AnnouncementType | None
    subject: str
    text: str  # the original text, so a claim is always traceable
    relevant: bool | None
    direction: EventDirection | None
    materiality: Materiality | None
    source_confidence: float | None
    classification_confidence: float | None
    classification_basis: str  # "exchange_subject" | "model" | "none"
    calibrated: bool
    updates: tuple[str, ...] = ()  # event ids this one corrects or re-issues
    superseded_by: str | None = None

    @property
    def is_current(self) -> bool:
        """False once a later event has superseded this one."""
        return self.superseded_by is None


def _symbol_of(instrument: Instrument | None, instrument_key: str | None) -> str:
    if instrument is not None:
        return instrument.symbol
    if instrument_key is None:
        return ""
    parts = instrument_key.split(":")
    return parts[-1] if parts else instrument_key


def _asset_of(instrument: Instrument | None) -> AssetClass:
    """Only an instrument this repository actually maps gets a real asset class."""
    if instrument is None:
        return AssetClass.UNSUPPORTED
    if instrument.segment == "INDEX":
        return AssetClass.INDEX
    if instrument.series == "EQ":
        return AssetClass.EQUITY
    return AssetClass.UNSUPPORTED


def _event_time(typed: TypedEvent) -> datetime | None:
    """A scheduled event's date (a board meeting, a record date) as an IST midnight instant."""
    raw = typed.extra.get("event_date")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        year, month, day = (int(part) for part in raw.split("-", 2))
        return datetime(year, month, day, tzinfo=IST)
    except (ValueError, TypeError):
        return None


def _facts(typed: TypedEvent, announcement: AnnouncementReceived | None) -> tuple[str, ...]:
    """Deterministic, human-readable facts pulled out of the record - never an interpretation."""
    facts: list[str] = []
    if announcement is not None and announcement.subject:
        facts.append(f"exchange category: {announcement.subject}")
    if typed.materiality is not None:
        facts.append(f"materiality: {typed.materiality.value}")
    if typed.direction is not None:
        facts.append(f"classified direction: {typed.direction.value}")
    event_time = _event_time(typed)
    if event_time is not None:
        facts.append(f"scheduled event date: {event_time.date().isoformat()}")
    basis = typed.extra.get("type_source")
    if isinstance(basis, str) and basis:
        facts.append(f"event type from: {basis}")
    abstained = typed.extra.get("abstained")
    if isinstance(abstained, list) and abstained:
        facts.append(f"model abstained on: {', '.join(str(a) for a in abstained)}")
    return tuple(facts)


def _classification_confidence(typed: TypedEvent) -> float | None:
    """The weakest per-question confidence, so one unsure answer cannot be hidden by the rest."""
    best: list[float] = []
    for probabilities in typed.probabilities.values():
        if probabilities:
            best.append(max(probabilities.values()))
    return min(best) if best else None


def _subject_of(announcement: AnnouncementReceived | None, typed: TypedEvent | None) -> str:
    """The exchange's own category, from the announcement, else the copy the classifier kept."""
    if announcement is not None:
        return announcement.subject
    if typed is None:
        return ""
    subject = typed.extra.get("subject")
    return subject if isinstance(subject, str) else ""


def research_events(
    store: EventStore,
    *,
    instruments: Mapping[str, Instrument] | None = None,
    as_of: datetime | None = None,
) -> list[ResearchEvent]:
    """Every classified announcement as a ``ResearchEvent``, oldest first by ``known_at``.

    ``as_of`` applies the point-in-time guard: only events already known at that instant are
    returned. An announcement that was ingested but never classified is still returned, with no
    classification fields, because "we saw it and could not read it" is itself information.
    """
    lookup = instruments or {}
    typed_by_id: dict[str, TypedEvent] = {}
    for event in store.read(types=["TypedEvent"]):
        payload = event.payload
        if isinstance(payload, TypedEvent):
            if as_of is not None and max(event.ts_utc, payload.classified_at) > as_of:
                continue
            typed_by_id[payload.event_id] = payload
    received_by_id: dict[str, AnnouncementReceived] = {}
    for event in store.read(types=["AnnouncementReceived"]):
        payload = event.payload
        if isinstance(payload, AnnouncementReceived):
            if as_of is not None and event.ts_utc > as_of:
                continue
            received_by_id[payload.announcement_id] = payload

    ids = set(typed_by_id) | set(received_by_id)
    out: list[ResearchEvent] = []
    for event_id in ids:
        typed = typed_by_id.get(event_id)
        announcement = received_by_id.get(event_id)
        anchor = typed or announcement
        if anchor is None:  # pragma: no cover - the id came from one of the two
            continue
        instrument_key = anchor.instrument_key
        instrument = lookup.get(instrument_key)
        published = anchor.published_at
        ingested = (
            announcement.received_at
            if announcement is not None
            else (typed.classified_at if typed is not None else None)
        )
        known = published if ingested is None else max(published, ingested)
        if typed is not None:
            known = max(known, typed.classified_at)
        if as_of is not None and known > as_of:
            continue  # not knowable yet: the point-in-time guard
        subject = _subject_of(announcement, typed)
        out.append(
            ResearchEvent(
                event_id=event_id,
                instrument_key=instrument_key,
                symbol=_symbol_of(instrument, instrument_key),
                company=announcement.company if announcement is not None else "",
                sector=(instrument.sector or None) if instrument is not None else None,
                asset=_asset_of(instrument),
                source=anchor.source,
                source_url=anchor.url,
                published_at=published,
                ingested_at=ingested,
                event_time=_event_time(typed) if typed is not None else None,
                known_at=known,
                event_type=typed.announcement_type if typed is not None else None,
                subject=subject,
                text=anchor.title,
                relevant=typed.relevant if typed is not None else None,
                direction=typed.direction if typed is not None else None,
                materiality=typed.materiality if typed is not None else None,
                source_confidence=SOURCE_CONFIDENCE.get(anchor.source),
                classification_confidence=(
                    _classification_confidence(typed) if typed is not None else None
                ),
                classification_basis=str(
                    (typed.extra.get("type_source") if typed is not None else None) or "none"
                ),
                calibrated=bool(typed.calibrated) if typed is not None else False,
            )
        )
    out.extend(_news_events(store, lookup=lookup, as_of=as_of))
    out.sort(key=lambda e: (e.known_at, e.event_id))
    return _mark_superseded(out)


def _news_events(
    store: EventStore,
    *,
    lookup: Mapping[str, Instrument],
    as_of: datetime | None,
) -> list[ResearchEvent]:
    """Classified news items as research events, with the same look-ahead guard.

    Only readings attached to a *resolved* instrument become research events (an unresolved
    mention has nothing to price); the items themselves stay in the archive and the News screen
    either way. ``known_at`` includes the classification time, so a reading that arrived late
    can never leak into an earlier evaluation.
    """
    items: dict[str, NewsItemReceived] = {}
    for event in store.read(types=["NewsItemReceived"]):
        payload = event.payload
        if isinstance(payload, NewsItemReceived):
            if as_of is not None and event.ts_utc > as_of:
                continue
            items[payload.news_id] = payload
    out: list[ResearchEvent] = []
    for event in store.read(types=["NewsClassified"]):
        payload = event.payload
        if not isinstance(payload, NewsClassified):
            continue
        item = items.get(payload.news_id)
        if item is None:
            continue  # a reading without its item cannot be audited; skip rather than invent
        known = max(item.published_at, item.received_at, payload.classified_at)
        if as_of is not None and known > as_of:
            continue  # not knowable yet: the point-in-time guard
        instrument = lookup.get(payload.instrument_key)
        out.append(
            ResearchEvent(
                event_id=f"{payload.news_id}|{payload.symbol}",
                instrument_key=payload.instrument_key,
                symbol=payload.symbol,
                company="",
                sector=(instrument.sector or None) if instrument is not None else None,
                asset=_asset_of(instrument),
                source=f"news:{item.source_key}",
                source_url=item.url,
                published_at=item.published_at,
                ingested_at=item.received_at,
                event_time=None,
                known_at=known,
                event_type=NEWS_CATEGORY_AS_ANNOUNCEMENT.get(payload.category),
                subject=payload.category.value.replace("_", " "),
                text=" — ".join(part for part in (item.title, item.summary) if part),
                relevant=payload.relevant,
                direction=NEWS_DIRECTION_AS_EVENT.get(payload.direction),
                materiality=NEWS_IMPACT_AS_MATERIALITY.get(payload.impact),
                source_confidence=NEWS_SOURCE_CONFIDENCE.get(item.source_type),
                classification_confidence=payload.confidence,
                classification_basis=f"news_{payload.confidence_basis}",
                calibrated=payload.confidence_basis == "model_calibrated",
            )
        )
    return out


def _mark_superseded(events: Iterable[ResearchEvent]) -> list[ResearchEvent]:
    """Record only explicit linked corrections; same-day independent news stays current."""
    ordered = sorted(events, key=lambda e: (e.known_at, e.event_id))
    by_group: dict[tuple[str | None, str], list[ResearchEvent]] = defaultdict(list)
    updates: dict[str, list[str]] = defaultdict(list)
    replaced: dict[str, str] = {}
    for event in ordered:
        group_key = (event.instrument_key, event.event_type.value if event.event_type else "")
        prior = by_group[group_key]
        for last in reversed(prior):
            says_correction = any(word in event.text.lower() for word in _CORRECTION_WORDS)
            within_window = (event.known_at - last.known_at).days <= CORRECTION_WINDOW_DAYS
            linked = last.event_id in event.text or (
                bool(last.source_url) and last.source_url == event.source_url
            )
            if says_correction and within_window and linked:
                updates[event.event_id].append(last.event_id)
                replaced[last.event_id] = event.event_id
                break
        by_group[group_key].append(event)
    if not replaced:
        return ordered
    return [
        replace(
            event,
            updates=tuple(updates.get(event.event_id, ())),
            superseded_by=replaced.get(event.event_id),
        )
        for event in ordered
    ]
