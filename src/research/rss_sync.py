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

from src.domain.events import AnnouncementCoverageGap, AnnouncementReceived, symbol_of
from src.research.importing import SourcePage, SourceRecord

RSS_SOURCE = "nse_rss"


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
    gap_from: datetime
    gap_to: datetime
    reason: str

    def as_row(self) -> Mapping[str, Any]:
        return {
            "source": self.source,
            "gap_from": self.gap_from,
            "gap_to": self.gap_to,
            "reason": self.reason,
        }


class EventStoreRssSource:
    """A historical source over announcements the live RSS collector already stored.

    It answers from the event store's own rows, so ``answered`` is always true once constructed:
    this source cannot have a transport failure, and it never invents records for a window -
    a window with no stored announcements genuinely has none *in the collection*.
    """

    def __init__(self, announcements: Iterable[tuple[AnnouncementReceived, int | None]]) -> None:
        self._records = tuple(
            record_from_announcement(payload, seq=seq) for payload, seq in announcements
        )

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
