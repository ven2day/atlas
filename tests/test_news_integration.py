"""Offline integration: the real paper path collects news before the market opens, persists it
to the session store, and the console's GET /api/news returns it.

Regression guard for the 2026-11-20 bug: the news task used to start only when the session
entered OPEN, so an evening or weekend run (which never leaves PRE_OPEN) polled nothing and the
News screen showed zero items for verified feeds with no log line explaining why. The engine's
pre-open hook now backfills news and starts its periodic task, exactly like announcements.

No network: the feed is an httpx MockTransport on a public IP literal (so SSRF validation passes
without DNS). No broker, no orders, no credentials.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import httpx2
import yaml

from src.config.limits import load_risk_limits
from src.decision_models.tasks.news import NewsClassifier, NewsPipeline
from src.domain.calendar import get_calendar
from src.domain.clock import ReplayClock
from src.domain.events import Alert, NewsClassified, NewsCoverageGap, NewsItemReceived
from src.domain.types import Instrument, Quote
from src.engine.lifecycle import LifecycleConfig, build_schedule
from src.engine.runner import EngineConfig, build_engine
from src.marketdata.history import HistoryResult
from src.marketdata.news import FeedSpec, NewsCollector
from src.marketdata.yfinance_source import PollResult
from src.store.event_store import EventStore
from src.store.sink import StoreSink
from src.utils.market_time import IST
from src.web.run_manager import RunManager
from src.web.server import create_app
from tests.web_helpers import SECURITY, authed

# A Friday evening: the market is closed, the session will sit in PRE_OPEN forever.
EVENING = datetime(2026, 11, 20, 20, 0, tzinfo=IST)
DAY = EVENING.date()
PUBLIC = "93.184.216.34"
INFY = Instrument.nse_equity("INFY", name="Infosys Limited")

RSS = (
    "<rss version='2.0'><channel><ttl>5</ttl>"
    "<item><title>Infosys Limited wins a major contract</title>"
    f"<link>http://{PUBLIC}/a1</link><guid>g1</guid>"
    "<pubDate>Fri, 20 Nov 2026 19:30:00 +0530</pubDate></item>"
    "</channel></rss>"
).encode()


class FakeQuotes:
    @property
    def last_quotes(self) -> dict[str, Quote]:
        return {}

    @property
    def next_delay_s(self) -> float:
        return 60.0

    def set_prev_closes(self, closes: object) -> None:
        return None

    async def poll(self) -> PollResult:
        return PollResult(ok=True, quotes={})


class FakeHistory:
    async def fetch(self, instruments, *, settled_before, expected_last, extra=None):
        return HistoryResult(series={})


def pipeline_for(handler, clock, sink, instruments=(INFY,)) -> NewsPipeline:
    feed = FeedSpec(key="test_feed", name="Test Feed", type="media_news",
                    url=f"http://{PUBLIC}/feed.xml", enabled=True, verified="2026-11-20",
                    min_interval_s=60.0)  # fmt: skip
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=5.0)
    collector = NewsCollector(feeds=[feed], instruments=instruments, clock=clock, sink=sink,
                              client=client)  # fmt: skip
    return NewsPipeline(collector=collector, classifier=NewsClassifier(sink=sink, clock=clock))


async def engine_with_news(tmp_path: Path, handler, clock):
    store = EventStore(tmp_path / "rq.db")
    sink = StoreSink(store, clock, "engine")
    engine = build_engine(
        config=EngineConfig(environment="test", books=("A",), heartbeat=False),
        clock=clock, calendar=get_calendar(), store=store, quotes=FakeQuotes(),
        history=FakeHistory(), universe=[INFY], limits=load_risk_limits(),
        news=pipeline_for(handler, clock, sink),
    )  # fmt: skip
    schedule = build_schedule(get_calendar(), DAY, LifecycleConfig())
    assert schedule is not None  # a trading day
    return engine, store, schedule


async def test_a_closed_market_session_still_collects_and_serves_news(tmp_path: Path, settings):
    handler = lambda request: httpx2.Response(200, content=RSS)  # noqa: E731
    clock = ReplayClock(EVENING)
    engine, store, schedule = await engine_with_news(tmp_path, handler, clock)
    try:
        # The paper path's pre-open hook: history, regime, reconciliation - and the news backfill.
        await engine.on_pre_open(schedule)

        items = store.read(types=[NewsItemReceived.event_type])
        assert len(items) == 1  # collected although the market is closed
        item = items[0].payload
        assert isinstance(item, NewsItemReceived)
        assert item.source_key == "test_feed" and item.received_at == EVENING
        readings = store.read(types=[NewsClassified.event_type])
        assert len(readings) == 1  # the rule classifier read the resolved symbol
        assert "news" in engine.tasks.running  # the periodic task is live from PRE_OPEN
    finally:
        await engine.tasks.stop()
        store.close()

    # The console reads the very same store: the item and its reading come back.
    registry = tmp_path / "feeds.yaml"
    registry.write_text(yaml.safe_dump({"feeds": [
        {"key": "test_feed", "name": "Test Feed", "type": "media_news",
         "url": f"http://{PUBLIC}/feed.xml", "enabled": True, "verified": "2026-11-20"},
    ]}), encoding="utf-8")  # fmt: skip
    local = settings.model_copy(update={"var_dir": tmp_path, "news_feeds_file": registry})
    manager = RunManager(local, store_path=tmp_path / "rq.db", clock=clock)
    try:
        client = authed(create_app(manager=manager, security=SECURITY))
        rows = client.get("/api/news").json()
        assert len(rows) == 1
        assert rows[0]["title"] == "Infosys Limited wins a major contract"
        assert rows[0]["source_key"] == "test_feed"
        (reading,) = rows[0]["readings"]
        assert reading["category"] == "contract_order" and reading["direction"] == "bullish"
        assert reading["confidence_basis"] == "rule"

        feeds = {f["key"]: f for f in client.get("/api/news/feeds").json()}
        assert feeds["test_feed"]["status"] == "active"
        assert feeds["test_feed"]["items_total"] == 1
    finally:
        manager.close_reader()


async def test_a_failing_feed_is_recorded_not_silent(tmp_path: Path):
    handler = lambda request: httpx2.Response(503, text="unavailable")  # noqa: E731
    clock = ReplayClock(EVENING)
    engine, store, schedule = await engine_with_news(tmp_path, handler, clock)
    try:
        await engine.on_pre_open(schedule)  # must not raise: a feed outage never blocks a session
        assert store.read(types=[NewsItemReceived.event_type]) == []
        gaps = store.read(types=[NewsCoverageGap.event_type])
        assert len(gaps) == 1  # unknown coverage, recorded
        alerts = [e.payload for e in store.read(types=[Alert.event_type])]
        assert any(a.key == "news_feed_down:test_feed" for a in alerts)  # and announced
    finally:
        await engine.tasks.stop()
        store.close()
