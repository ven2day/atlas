"""The news collector, offline: registry rules, SSRF validation, RSS/Atom parsing, symbol
matching with evidence, dedupe/provenance, honest timestamps, and failure-as-gap (never as
"no news"). Every transport is mocked; no test touches the network."""

from __future__ import annotations

import socket
from datetime import datetime
from pathlib import Path

import httpx2
import pytest
import yaml

from src.domain.clock import ReplayClock
from src.domain.events import Alert, NewsCoverageGap, NewsItemReceived
from src.domain.sink import RecordingSink
from src.domain.types import Instrument
from src.marketdata.news import (
    FeedError,
    FeedSpec,
    NewsCollector,
    NewsSymbolMatcher,
    UnsafeFeedURLError,
    load_news_feeds,
    news_watermark,
    parse_news_feed,
    validate_feed_url,
)
from src.utils.market_time import IST

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
PUBLIC = "93.184.216.34"  # a public literal, so validation needs no DNS
INFY = Instrument.nse_equity("INFY", name="Infosys Limited")
TCS = Instrument.nse_equity("TCS", name="Tata Consultancy Services Limited")


def feed(key: str = "f1", url: str = f"http://{PUBLIC}/feed.xml", **kw) -> FeedSpec:
    defaults = dict(name=f"Feed {key}", type="media_news", enabled=True, verified="2026-11-19",
                    min_interval_s=60.0)  # fmt: skip
    return FeedSpec(key=key, url=url, **{**defaults, **kw})


def rss(items: list[tuple[str, str, str, str]], *, ttl: int | None = 5) -> bytes:
    parts = "".join(
        f"<item><title>{t}</title><link>{u}</link><guid>{g}</guid><pubDate>{p}</pubDate></item>"
        for t, u, g, p in items
    )
    channel = f"<ttl>{ttl}</ttl>" if ttl else ""
    return f"<rss version='2.0'><channel>{channel}{parts}</channel></rss>".encode()


PUBDATE = "Mon, 05 Oct 2026 09:30:00 +0530"
ITEM = ("Infosys Limited wins a major contract", f"http://{PUBLIC}/a1", "g1", PUBDATE)


def collector_for(
    handler,
    feeds: list[FeedSpec],
    instruments=(INFY, TCS),
    **kwargs,
) -> tuple[NewsCollector, RecordingSink]:
    clock = ReplayClock(NOW)
    sink = RecordingSink(clock)
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler), timeout=5.0)
    collector = NewsCollector(
        feeds=feeds, instruments=instruments, clock=clock, sink=sink, client=client, **kwargs
    )
    return collector, sink


# -- registry -------------------------------------------------------------------------------------


