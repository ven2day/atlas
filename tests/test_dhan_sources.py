"""The Dhan quote and history sources, against fake clients: normalisation into the engine's
own types, honest timestamps, staleness, and the failures a session must survive."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pandas as pd
import pytest

from src.config.errors import ConfigError
from src.domain.clock import ReplayClock
from src.domain.events import FeedStale, QuoteRejected
from src.domain.sink import RecordingSink
from src.domain.types import Instrument, MarketDataSource
from src.engine.market import INDEX_KEY
from src.marketdata.composite import CompositeHistorySource
from src.marketdata.dhan.client import CandleFrame
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.history import DhanHistorySource
from src.marketdata.dhan.quotes import DhanQuoteSource
from src.marketdata.history import DailySeries, HistoryResult
from src.utils.market_time import IST

NOW = datetime(2026, 10, 5, 4, 45, 0, tzinfo=UTC)  # 10:15 IST
SETTLED_BEFORE = datetime(2026, 10, 5).date()

INFY = Instrument.nse_equity("INFY", broker_tokens={"dhan": "1333"})
TCS = Instrument.nse_equity("TCS", broker_tokens={"dhan": "4000"})
NOMAP = Instrument.nse_equity("NEWLIST")  # not in the scrip master: no security id


def payload(
    security_id: str,
    *,
    ltp: float = 1542.0,
    trade_time: str = "05/10/2026 10:14:55",
    net_change: float = -4.28,
    volume: int = 123456,
) -> dict[str, Any]:
    return {
        "last_price": ltp,
        "volume": volume,
        "net_change": net_change,
        "last_trade_time": trade_time,
    }  # fmt: skip


class QuotePayload:
    """Stands in for the client's parsed quote, keyed by security id."""

    def __init__(
        self,
        security_id: str,
        last_price: float,
        volume: int,
        prev_close: float | None,
        last_trade_time: str | None,
    ) -> None:
        self.security_id = security_id
        self.last_price = last_price
        self.volume = volume
        self.prev_close = prev_close
        self.last_trade_time = last_trade_time


class FakeDhan:
    def __init__(
        self,
        *,
        quotes: dict[str, Any] | None = None,
        daily: dict[str, CandleFrame] | None = None,
        error: DhanError | None = None,
    ) -> None:
        self.quote_payloads = quotes or {}
        self.daily = daily or {}
        self.error = error
        self.quote_calls = 0
        self.daily_calls: list[tuple[str, Any, Any]] = []

    async def quotes(self, security_ids: Any) -> dict[str, Any]:
        self.quote_calls += 1
        if self.error is not None:
            raise self.error
        return self.quote_payloads

    async def historical_daily(
        self,
        security_id: str,
        *,
        frm: Any,
        to: Any,
        segment: str = "NSE_EQ",
        instrument: str = "EQUITY",
    ) -> CandleFrame:
        self.daily_calls.append((security_id, frm, to))
        if self.error is not None:
            raise self.error
        return self.daily.get(security_id, CandleFrame((), (), (), (), (), ()))


def quote_source(
    fake: FakeDhan, instruments: Any = None, **kwargs: Any
) -> tuple[DhanQuoteSource, RecordingSink]:
    clock = kwargs.pop("clock", ReplayClock(NOW))
    sink = RecordingSink(clock)
    source = DhanQuoteSource(
        instruments if instruments is not None else [INFY, TCS, NOMAP],
        clock=clock, sink=sink, client=fake,  # type: ignore[arg-type]
        market_open=kwargs.pop("market_open", lambda _ts: True), **kwargs,
    )  # fmt: skip
    return source, sink


# -- quotes -----------------------------------------------------------------------------------


async def test_a_batched_quote_becomes_an_engine_quote_with_an_honest_time() -> None:
    fake = FakeDhan(
        quotes={
            "1333": QuotePayload("1333", 1542.0, 123456, 1546.28, "05/10/2026 10:14:55"),
            "4000": QuotePayload("4000", 3980.0, 900, 3984.0, "01/01/1980 00:00:00"),
        }
    )
    source, _ = quote_source(fake)
    result = await source.poll()
    assert fake.quote_calls == 1  # one batched request for the whole universe
    assert result.ok and set(result.quotes) == {INFY.key}
    quote = result.quotes[INFY.key]
    assert quote.source is MarketDataSource.DHAN
    assert quote.is_delayed is False
    assert quote.ltp == 1542.0 and quote.volume_cum == 123456
    assert quote.prev_close == pytest.approx(1546.28)
    assert quote.exchange_ts == datetime(2026, 10, 5, 10, 14, 55, tzinfo=IST)
    # The placeholder trade time and the unmapped symbol are missing, never fabricated.
    assert set(result.missing) == {TCS.key, NOMAP.key}


