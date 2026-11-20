"""Sync the live NSE RSS announcement collection into the historical research archive.

The engine's :class:`~src.marketdata.announcements.AnnouncementIngestor` already polls NSE's
official announcements RSS feed and stores every matched announcement as an
``AnnouncementReceived`` event - with the exchange's ``published_at`` **and** the collector's own
``received_at`` - plus ``AnnouncementCoverageGap`` events whenever a poll failed or the feed's
rolling window moved past what was last seen. This module makes those stored events available to
``scripts/research_history.py`` without a second collector and without losing provenance:

* ``EventStoreRssSource`` is a :class:`~src.research.importing.HistoricalSource` over the event
  store's own records, so the existing :class:`~src.research.importing.EventImporter` machinery
  (import rows, batching, dedup, resume, coverage reporting) does the syncing. Re-running a sync
  is a no-op: ``record_id`` is derived from the stable ``announcement_id``.
* Receipt times are the collector's real observations and are carried through verbatim
  (``capture_kind='forward_captured'``). Nothing is backdated, and a record whose published date
  is old but whose receipt is recorded keeps its true receipt: ACTUAL-mode availability is then
  the (late) receipt, exactly as the system experienced it.
* Coverage gaps are synced into ``research_coverage_gaps``, so a missed feed window is reported
  as unknown coverage, never as "no events".

**The archive is prospective.** The RSS feed holds only the newest ~165 items; the research
archive grows from the first collection onward and is not, and never becomes, a complete
historical record of NSE announcements.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from src.domain.events import (
    AnnouncementCoverageGap,
    AnnouncementReceived,
    NewsCoverageGap,
    NewsItemReceived,
    symbol_of,
)
from src.research.importing import SourcePage, SourceRecord

RSS_SOURCE = "nse_rss"
# A NewsCoverageGap whose feed never succeeded has no known start; the archive stores this
# literal rather than inventing a timestamp.
UNKNOWN_GAP_START = "unknown"


def record_from_announcement(
    announcement: AnnouncementReceived, *, seq: int | None = None
) -> SourceRecord:
    """One stored announcement as a research source record, provenance intact.

    ``source_id`` is the collector's stable ``announcement_id``, so re-syncing deduplicates; the
    raw payload keeps the company name, subject, title, url and instrument key verbatim. An
    edited feed item arrives from the collector under a *new* announcement id (the id hashes the
    title), so both versions persist side by side - nothing is overwritten.
    """
    raw: dict[str, Any] = {
        "company": announcement.company,
        "title": announcement.title,
        "subject": announcement.subject,
        "url": announcement.url,
        "instrument_key": announcement.instrument_key,
        "announcement_id": announcement.announcement_id,
        "feed_source": announcement.source,
    }
    if seq is not None:
        raw["event_seq"] = seq
    return SourceRecord(
        source=announcement.source,
        symbol=symbol_of(announcement.instrument_key),
        published_at=announcement.published_at,
        raw=raw,
        source_id=announcement.announcement_id,
        document_url=announcement.url,
        received_at=announcement.received_at,
        capture_kind="forward_captured",
    )


@dataclass(frozen=True, slots=True)
class CoverageGapRow:
    source: str
    gap_from: datetime | str  # str only for UNKNOWN_GAP_START (a feed that never succeeded)
    gap_to: datetime
    reason: str

    def as_row(self) -> Mapping[str, Any]:
        return {
            "source": self.source,
            "gap_from": self.gap_from,
            "gap_to": self.gap_to,
            "reason": self.reason,
        }


def records_from_news(
    item: NewsItemReceived, *, seq: int | None = None, event_type: str | None = None
) -> list[SourceRecord]:
    """One research record per **resolved** symbol match of a stored news item.

    An unresolved (ambiguous) mention is deliberately not synced: research rows need an
    instrument, and guessing one would price the wrong company. The item itself stays in the
    event store and the News screen either way. ``source_id`` combines the stable ``news_id``
    with the symbol, so re-syncing deduplicates and a story matched to two symbols keeps two
    auditable rows. ``received_at`` is the collector's real observation - never backdated.
    """
    out: list[SourceRecord] = []
    for match in item.symbols:
        if not match.resolved or not match.symbol:
            continue
        raw: dict[str, Any] = {
            "title": item.title,
            "summary": item.summary,
            "url": item.url,
            "news_id": item.news_id,
            "source_key": item.source_key,
            "source_name": item.source_name,
            "source_type": item.source_type,
            "content_hash": item.content_hash,
            "duplicate_of": item.duplicate_of,
            "symbol_evidence": match.evidence,
            "instrument_key": match.instrument_key,
        }
        if event_type:
            raw["event_type"] = event_type
        if seq is not None:
            raw["event_seq"] = seq
        out.append(
            SourceRecord(
                source=f"news:{item.source_key}",
                symbol=match.symbol.upper(),
                published_at=item.published_at,
                raw=raw,
                source_id=f"{item.news_id}:{match.symbol.upper()}",
                event_type=event_type,
                document_url=item.url,
                received_at=item.received_at,
                capture_kind="forward_captured",
            )
        )
    return out


class EventStoreRssSource:
    """A historical source over what the live collectors already stored.

    It answers from the event store's own rows, so ``answered`` is always true once constructed:
    this source cannot have a transport failure, and it never invents records for a window -
    a window with no stored items genuinely has none *in the collection*. News items ride the
    same sync (record-level ``source`` tells them apart: ``nse_rss`` vs ``news:<key>``).
    """

    def __init__(
        self,
        announcements: Iterable[tuple[AnnouncementReceived, int | None]],
        news: Iterable[tuple[NewsItemReceived, int | None]] = (),
        news_event_types: Mapping[tuple[str, str], str] | None = None,
    ) -> None:
        records = [record_from_announcement(payload, seq=seq) for payload, seq in announcements]
        types = news_event_types or {}
        for item, seq in news:
            for record in records_from_news(item, seq=seq):
                event_type = types.get((item.news_id, record.symbol))
                if event_type is not None:
                    record = SourceRecord(
                        source=record.source, symbol=record.symbol,
                        published_at=record.published_at, raw=record.raw,
                        source_id=record.source_id, event_type=event_type,
                        document_url=record.document_url, received_at=record.received_at,
                        capture_kind=record.capture_kind,
                    )  # fmt: skip
                records.append(record)
        self._records = tuple(records)

    @property
    def name(self) -> str:
        return RSS_SOURCE

    @property
    def supports_pagination(self) -> bool:
        return False  # the whole collection is read at once; paging it would only hide records

    @property
    def records(self) -> tuple[SourceRecord, ...]:
        return self._records

    async def fetch(
        self,
        *,
        range_from: date,
        range_to: date,
        symbols: Sequence[str],
        cursor: str | None,
        limit: int,
    ) -> SourcePage:
        wanted = {s.upper() for s in symbols}
        kept = [
            record
            for record in self._records
            if range_from <= record.published_at.date() <= range_to
            and (not wanted or record.symbol in wanted)
        ]
        kept.sort(key=lambda r: (r.published_at, r.record_id))
        return SourcePage(records=tuple(kept[:limit]), next_cursor=None, answered=True)

    async def aclose(self) -> None:
        return None


def gaps_from_events(gaps: Iterable[AnnouncementCoverageGap]) -> list[CoverageGapRow]:
    """The collector's own coverage-gap events, as rows for ``save_coverage_gaps``."""
    return [
        CoverageGapRow(
            source=gap.source, gap_from=gap.gap_from, gap_to=gap.gap_to, reason=gap.reason
        )  # fmt: skip
        for gap in gaps
    ]


