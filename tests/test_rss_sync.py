"""The RSS -> research archive sync: idempotency, stable ids, honest timestamps, corrections,
coverage gaps, and the ACTUAL/RECONSTRUCTED separation. All fixtures are synthetic and local."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from src.domain.clock import ReplayClock
from src.domain.events import AnnouncementCoverageGap, AnnouncementReceived
from src.marketdata.announcements import announcement_id
from src.research import historical
from src.research.importing import ImportReport, SourceRecord
from src.research.rss_sync import (
    EventStoreRssSource,
    actual_mode_records,
    gaps_from_events,
    record_from_announcement,
)
from src.research.store import ResearchStore
from src.store.event_store import EventStore
from src.store.sink import StoreSink
from src.utils.market_time import IST

PUBLISHED = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
RECEIVED = PUBLISHED + timedelta(minutes=4)
KEY = "NSE:EQ:INFY"


def announcement(
    *,
    title: str = "Q2 FY27 results",
    subject: str = "Financial Results",
    published: datetime = PUBLISHED,
    received: datetime = RECEIVED,
    instrument_key: str = KEY,
    url: str | None = "https://nsearchives.nseindia.com/content/x.pdf",
) -> AnnouncementReceived:
    company = "Infosys Limited"
    return AnnouncementReceived(
        announcement_id=announcement_id(instrument_key, published, title),
        instrument_key=instrument_key,
        company=company,
        published_at=published,
        received_at=received,
        title=title,
        subject=subject,
        url=url,
        source="nse_rss",
    )


def collect(db: Path, payloads: list[object]) -> list[object]:
    """Store payloads as real events and read them back the way the sync does."""
    clock = ReplayClock(RECEIVED)
    with EventStore(db) as store:
        sink = StoreSink(store, clock, "test")
        for payload in payloads:
            sink.emit(payload, source="announcements")  # type: ignore[arg-type]
        return store.read(types=["AnnouncementReceived", "AnnouncementCoverageGap"])


async def sync(store: ResearchStore, events: list[object]) -> ImportReport:
    announcements = [
        (e.payload, e.seq)  # type: ignore[attr-defined]
        for e in events
        if isinstance(e.payload, AnnouncementReceived)  # type: ignore[attr-defined]
    ]
    source = EventStoreRssSource(announcements)
    return await historical.import_history(
        store, source, range_from=date(2020, 1, 1), range_to=date(2026, 12, 31)
    )


# -- idempotency and stable ids -----------------------------------------------------------------


async def test_syncing_twice_is_a_no_op_with_stable_ids(tmp_path: Path) -> None:
    db = tmp_path / "rq.db"
    events = collect(db, [announcement(), announcement(title="Dividend record date",
                                                       subject="Dividend")])  # fmt: skip
    with ResearchStore(db) as store:
        first = await sync(store, events)
        assert first.status == "completed" and first.inserted == 2 and first.duplicates == 0
        before = {r["record_id"] for r in store.source_records()}
        second = await sync(store, events)
        assert second.inserted == 0 and second.duplicates == 2
        assert {r["record_id"] for r in store.source_records()} == before


def test_record_ids_derive_from_the_collector_stable_announcement_id() -> None:
    a = announcement()
    record = record_from_announcement(a, seq=7)
    assert record.source_id == a.announcement_id
    assert record.record_id == record_from_announcement(a, seq=99).record_id  # seq is provenance
    assert record.capture_kind == "forward_captured"
    assert record.received_at == RECEIVED
    assert record.raw["company"] == "Infosys Limited"
    assert record.raw["event_seq"] == 7


# -- timestamps ---------------------------------------------------------------------------------


async def test_receipt_and_publication_times_stay_separate_and_nothing_is_backdated(
    tmp_path: Path,
) -> None:
    db = tmp_path / "rq.db"
    events = collect(db, [announcement()])
    with ResearchStore(db) as store:
        await sync(store, events)
        (row,) = store.source_records()
    published = datetime.fromisoformat(row["published_at_utc"])
    received = datetime.fromisoformat(row["received_at_utc"])
    imported = datetime.fromisoformat(row["imported_at_utc"])
    assert published == PUBLISHED
    # The receipt is the collector's real observation, four minutes after publication - not the
    # publication time, and not the sync time either.
    assert received == RECEIVED
    assert received > published
    assert imported >= received  # the sync ran after the collector saw it; never backdated
    assert row["capture_kind"] == "forward_captured"


async def test_an_old_announcement_received_late_keeps_its_true_late_receipt(
    tmp_path: Path,
) -> None:
    """A record first collected now, published long ago, is stored with the real (late) receipt:
    it can never masquerade as something the system saw on the publication day."""
    db = tmp_path / "rq.db"
    old_published = datetime(2024, 3, 1, 12, 0, tzinfo=IST)
    events = collect(db, [announcement(published=old_published, received=RECEIVED)])
    with ResearchStore(db) as store:
        await sync(store, events)
        (row,) = store.source_records()
    received = datetime.fromisoformat(row["received_at_utc"])
    published = datetime.fromisoformat(row["published_at_utc"])
    assert (received - published) > timedelta(days=900)  # the import delay is preserved, not hidden


# -- corrections and edits ----------------------------------------------------------------------


async def test_changed_content_under_a_stable_id_becomes_a_revision_not_an_overwrite(
    tmp_path: Path,
) -> None:
    base = SourceRecord(
        source="nse_rss", symbol="INFY", published_at=PUBLISHED,
        raw={"title": "original"}, source_id="stable-1",
    )  # fmt: skip
    changed = SourceRecord(
        source="nse_rss", symbol="INFY", published_at=PUBLISHED,
        raw={"title": "corrected by the exchange"}, source_id="stable-1",
    )  # fmt: skip
    with ResearchStore(tmp_path / "rq.db") as store:
        assert store.save_source_records([base], batch="b1") == (1, 0, 0)
        inserted, duplicates, rejected = store.save_source_records([changed], batch="b2")
        assert (inserted, duplicates, rejected) == (0, 1, 0)
        (row,) = store.source_records()
        assert '"original"' in row["raw"]  # the first receipt stays authoritative
        revisions = store.record_revisions(row["record_id"])
        assert len(revisions) == 1
        assert "corrected" in revisions[0]["raw"]
        assert revisions[0]["import_batch"] == "b2"
        # Syncing the same changed content again adds nothing.
        store.save_source_records([changed], batch="b3")
        assert len(store.record_revisions(row["record_id"])) == 1


async def test_an_edited_feed_item_arrives_as_its_own_record(tmp_path: Path) -> None:
    """The collector hashes the title into the announcement id, so an edited item is a new event:
    both versions persist side by side instead of one silently replacing the other."""
    db = tmp_path / "rq.db"
    original = announcement(title="Board meeting outcome")
    edited = announcement(title="Board meeting outcome (revised)")
    events = collect(db, [original, edited])
    with ResearchStore(db) as store:
        report = await sync(store, events)
        assert report.inserted == 2
        titles = sorted(r["raw"] for r in store.source_records())
        assert any("revised" in t for t in titles) and any("revised" not in t for t in titles)


# -- coverage gaps ------------------------------------------------------------------------------


async def test_feed_gaps_are_synced_deduplicated_and_never_read_as_quiet_periods(
    tmp_path: Path,
) -> None:
    db = tmp_path / "rq.db"
    gap = AnnouncementCoverageGap(
        source="nse_rss", gap_from=PUBLISHED, gap_to=PUBLISHED + timedelta(hours=2),
        reason="feed_window_exceeded",
    )  # fmt: skip
    events = collect(db, [announcement(), gap])
    with ResearchStore(db) as store:
        await sync(store, events)
        gaps = [g for g in events if isinstance(g.payload, AnnouncementCoverageGap)]
        rows = [g.as_row() for g in gaps_from_events(g.payload for g in gaps)]  # type: ignore[attr-defined]
        assert store.save_coverage_gaps(rows) == 1
        assert store.save_coverage_gaps(rows) == 1  # idempotent statements, but...
        stored = store.coverage_gaps()
        assert len(stored) == 1  # ...the dedupe key means one row, not two
        assert stored[0]["reason"] == "feed_window_exceeded"
        summary = historical.coverage_summary(store)
        assert summary["coverage_gaps"][0]["source"] == "nse_rss"
        assert "UNKNOWN" in summary["warning"] or summary["coverage_gaps"]


# -- ACTUAL vs RECONSTRUCTED --------------------------------------------------------------------


def test_actual_mode_only_reads_forward_captured_records() -> None:
    forward = {"capture_kind": "forward_captured", "received_at_utc": "2026-10-05T04:34:00+00:00"}
    backfilled = {"capture_kind": "unknown", "received_at_utc": "2026-11-01T00:00:00+00:00"}
    no_receipt = {"capture_kind": "forward_captured", "received_at_utc": None}
    kept, excluded = actual_mode_records([forward, backfilled, no_receipt])
    assert kept == [forward] and excluded == 2


async def test_a_local_backfill_import_stays_out_of_actual_mode(tmp_path: Path) -> None:
    record = SourceRecord(
        source="local_file", symbol="TCS", published_at=PUBLISHED, raw={"title": "old export"}
    )
    with ResearchStore(tmp_path / "rq.db") as store:
        store.save_source_records([record], batch="b1")
        (row,) = store.source_records()
        assert row["capture_kind"] == "unknown"
        kept, excluded = actual_mode_records([row])
        assert kept == [] and excluded == 1


# -- archive health -----------------------------------------------------------------------------


async def test_coverage_summary_reports_the_archive_health(tmp_path: Path) -> None:
    db = tmp_path / "rq.db"
    events = collect(
        db,
        [
            announcement(),
            announcement(
                title="Order win",
                subject="Other",
                instrument_key="NSE:EQ:TCS",
                published=PUBLISHED + timedelta(days=1),
                received=RECEIVED + timedelta(days=1),
            ),  # fmt: skip
            AnnouncementCoverageGap(
                source="nse_rss",
                gap_from=PUBLISHED,
                gap_to=PUBLISHED + timedelta(hours=1),
                reason="fetch_failed",
            ),
        ],  # fmt: skip
    )
    with ResearchStore(db) as store:
        await sync(store, events)
        await sync(store, events)  # the second run contributes duplicates
        gaps = [e.payload for e in events if isinstance(e.payload, AnnouncementCoverageGap)]
        store.save_coverage_gaps([g.as_row() for g in gaps_from_events(gaps)])
        summary = historical.coverage_summary(store)
    assert summary["by_source"] == {"nse_rss": 2}
    assert summary["by_symbol"] == {"INFY": 1, "TCS": 1}
    assert summary["duplicates"] == 2
    assert summary["oldest_published"]["nse_rss"] < summary["newest_published"]["nse_rss"]
    # The last collection time is the newest real receipt, not the sync time.
    assert summary["last_received"]["nse_rss"].startswith("2026-10-06")
    assert len(summary["coverage_gaps"]) == 1
    assert summary["by_capture_kind"] == {"forward_captured": 2}
    assert "rolling window" in summary["rss_note"]


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
