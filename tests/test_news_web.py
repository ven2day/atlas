"""The news REST routes over a seeded store: joins, filters, and per-feed health where a
failing feed is never presented as an empty one. Hermetic: a real EventStore in tmp_path,
no network, no engine run."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest
import yaml

from src.domain.clock import ReplayClock
from src.domain.events import (
    Alert,
    NewsClassified,
    NewsCoverageGap,
    NewsItemReceived,
    NewsSymbolMatch,
)
from src.domain.types import NewsCategory, NewsDirection, NewsHorizon, NewsImpact
from src.store.event_store import EventStore
from src.store.sink import StoreSink
from src.utils.market_time import IST
from src.web.run_manager import RunManager
from src.web.server import create_app
from tests.web_helpers import SECURITY, anonymous, authed

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
REGISTRY = {
    "feeds": [
        {
            "key": "owned",
            "name": "NSE",
            "type": "exchange_filing",
            "url": "https://x/o",
            "enabled": True,
            "verified": "2026-10-02",
            "owned_by": "announcements",
        },
        {
            "key": "off",
            "name": "Off",
            "type": "media_news",
            "url": "https://x/d",
            "enabled": False,
            "verified": "2026-10-02",
        },
        {
            "key": "unverified",
            "name": "Unverified",
            "type": "media_news",
            "url": "https://x/u",
            "enabled": True,
        },
        {
            "key": "active",
            "name": "Active Feed",
            "type": "media_news",
            "url": "https://x/a",
            "enabled": True,
            "verified": "2026-10-02",
        },
        {
            "key": "failing",
            "name": "Failing Feed",
            "type": "media_news",
            "url": "https://x/f",
            "enabled": True,
            "verified": "2026-10-02",
        },
        {
            "key": "empty",
            "name": "Empty Feed",
            "type": "media_news",
            "url": "https://x/e",
            "enabled": True,
            "verified": "2026-10-02",
        },
    ]
}


def news_item(news_id: str, source_key: str, *, published: datetime, received: datetime,
              title: str = "Infosys wins an order", duplicate_of: str | None = None,
              symbols: tuple[NewsSymbolMatch, ...] = ()) -> NewsItemReceived:  # fmt: skip
    return NewsItemReceived(
        news_id=news_id, source_key=source_key, source_name=f"{source_key} feed",
        source_type="media_news", title=title, summary="short summary",
        url=f"https://x/{news_id}", published_at=published, received_at=received,
        symbols=symbols or (NewsSymbolMatch(symbol="INFY", instrument_key="NSE:EQ:INFY",
                                            evidence="company name in the title"),),
        content_hash=f"h-{news_id}", duplicate_of=duplicate_of,
    )  # fmt: skip


@pytest.fixture
def client(settings, tmp_path: Path):
    registry = tmp_path / "feeds.yaml"
    registry.write_text(yaml.safe_dump(REGISTRY), encoding="utf-8")
    local = settings.model_copy(update={"var_dir": tmp_path, "news_feeds_file": registry})
    clock = ReplayClock(NOW)
    with EventStore(tmp_path / "rq.db") as store:
        sink = StoreSink(store, clock, "test")
        # An active feed: one fresh item (classified), one duplicate of it, one ambiguous item.
        sink.emit(
            news_item(
                "a1",
                "active",
                published=NOW - timedelta(hours=2),
                received=NOW - timedelta(hours=1),
            ),
            source="news",
        )
        sink.emit(
            news_item(
                "a2",
                "active",
                published=NOW - timedelta(hours=2),
                received=NOW - timedelta(hours=1),
                duplicate_of="a1",
                title="Infosys wins an order",
            ),
            source="news",
        )
        sink.emit(news_item("a3", "active", published=NOW - timedelta(hours=1, minutes=-30),
                            received=NOW - timedelta(minutes=20),
                            title="Twin Company announces results",
                            symbols=(NewsSymbolMatch(symbol="twin", instrument_key=None,
                                                     evidence="ambiguous", resolved=False),)),
                  source="news")  # fmt: skip
        sink.emit(NewsClassified(
            news_id="a1", instrument_key="NSE:EQ:INFY", symbol="INFY",
            category=NewsCategory.CONTRACT_ORDER, direction=NewsDirection.BULLISH,
            impact=NewsImpact.UNKNOWN, horizon=NewsHorizon.UNKNOWN, confidence=0.35,
            confidence_basis="rule", rationale="Rules: an order win reads as bullish.",
            evidence=("headline: Infosys wins an order",), uncertainties=(),
            relevant=True, model="rule_table", rules_version="news-rules-v1",
            classified_at=NOW - timedelta(minutes=55),
        ), source="news")  # fmt: skip
        # A failing feed: old items, a coverage gap and a down alert as the newest state.
        sink.emit(news_item("f1", "failing", published=NOW - timedelta(days=1),
                            received=NOW - timedelta(days=1), title="Old TCS story",
                            symbols=(NewsSymbolMatch(symbol="TCS", instrument_key="NSE:EQ:TCS",
                                                     evidence="symbol in the title"),)),
                  source="news")  # fmt: skip
        sink.emit(
            NewsCoverageGap(
                source_key="failing",
                gap_from=NOW - timedelta(hours=6),
                gap_to=NOW,
                reason="HTTP 503",
            ),
            source="news",
        )
        sink.emit(Alert(level="WARNING", key="news_feed_down:failing",
                        message="news feed 'Failing Feed' is unavailable: HTTP 503"),
                  source="news")  # fmt: skip
        manager = RunManager(local, store_path=store.path, clock=clock)
        yield authed(create_app(manager=manager, security=SECURITY))
        manager.close_reader()


def test_the_news_routes_need_the_token(settings, tmp_path):
    app = create_app(
        manager=RunManager(
            settings.model_copy(update={"var_dir": tmp_path}),
            store_path=tmp_path / "none.db",
            clock=ReplayClock(NOW),
        ),
        security=SECURITY,
    )
    anon = anonymous(app)
    assert anon.get("/api/news").status_code == 401
    assert anon.get("/api/news/feeds").status_code == 401


def test_news_rows_join_their_readings_and_keep_both_timestamps(client):
    rows = client.get("/api/news").json()
    assert [r["news_id"] for r in rows] == ["a3", "a1", "a2", "f1"]  # newest publication first
    a1 = next(r for r in rows if r["news_id"] == "a1")
    assert a1["source_name"] == "active feed" and a1["source_type"] == "media_news"
    assert a1["published_at"] != a1["received_at"]  # never conflated
    assert a1["summary"] == "short summary" and a1["url"] == "https://x/a1"
    (reading,) = a1["readings"]
    assert reading["category"] == "contract_order" and reading["direction"] == "bullish"
    assert reading["confidence_basis"] == "rule" and reading["relevant"] is True
    assert reading["rationale"].startswith("Rules:")
    # The duplicate keeps its own row and points at the first-seen item.
    a2 = next(r for r in rows if r["news_id"] == "a2")
    assert a2["duplicate_of"] == "a1" and a2["readings"] == []
    # An item with only an unresolved mention is returned *unclassified*, not dropped.
    a3 = next(r for r in rows if r["news_id"] == "a3")
    assert a3["readings"] == [] and a3["symbols"][0]["resolved"] is False


def test_news_filters(client):
    by_symbol = client.get("/api/news", params={"symbol": "INFY"}).json()
    assert {r["news_id"] for r in by_symbol} == {"a1", "a2"}  # via the reading and the mention
    ambiguous = client.get("/api/news", params={"symbol": "TWIN"}).json()
    assert [r["news_id"] for r in ambiguous] == ["a3"]  # an unresolved mention is still findable
    by_category = client.get("/api/news", params={"category": "contract_order"}).json()
    assert [r["news_id"] for r in by_category] == ["a1"]
    by_direction = client.get("/api/news", params={"direction": "bullish"}).json()
    assert [r["news_id"] for r in by_direction] == ["a1"]
    by_source = client.get("/api/news", params={"source": "failing"}).json()
    assert [r["news_id"] for r in by_source] == ["f1"]
    by_day = client.get("/api/news", params={"date": (NOW - timedelta(days=1)).date().isoformat()})
    assert [r["news_id"] for r in by_day.json()] == ["f1"]
    assert len(client.get("/api/news", params={"limit": 2}).json()) == 2
    # A typo in a closed vocabulary is a 422, never a silently empty list.
    assert client.get("/api/news", params={"category": "bogus"}).status_code == 422
    assert client.get("/api/news", params={"direction": "moon"}).status_code == 422


def test_feed_health_never_reads_a_failure_as_quiet(client):
    feeds = {f["key"]: f for f in client.get("/api/news/feeds").json()}
    assert feeds["owned"]["status"] == "owned"
    assert feeds["off"]["status"] == "disabled"
    assert feeds["unverified"]["status"] == "unverified" and feeds["unverified"]["verified"] is None
    assert feeds["active"]["status"] == "active" and feeds["active"]["items_total"] == 3
    assert feeds["empty"]["status"] == "empty" and feeds["empty"]["items_total"] == 0
    failing = feeds["failing"]
    assert failing["status"] == "failing"  # the down alert is the newest recorded state
    assert "503" in (failing["last_error"] or "")
    assert failing["gaps"] == 1 and failing["items_total"] == 1
    assert failing["last_item_at"] is not None
