"""News into the research archive and the research pipeline: honest receipts, stable ids,
dedupe, coverage gaps, and the point-in-time guard. Local stores only."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from src.domain.clock import ReplayClock
from src.domain.events import (
    NewsClassified,
    NewsCoverageGap,
    NewsItemReceived,
    NewsSymbolMatch,
)
from src.domain.types import Instrument, NewsCategory, NewsDirection, NewsHorizon, NewsImpact
from src.research import historical
from src.research.events import research_events
from src.research.rss_sync import (
    UNKNOWN_GAP_START,
    EventStoreRssSource,
    news_gaps_from_events,
    records_from_news,
)
from src.research.store import ResearchStore
from src.store.event_store import EventStore
from src.store.sink import StoreSink
from src.utils.market_time import IST

PUBLISHED = datetime(2026, 10, 5, 9, 30, tzinfo=IST)
RECEIVED = PUBLISHED + timedelta(minutes=4)
CLASSIFIED = RECEIVED + timedelta(minutes=1)
INFY = Instrument.nse_equity("INFY", name="Infosys Limited")


def news_item(
    news_id: str = "n1",
    *,
    published: datetime = PUBLISHED,
    received: datetime = RECEIVED,
    symbols: tuple[NewsSymbolMatch, ...] | None = None,
    duplicate_of: str | None = None,
) -> NewsItemReceived:
    if symbols is None:
        symbols = (
            NewsSymbolMatch(symbol="INFY", instrument_key=INFY.key, evidence="company name"),
        )
    return NewsItemReceived(
        news_id=news_id, source_key="media_x", source_name="Media X", source_type="media_news",
        title="Infosys wins an order", summary="short", url="https://x/n1",
        published_at=published, received_at=received, symbols=symbols,
        content_hash="h1", duplicate_of=duplicate_of,
    )  # fmt: skip


def classified(news_id: str = "n1", *, at: datetime = CLASSIFIED) -> NewsClassified:
    return NewsClassified(
        news_id=news_id, instrument_key=INFY.key, symbol="INFY",
        category=NewsCategory.CONTRACT_ORDER, direction=NewsDirection.BULLISH,
        impact=NewsImpact.HIGH, horizon=NewsHorizon.SESSIONS_1_3, confidence=0.35,
        confidence_basis="rule", rationale="Rules: an order win reads as bullish.",
        relevant=True, model="rule_table", rules_version="news-rules-v1", classified_at=at,
    )  # fmt: skip


# -- archive records -------------------------------------------------------------------------------


def test_records_from_news_keep_receipts_evidence_and_provenance() -> None:
    (record,) = records_from_news(news_item(), seq=7)
    assert record.source == "news:media_x"
    assert record.symbol == "INFY"
    assert record.source_id == "n1:INFY"  # stable across re-syncs
    assert record.capture_kind == "forward_captured"
    assert record.received_at == RECEIVED  # the collector's real receipt, never backdated
    assert record.published_at == PUBLISHED and record.received_at > record.published_at
    assert record.raw["symbol_evidence"] == "company name"
    assert record.raw["title"] == "Infosys wins an order"
    assert record.raw["event_seq"] == 7
    assert record.document_url == "https://x/n1"


def test_unresolved_mentions_are_not_synced_and_duplicates_keep_their_link() -> None:
    ambiguous = news_item(
        "n2",
        symbols=(NewsSymbolMatch(symbol="twin", instrument_key=None, evidence="ambiguous",
                                 resolved=False),),
    )  # fmt: skip
    assert records_from_news(ambiguous) == []  # never guessed into a research row

    dup = news_item("n3", duplicate_of="n1")
    (record,) = records_from_news(dup)
    assert record.raw["duplicate_of"] == "n1"  # provenance survives the sync


def test_a_story_matched_to_two_symbols_keeps_two_rows() -> None:
    both = news_item(
        "n4",
        symbols=(
            NewsSymbolMatch(symbol="INFY", instrument_key=INFY.key, evidence="title"),
            NewsSymbolMatch(symbol="TCS", instrument_key="NSE:EQ:TCS", evidence="title"),
        ),
    )
    records = records_from_news(both)
    assert {r.symbol for r in records} == {"INFY", "TCS"}
    assert {r.source_id for r in records} == {"n4:INFY", "n4:TCS"}


async def test_news_sync_is_idempotent_through_the_importer(tmp_path: Path) -> None:
    items = [(news_item(), None), (news_item("n2", duplicate_of="n1"), None)]
    source = EventStoreRssSource([], news=items)
    with ResearchStore(tmp_path / "rq.db") as store:
        first = await historical.import_history(
            store, EventStoreRssSource([], news=items), range_from=PUBLISHED.date(),
            range_to=PUBLISHED.date(),
        )  # fmt: skip
        assert first.status == "completed" and first.inserted == 2
        rows = store.source_records()
        assert {r["source"] for r in rows} == {"news:media_x"}
        assert all(r["capture_kind"] == "forward_captured" for r in rows)

        second = await historical.import_history(
            store, source, range_from=PUBLISHED.date(), range_to=PUBLISHED.date()
        )
        assert second.inserted == 0 and second.duplicates == 2  # re-sync is a no-op
        assert len(store.source_records()) == 2
        del first, second


def test_news_coverage_gaps_sync_once_and_admit_an_unknown_start(tmp_path: Path) -> None:
    gaps = [
        NewsCoverageGap(
            source_key="media_x", gap_from=PUBLISHED, gap_to=RECEIVED, reason="HTTP 500"
        ),
        NewsCoverageGap(
            source_key="media_y", gap_from=None, gap_to=RECEIVED, reason="never succeeded"
        ),
    ]
    rows = [g.as_row() for g in news_gaps_from_events(gaps)]
    assert rows[1]["gap_from"] == UNKNOWN_GAP_START  # honest, not an invented timestamp
    with ResearchStore(tmp_path / "rq.db") as store:
        store.save_coverage_gaps(rows)
        store.save_coverage_gaps(rows)  # a second sync adds nothing
        stored = store.coverage_gaps()
        assert len(stored) == 2
        assert {g["source"] for g in stored} == {"news:media_x", "news:media_y"}


# -- the research pipeline -------------------------------------------------------------------------


def _store_with(tmp_path: Path, *payloads) -> EventStore:
    store = EventStore(tmp_path / "rq.db")
    sink = StoreSink(store, ReplayClock(CLASSIFIED), "test")
    for payload in payloads:
        sink.emit(payload, source="news")
    return store


def test_classified_news_becomes_a_research_event_with_mapped_fields(tmp_path: Path) -> None:
    store = _store_with(tmp_path, news_item(), classified())
    events = research_events(store, instruments={INFY.key: INFY})
    assert len(events) == 1
    event = events[0]
    assert event.event_id == "n1|INFY" and event.source == "news:media_x"
    assert event.event_type is None or event.event_type.value == "order_win"
    assert event.direction is not None and event.direction.value == "positive"
    assert event.materiality is not None and event.materiality.value == "major"
    assert event.source_confidence == 0.6  # media trust, not exchange trust
    assert event.classification_basis == "news_rule" and event.calibrated is False
    assert event.classification_confidence == 0.35
    assert event.known_at == CLASSIFIED  # publication, receipt AND classification
    store.close()


def test_the_point_in_time_guard_excludes_late_readings(tmp_path: Path) -> None:
    store = _store_with(tmp_path, news_item(), classified())
    # Before the classification existed, the event was not knowable in this form.
    early = research_events(store, instruments={INFY.key: INFY}, as_of=RECEIVED)
    assert early == []
    late = research_events(store, instruments={INFY.key: INFY}, as_of=CLASSIFIED)
    assert len(late) == 1
    store.close()


def test_unclassified_news_never_reaches_the_research_pipeline(tmp_path: Path) -> None:
    store = _store_with(tmp_path, news_item())  # collected but nobody read it
    assert research_events(store, instruments={INFY.key: INFY}) == []
    # ...and an ambiguous mention (no resolved instrument) has no research event either.
    store2 = _store_with(
        tmp_path / "sub",
        news_item("n9", symbols=(NewsSymbolMatch(symbol="twin", instrument_key=None,
                                                 evidence="ambiguous", resolved=False),)),
    )  # fmt: skip
    assert research_events(store2, instruments={INFY.key: INFY}) == []
    store.close()
    store2.close()