def test_registry_only_fetches_enabled_and_verified_feeds(tmp_path: Path) -> None:
    registry = tmp_path / "feeds.yaml"
    registry.write_text(
        yaml.safe_dump(
            {
                "feeds": [
                    {
                        "key": "ok",
                        "name": "Ok",
                        "type": "media_news",
                        "url": f"http://{PUBLIC}/ok",
                        "enabled": True,
                        "verified": "2026-11-19",
                    },
                    {
                        "key": "unverified",
                        "name": "Unverified",
                        "type": "media_news",
                        "url": f"http://{PUBLIC}/u",
                        "enabled": True,
                    },
                    {
                        "key": "off",
                        "name": "Off",
                        "type": "media_news",
                        "url": f"http://{PUBLIC}/o",
                        "enabled": False,
                        "verified": "2026-11-19",
                    },
                    {
                        "key": "nse",
                        "name": "NSE",
                        "type": "exchange_filing",
                        "url": "https://x/y",
                        "enabled": True,
                        "verified": "2026-10-02",
                        "owned_by": "announcements",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    feeds = load_news_feeds(registry)
    assert [f.key for f in feeds if f.fetchable] == ["ok"]  # never assumed to work


def test_a_broken_registry_is_an_error_not_an_empty_list(tmp_path: Path) -> None:
    bad = tmp_path / "feeds.yaml"
    bad.write_text(
        yaml.safe_dump(
            {
                "feeds": [
                    {"key": "a", "name": "A", "type": "media_news", "url": "http://x/a"},
                    {"key": "a", "name": "A2", "type": "media_news", "url": "http://x/b"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(FeedError, match="duplicate"):
        load_news_feeds(bad)
    missing = tmp_path / "missing.yaml"
    with pytest.raises(FeedError, match="cannot read"):
        load_news_feeds(missing)


# -- SSRF validation ------------------------------------------------------------------------------


def test_url_validation_refuses_non_public_destinations() -> None:
    with pytest.raises(UnsafeFeedURLError, match="scheme"):
        validate_feed_url("file:///etc/passwd")
    with pytest.raises(UnsafeFeedURLError, match="scheme"):
        validate_feed_url("ftp://example.com/feed")
    with pytest.raises(UnsafeFeedURLError, match="credentials"):
        validate_feed_url("http://user:pass@example.com/feed")
    for host in ("127.0.0.1", "10.1.2.3", "192.168.0.1", "169.254.1.1", "[::1]"):
        with pytest.raises(UnsafeFeedURLError, match="non-public"):
            validate_feed_url(f"http://{host}/feed.xml")

    def fake_resolver(host, _port):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 80))]

    with pytest.raises(UnsafeFeedURLError, match="non-public"):
        validate_feed_url("http://feeds.example.com/rss", resolver=fake_resolver)

    def broken_resolver(host, _port):
        raise socket.gaierror("no such host")

    with pytest.raises(FeedError, match="cannot resolve"):
        validate_feed_url("http://nope.invalid/rss", resolver=broken_resolver)
    validate_feed_url(f"http://{PUBLIC}/feed.xml")  # a public literal passes


# -- parsing --------------------------------------------------------------------------------------


def test_rss_and_atom_parse_with_ttl_and_html_stripped() -> None:
    entries, ttl = parse_news_feed(rss([ITEM]))
    assert ttl == 300.0 and len(entries) == 1
    entry = entries[0]
    assert entry.title == ITEM[0] and entry.guid == "g1" and entry.url == ITEM[1]
    assert entry.published_at == datetime(2026, 10, 5, 9, 30, tzinfo=IST)

    atom = (
        b"<feed xmlns='http://www.w3.org/2005/Atom'>"
        b"<entry><title>TCS &lt;b&gt;rises&lt;/b&gt;</title>"
        b"<link rel='alternate' href='http://x/1'/><id>tag:1</id>"
        b"<published>2026-10-05T04:00:00Z</published>"
        b"<summary>Strong &lt;em&gt;orders&lt;/em&gt; quarter</summary></entry>"
        b"<entry><title>No date</title></entry>"
        b"</feed>"
    )
    entries, ttl = parse_news_feed(atom)
    assert ttl is None and len(entries) == 1  # the dateless item is skipped, never re-stamped
    assert entries[0].title == "TCS rises"  # entities decoded, then markup stripped
    assert entries[0].summary == "Strong orders quarter"
    with pytest.raises(FeedError, match="not XML"):
        parse_news_feed(b"not xml at all")


# -- symbol matching ------------------------------------------------------------------------------


def test_symbol_matching_carries_evidence_and_never_guesses() -> None:
    matcher = NewsSymbolMatcher([INFY, TCS])
    (match,) = matcher.match("Infosys Limited wins a major contract", "")
    assert match.resolved and match.symbol == "INFY" and "title" in match.evidence

    (match,) = matcher.match("Quarterly update", "Shares of TCS ended higher")
    assert match.resolved and match.symbol == "TCS" and "summary" in match.evidence

    assert matcher.match("No company here", "nothing at all") == ()
    # A two-letter fragment must not match; word boundaries must hold.
    assert matcher.match("INFYX doubles on INFYS buzz", "") == ()

    twin_a = Instrument.nse_equity("AAA", name="Twin Company")
    twin_b = Instrument.nse_equity("BBB", name="Twin Company Ltd")
    ambiguous = NewsSymbolMatcher([twin_a, twin_b])
    (match,) = ambiguous.match("Twin Company announces results", "")
    assert not match.resolved and match.instrument_key is None  # ambiguous stays unresolved


# -- collection -----------------------------------------------------------------------------------


async def test_a_successful_poll_stores_items_with_honest_timestamps() -> None:
    handler = lambda request: httpx2.Response(200, content=rss([ITEM]))  # noqa: E731
    collector, sink = collector_for(handler, [feed()])
    stats = await collector.poll(force=True)
    assert stats.ok and len(stats.new) == 1
    (item,) = sink.payloads(NewsItemReceived)
    assert item.published_at == datetime(2026, 10, 5, 9, 30, tzinfo=IST)  # the publisher's time
    assert item.received_at == NOW  # our clock, never the publication time
    assert item.source_key == "f1" and item.source_name == "Feed f1"
    assert item.source_type == "media_news"
    assert [m.symbol for m in item.symbols] == ["INFY"]
    assert item.symbols[0].evidence  # the match is explained
    assert item.duplicate_of is None and item.summary == ""


async def test_repolling_dedupes_and_the_watermark_restores_it() -> None:
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx2.Response(200, content=rss([ITEM]))

    collector, sink = collector_for(handler, [feed()])
    first = await collector.poll(force=True)
    assert len(first.new) == 1
    second = await collector.poll(force=True)
    assert second.new == []  # the same guid never becomes a second record

    seen, by_content, last_ok = news_watermark(sink.payloads(NewsItemReceived))
    fresh, _ = collector_for(handler, [feed()], seen=seen, by_content=by_content,
                             last_ok=last_ok)  # fmt: skip
    assert (await fresh.poll(force=True)).new == []  # a restart does not re-collect


async def test_the_same_story_from_two_feeds_keeps_both_with_provenance() -> None:
    feeds = [feed("a", f"http://{PUBLIC}/a.xml"), feed("b", f"http://{PUBLIC}/b.xml")]

    def handler(request):
        return httpx2.Response(200, content=rss([ITEM]))  # identical title in both feeds

    collector, sink = collector_for(handler, feeds)
    stats = await collector.poll(force=True)
    assert len(stats.new) == 2
    by_source = {item.source_key: item for item in sink.payloads(NewsItemReceived)}
    assert by_source["a"].duplicate_of is None  # first seen keeps the original
    assert by_source["b"].duplicate_of == by_source["a"].news_id  # the later one points at it
    assert by_source["b"].news_id != by_source["a"].news_id  # both records persist


async def test_a_304_is_not_modified_not_an_error() -> None:
    seen = {"n": 0}

    def handler(request):
        seen["n"] += 1
        if seen["n"] == 1:
            return httpx2.Response(200, content=rss([ITEM]),
                                   headers={"etag": '"v1"'})  # fmt: skip
        assert request.headers.get("if-none-match") == '"v1"'  # the conditional GET is sent
        return httpx2.Response(304)

    collector, sink = collector_for(handler, [feed()])
    assert len((await collector.poll(force=True)).new) == 1
    second = await collector.poll(force=True)
    assert second.ok and second.feeds[0].not_modified and second.new == []


async def test_a_failing_feed_is_a_gap_and_an_alert_never_silence() -> None:
    state = {"fail": True}

    def handler(request):
        if state["fail"]:
            return httpx2.Response(500, text="boom")
        return httpx2.Response(200, content=rss([ITEM]))

    collector, sink = collector_for(handler, [feed()])
    first = await collector.poll(force=True)
    assert not first.ok and first.feeds[0].error is not None and first.new == []
    (alert,) = sink.payloads(Alert)
    assert alert.key == "news_feed_down:f1" and alert.level == "WARNING"
    (gap,) = sink.payloads(NewsCoverageGap)
    assert gap.source_key == "f1" and gap.gap_from is None  # never succeeded: start unknown
    assert gap.gap_to == NOW and "500" in gap.reason

    await collector.poll(force=True)  # still failing: no duplicate alert, no duplicate gap
    assert len(sink.payloads(Alert)) == 1 and len(sink.payloads(NewsCoverageGap)) == 1

    state["fail"] = False
    recovered = await collector.poll(force=True)
    assert recovered.ok and len(recovered.new) == 1
    alerts = sink.payloads(Alert)
    assert any(a.key == "news_feed_recovered:f1" and a.level == "INFO" for a in alerts)


async def test_oversized_responses_and_private_redirects_are_refused() -> None:
    big = rss([ITEM]) + b" " * 500

    def handler(request):
        return httpx2.Response(200, content=big)

    collector, _ = collector_for(handler, [feed()], max_bytes=100)
    stats = await collector.poll(force=True)
    assert not stats.ok and "byte cap" in (stats.feeds[0].error or "")

    def redirector(request):
        return httpx2.Response(302, headers={"location": "http://127.0.0.1/internal"})

    collector, _ = collector_for(redirector, [feed()])
    stats = await collector.poll(force=True)
    assert not stats.ok and "non-public" in (stats.feeds[0].error or "")


async def test_the_feed_interval_is_respected_and_the_ttl_raises_it() -> None:
    handler = lambda request: httpx2.Response(200, content=rss([ITEM], ttl=5))  # noqa: E731
    collector, _ = collector_for(handler, [feed(min_interval_s=60.0)])
    await collector.poll(force=True)
    assert collector.interval_s == 300.0  # the feed's own ttl (5 min) wins over 60 s
    assert (await collector.poll()).feeds == []  # not due yet: nothing is fetched


async def test_unverified_and_owned_feeds_are_never_polled() -> None:
    def handler(request):
        pytest.fail("a non-fetchable feed must never be requested")

    feeds = [
        feed("owned", owned_by="announcements"),
        feed("unverified", verified=None),
        feed("off", enabled=False),
    ]
    collector, _ = collector_for(handler, feeds)
    stats = await collector.poll(force=True)  # the handler would fail on any request
    assert stats.ok and stats.feeds == [] and stats.new == []
    assert collector.interval_s == 300.0  # the default cadence, nothing feed-specific