def news_gaps_from_events(gaps: Iterable[NewsCoverageGap]) -> list[CoverageGapRow]:
    """News-feed coverage gaps, keyed ``news:<feed>``. A feed that never succeeded has no known
    window start; that is stored as ``UNKNOWN_GAP_START`` rather than an invented timestamp."""
    return [
        CoverageGapRow(
            source=f"news:{gap.source_key}",
            gap_from=gap.gap_from if gap.gap_from is not None else UNKNOWN_GAP_START,
            gap_to=gap.gap_to,
            reason=gap.reason,
        )
        for gap in gaps
    ]


def actual_mode_records(
    records: Sequence[Mapping[str, Any]],
) -> tuple[list[Mapping[str, Any]], int]:
    """Records eligible for ACTUAL-mode evaluation, and how many were excluded.

    ACTUAL means "what this installation really knew, and when": only rows carrying a genuine
    recorded receipt (``capture_kind='forward_captured'`` with a ``received_at_utc``) qualify. A
    backfilled import - however accurate its publication dates - is a RECONSTRUCTED-mode input,
    and labelling it ACTUAL would claim an observation that never happened.
    """
    kept: list[Mapping[str, Any]] = []
    excluded = 0
    for record in records:
        if str(record.get("capture_kind") or "") == "forward_captured" and record.get(
            "received_at_utc"
        ):
            kept.append(record)
        else:
            excluded += 1
    return kept, excluded