async def test_a_quote_without_a_plausible_trade_time_is_missing_not_fake() -> None:
    fake = FakeDhan(quotes={"1333": QuotePayload("1333", 1542.0, 10, None, "01/01/1980 00:00:00")})
    source, _ = quote_source(fake)
    result = await source.poll()
    assert result.quotes == {} and INFY.key in result.missing


async def test_an_out_of_band_price_is_rejected_with_an_event() -> None:
    fake = FakeDhan(
        quotes={"1333": QuotePayload("1333", 4000.0, 10, 1546.28, "05/10/2026 10:14:55")}
    )
    source, sink = quote_source(fake)
    source.set_prev_closes({INFY.key: 1546.28})
    result = await source.poll()
    assert result.quotes == {} and result.rejected == (INFY.key,)
    (event,) = sink.payloads(QuoteRejected)
    assert event.reason == "out_of_band" and event.source is MarketDataSource.DHAN


async def test_a_quiet_feed_goes_stale_and_recovers() -> None:
    old = (NOW - timedelta(minutes=10)).astimezone(IST).strftime("%d/%m/%Y %H:%M:%S")
    fake = FakeDhan(quotes={"1333": QuotePayload("1333", 1542.0, 10, 1546.28, old)})
    source, sink = quote_source(fake, [INFY], stale_after_s=120.0)
    await source.poll()
    assert source.stale_keys == frozenset({INFY.key})
    assert len(sink.payloads(FeedStale)) == 1
    fake.quote_payloads = {"1333": QuotePayload("1333", 1542.0, 10, 1546.28, "05/10/2026 10:14:55")}
    await source.poll()
    assert source.stale_keys == frozenset()


async def test_a_client_failure_backs_off_and_marks_the_feed_down() -> None:
    fake = FakeDhan(error=DhanError("dhan /marketfeed/quote: HTTP 500"))
    source, sink = quote_source(fake)
    first = await source.poll()
    assert not first.ok and first.error is not None
    assert source.next_delay_s > 60.0  # backed off from the 60 s poll interval
    assert len(sink.payloads(FeedStale)) == 1


def test_a_universe_without_any_security_id_is_refused() -> None:
    sink = RecordingSink(ReplayClock(NOW))
    with pytest.raises(ConfigError, match="security id"):
        DhanQuoteSource([NOMAP], clock=ReplayClock(NOW), sink=sink, client=FakeDhan())  # type: ignore[arg-type]


# -- history ----------------------------------------------------------------------------------


def daily_frame(days: int, end: Any = SETTLED_BEFORE) -> CandleFrame:
    stamps, opens, highs, lows, closes, volumes = [], [], [], [], [], []
    for i in range(days):
        day = end - timedelta(days=days - 1 - i)
        stamps.append(int(datetime(day.year, day.month, day.day, tzinfo=IST).timestamp()))
        opens.append(100.0 + i)
        highs.append(102.0 + i)
        lows.append(99.0 + i)
        closes.append(101.0 + i)
        volumes.append(1000 + i)
    return CandleFrame(
        tuple(stamps), tuple(opens), tuple(highs), tuple(lows), tuple(closes), tuple(volumes)
    )


def _series(key: str, days: int = 70) -> DailySeries:
    frame = daily_frame(days)
    index = pd.DatetimeIndex(
        [pd.Timestamp(datetime.fromtimestamp(ts, tz=IST).date()) for ts in frame.timestamps],
        name="date",
    )
    return DailySeries(
        key,
        pd.DataFrame(
            {
                "open": list(frame.open),
                "high": list(frame.high),
                "low": list(frame.low),
                "close": list(frame.close),
                "volume": list(frame.volume),
                "dividend": 0.0,
                "split": 0.0,
                "factor": 1.0,
            },
            index=index,
        ),
    )


async def test_daily_candles_become_a_series_of_settled_dhan_bars() -> None:
    fake = FakeDhan(daily={"1333": daily_frame(70)})
    source = DhanHistorySource(client=fake)  # type: ignore[arg-type]
    result = await source.fetch([INFY], settled_before=SETTLED_BEFORE, expected_last=None)
    series = result.series[INFY.key]
    assert series.last_date == SETTLED_BEFORE - timedelta(days=1)
    bars = series.bars(adjusted=False)
    assert all(b.source is MarketDataSource.DHAN and b.is_settled for b in bars)
    assert series.adjusted()["close"].equals(
        series.raw()["close"]
    )  # factor 1: no corporate actions
    (_, frm, to) = fake.daily_calls[0]
    assert to == SETTLED_BEFORE  # toDate is non-inclusive: nothing unsettled can arrive


async def test_a_short_or_unmapped_history_is_reported_not_padded() -> None:
    fake = FakeDhan(daily={"1333": daily_frame(5)})
    source = DhanHistorySource(client=fake)  # type: ignore[arg-type]
    result = await source.fetch([INFY, NOMAP], settled_before=SETTLED_BEFORE, expected_last=None)
    assert result.series == {}
    assert "settled bars" in result.failed[INFY.key]
    assert "security id" in result.failed[NOMAP.key]


async def test_a_series_ending_before_the_previous_session_is_flagged_lagging() -> None:
    fake = FakeDhan(daily={"1333": daily_frame(70, end=SETTLED_BEFORE - timedelta(days=3))})
    source = DhanHistorySource(client=fake)  # type: ignore[arg-type]
    result = await source.fetch(
        [INFY], settled_before=SETTLED_BEFORE, expected_last=SETTLED_BEFORE - timedelta(days=1)
    )
    assert result.lagging == {INFY.key: SETTLED_BEFORE - timedelta(days=3)}


async def test_a_client_failure_fails_every_instrument_with_the_reason() -> None:
    fake = FakeDhan(error=DhanError("dhan /charts/historical: network failure"))
    source = DhanHistorySource(client=fake)  # type: ignore[arg-type]
    result = await source.fetch([INFY, TCS], settled_before=SETTLED_BEFORE, expected_last=None)
    assert result.series == {} and set(result.failed) == {INFY.key, TCS.key}


async def test_the_composite_routes_the_index_to_the_secondary_source() -> None:
    primary = HistoryResult(series={INFY.key: _series(INFY.key)})
    secondary = HistoryResult(series={INDEX_KEY: _series(INDEX_KEY)})

    class Primary:
        seen: dict[str, Any] = {}

        async def fetch(
            self, instruments: Any, *, settled_before: Any, expected_last: Any, extra: Any = None
        ) -> HistoryResult:
            Primary.seen["extra"] = extra
            return primary

    class Secondary:
        seen: dict[str, Any] = {}

        async def fetch(
            self, instruments: Any, *, settled_before: Any, expected_last: Any, extra: Any = None
        ) -> HistoryResult:
            Secondary.seen["instruments"] = list(instruments)
            Secondary.seen["extra"] = extra
            return secondary

    composite = CompositeHistorySource(
        primary=Primary(), secondary=Secondary(), secondary_keys={INDEX_KEY}
    )  # fmt: skip
    result = await composite.fetch(
        [INFY], settled_before=SETTLED_BEFORE, expected_last=None,
        extra={INDEX_KEY: "^NSEI"},
    )  # fmt: skip
    assert set(result.series) == {INFY.key, INDEX_KEY}
    assert result.failed == {}
    assert not Primary.seen["extra"]  # the index never reaches the Dhan source
    assert Secondary.seen["instruments"] == []
    assert Secondary.seen["extra"] == {INDEX_KEY: "^NSEI"}


class _Scripted:
    def __init__(self, result: HistoryResult) -> None:
        self.result = result
        self.seen: dict[str, Any] = {}

    async def fetch(
        self, instruments: Any, *, settled_before: Any, expected_last: Any, extra: Any = None
    ) -> HistoryResult:
        self.seen["extra"] = dict(extra or {})
        return self.result


async def test_a_dhan_index_failure_falls_back_to_the_secondary_source() -> None:
    """The regime must not go UNKNOWN because Dhan rate-limited, while Yahoo can serve the index:
    a key routed to the primary but listed as a fallback key is re-answered by the secondary."""
    primary = _Scripted(
        HistoryResult(
            series={INFY.key: _series(INFY.key)},
            failed={INDEX_KEY: "dhan /charts/historical: rate limited (HTTP 429)"},
        )
    )
    secondary = _Scripted(HistoryResult(series={INDEX_KEY: _series(INDEX_KEY)}))
    composite = CompositeHistorySource(
        primary=primary, secondary=secondary, secondary_keys=set(),
        secondary_fallback_keys={INDEX_KEY},
    )  # fmt: skip
    result = await composite.fetch(
        [INFY], settled_before=SETTLED_BEFORE, expected_last=None, extra={INDEX_KEY: "^NSEI"}
    )
    assert secondary.seen["extra"] == {INDEX_KEY: "^NSEI"}  # the fallback was actually fetched
    assert INDEX_KEY in result.series and result.failed == {}
    assert result.series[INDEX_KEY].source is MarketDataSource.YFINANCE  # labelled, not blended


async def test_when_both_sources_fail_the_reasons_are_joined_not_hidden() -> None:
    primary = _Scripted(HistoryResult(series={}, failed={INDEX_KEY: "rate limited (HTTP 429)"}))
    secondary = _Scripted(HistoryResult(series={}, failed={INDEX_KEY: "no data returned"}))
    composite = CompositeHistorySource(
        primary=primary, secondary=secondary, secondary_keys=set(),
        secondary_fallback_keys={INDEX_KEY},
    )  # fmt: skip
    result = await composite.fetch(
        [], settled_before=SETTLED_BEFORE, expected_last=None, extra={INDEX_KEY: "^NSEI"}
    )
    assert result.series == {}
    reason = result.failed[INDEX_KEY]
    assert "rate limited (HTTP 429)" in reason and "no data returned" in reason
