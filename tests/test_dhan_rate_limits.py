"""Shared-account rate limiting for Dhan: one limiter per process across client instances, a
bounded local queue, bounded 429 retries honouring Retry-After, a cross-process SQLite pacer and
the daily-candle cache. All transports are mocked; nothing touches the network."""

from __future__ import annotations

import asyncio
import contextlib
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from src.marketdata.dhan.auth import DhanAuth
from src.marketdata.dhan.client import (
    CandleFrame,
    DhanClient,
    _Limiter,
    shared_limiter,
)
from src.marketdata.dhan.errors import DhanAuthError, DhanError, DhanRateLimitError
from src.marketdata.dhan.history_cache import DailyCandleCache
from src.marketdata.dhan.pacer import PacerTimeoutError, SqlitePacer

DAILY_BODY = {
    "open": [100.0],
    "high": [102.0],
    "low": [99.0],
    "close": [101.0],
    "volume": [1000],
    "timestamp": [1790706600],
}


def auth() -> DhanAuth:
    return DhanAuth(client_id="1000000001", access_token=SecretStr("static-token"))


class ScriptedTransport(httpx2.AsyncBaseTransport):
    """Answers each request with the next scripted response (last one repeats)."""

    def __init__(self, responses: list[httpx2.Response]) -> None:
        self.responses = responses
        self.calls = 0

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        index = min(self.calls, len(self.responses) - 1)
        self.calls += 1
        return self.responses[index]


def client_with(
    responses: list[httpx2.Response],
    *,
    base_url: str = "https://test.dhan.local/v2",
    **kwargs: object,
) -> tuple[DhanClient, ScriptedTransport]:
    transport = ScriptedTransport(responses)
    client = DhanClient(
        auth=auth(),
        base_url=base_url,
        client=httpx2.AsyncClient(transport=transport),
        **kwargs,  # type: ignore[arg-type]
    )
    return client, transport


ok = lambda: httpx2.Response(200, json=DAILY_BODY)  # noqa: E731


# -- shared limiters ----------------------------------------------------------------------------


def test_every_client_in_a_process_shares_one_limiter_per_endpoint_class() -> None:
    a = DhanClient(auth=auth(), base_url="https://shared.example/v2")
    b = DhanClient(auth=auth(), base_url="https://shared.example/v2")
    c = DhanClient(auth=auth(), base_url="https://other.example/v2")
    assert a._data is b._data  # the account budget is one, not one per instance
    assert a._quotes is b._quotes
    assert a._data is not c._data  # a different endpoint is a different budget
    assert shared_limiter("https://shared.example/v2", "data", 4.0) is a._data


async def test_the_limiter_paces_concurrent_callers() -> None:
    limiter = _Limiter(20.0)  # 50 ms between requests
    started = time.monotonic()
    await asyncio.gather(*(limiter.wait() for _ in range(4)))
    assert time.monotonic() - started >= 0.14  # three intervals after the first


async def test_the_local_queue_is_bounded_and_sheds_instead_of_stacking() -> None:
    limiter = _Limiter(0.02, max_waiters=1)  # a 50 s interval after the first call
    await limiter.wait()  # the first call passes and arms the interval
    stuck = asyncio.create_task(limiter.wait())  # now waits ~50 s: the one allowed waiter
    await asyncio.sleep(0.01)
    with pytest.raises(DhanRateLimitError, match="queue is full"):
        await limiter.wait()  # the queue is bounded: shed, never stack
    stuck.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stuck


# -- bounded 429 retry ---------------------------------------------------------------------------


async def test_a_429_is_retried_and_retry_after_is_honoured() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client, transport = client_with(
        [httpx2.Response(429, headers={"retry-after": "2"}), ok()], sleep=fake_sleep
    )
    frame = await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert len(frame) == 1 and transport.calls == 2
    assert slept == [2.0]  # the server's own Retry-After, not a guess


async def test_a_429_without_retry_after_backs_off_with_jitter() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client, transport = client_with(
        [httpx2.Response(429), httpx2.Response(429), ok()], sleep=fake_sleep
    )
    await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert transport.calls == 3 and len(slept) == 2
    assert 0.5 <= slept[0] <= 0.75  # base 0.5 + jitter <= 0.25
    assert 1.0 <= slept[1] <= 1.25  # exponential: base 1.0 + jitter


async def test_the_429_retry_is_bounded_and_then_surfaces() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client, transport = client_with(
        [httpx2.Response(429)] * 10, sleep=fake_sleep, rate_limit_retries=3
    )
    with pytest.raises(DhanRateLimitError, match="bounded"):
        await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert transport.calls == 4  # one try plus three retries, never more


async def test_retry_after_is_capped() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client, _ = client_with(
        [httpx2.Response(429, headers={"retry-after": "600"}), ok()], sleep=fake_sleep
    )
    await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert slept == [30.0]


async def test_auth_and_client_errors_are_never_retried_as_rate_limits() -> None:
    slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)

    client, transport = client_with([httpx2.Response(401)], sleep=fake_sleep)
    with pytest.raises(DhanAuthError):
        await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert transport.calls == 1 and slept == []

    client, transport = client_with([httpx2.Response(400, text="bad request")], sleep=fake_sleep)
    with pytest.raises(DhanError) as info:
        await client.historical_daily("1333", frm=date(2026, 9, 1), to=date(2026, 10, 5))
    assert not isinstance(info.value, DhanRateLimitError)  # a 400 is not a rate limit
    assert transport.calls == 1 and slept == []


# -- cross-process pacer -------------------------------------------------------------------------


async def test_two_pacers_on_one_file_share_the_account_budget(tmp_path: Path) -> None:
    path = tmp_path / "pacer.sqlite3"
    one = SqlitePacer(path, sleep=asyncio.sleep)
    two = SqlitePacer(path, sleep=asyncio.sleep)
    started = time.monotonic()
    await one.wait("data", 0.2)
    await two.wait("data", 0.2)  # a different "process" (connection) on the same file
    elapsed = time.monotonic() - started
    assert elapsed >= 0.18  # the second caller waited out the shared interval
    rows = one._conn.execute("SELECT COUNT(*) FROM dhan_request_log WHERE kind='data'").fetchone()
    assert int(rows[0]) == 2
    # A different endpoint class has its own budget and does not wait on 'data'.
    await two.wait("quote", 0.2)
    one.close()
    two.close()


async def test_the_pacer_gives_up_instead_of_queueing_forever(tmp_path: Path) -> None:
    path = tmp_path / "pacer.sqlite3"
    holder = SqlitePacer(path)
    waiter = SqlitePacer(path)
    await holder.wait("data", 5.0)  # claims the slot; the next one is 5 s out
    with pytest.raises(PacerTimeoutError, match="shared Dhan"):
        await waiter.wait("data", 5.0, max_wait_s=0.1)
    holder.close()
    waiter.close()


# -- daily candle cache ---------------------------------------------------------------------------


def frame(days: int = 3) -> CandleFrame:
    base = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp())
    return CandleFrame(
        timestamps=tuple(base + 86400 * i for i in range(days)),
        open=tuple(100.0 + i for i in range(days)),
        high=tuple(102.0 + i for i in range(days)),
        low=tuple(99.0 + i for i in range(days)),
        close=tuple(101.0 + i for i in range(days)),
        volume=tuple(1000 * (i + 1) for i in range(days)),
    )


def test_the_cache_round_trips_frames_and_exposes_their_age(tmp_path: Path) -> None:
    cache = DailyCandleCache(tmp_path / "c.sqlite3")
    fetched = datetime(2026, 10, 5, 2, 0, tzinfo=UTC)
    key = DailyCandleCache.key("1333", segment="NSE_EQ", instrument="EQUITY",
                               frm=date(2025, 1, 1), to=date(2026, 10, 5),
                               session_date=date(2026, 10, 5))  # fmt: skip
    assert cache.get(key) is None
    cache.put(key, frame(), security_id="1333", segment="NSE_EQ", instrument="EQUITY",
              frm=date(2025, 1, 1), to=date(2026, 10, 5), session_date=date(2026, 10, 5),
              fetched_at=fetched)  # fmt: skip
    hit = cache.get(key)
    assert hit is not None
    got, got_fetched = hit
    assert got.close == frame().close and got.timestamps == frame().timestamps
    assert got_fetched == fetched.replace(microsecond=0)  # age is exposed, never hidden
    # A different session date is a different key: the same window asked for a later session.
    other = DailyCandleCache.key("1333", segment="NSE_EQ", instrument="EQUITY",
                                 frm=date(2025, 1, 1), to=date(2026, 10, 5),
                                 session_date=date(2026, 10, 6))  # fmt: skip
    assert cache.get(other) is None
    cache.close()


def test_the_cache_prunes_old_entries(tmp_path: Path) -> None:
    cache = DailyCandleCache(tmp_path / "c.sqlite3", max_age_days=7)
    old = datetime.now(UTC) - timedelta(days=10)
    cache.put("k1", frame(), security_id="1", segment="NSE_EQ", instrument="EQUITY",
              frm=date(2025, 1, 1), to=date(2026, 1, 1), session_date=date(2026, 1, 1),
              fetched_at=old)  # fmt: skip
    cache.put("k2", frame(), security_id="2", segment="NSE_EQ", instrument="EQUITY",
              frm=date(2025, 1, 1), to=date(2026, 1, 1), session_date=date(2026, 1, 1))  # fmt: skip
    assert cache.prune() == 1
    assert cache.get("k1") is None and cache.get("k2") is not None
    cache.close()


async def test_a_cache_hit_skips_the_client_and_is_labelled_in_the_result(tmp_path: Path) -> None:
    from src.domain.types import Instrument
    from src.marketdata.dhan.history import DhanHistorySource
    from tests.test_dhan_sources import SETTLED_BEFORE, FakeDhan, daily_frame

    infy = Instrument.nse_equity("INFY", broker_tokens={"dhan": "1333"})
    cache = DailyCandleCache(tmp_path / "c.sqlite3")
    first = FakeDhan(daily={"1333": daily_frame(70)})
    result = await DhanHistorySource(client=first, cache=cache).fetch(  # type: ignore[arg-type]
        [infy], settled_before=SETTLED_BEFORE, expected_last=None
    )
    assert len(first.daily_calls) == 1 and infy.key in result.series
    assert result.cached == {}  # the first fetch is fresh, and says so

    second = FakeDhan(daily={"1333": daily_frame(70)})
    source = DhanHistorySource(client=second, cache=cache)
    again = await source.fetch([infy], settled_before=SETTLED_BEFORE, expected_last=None)
    assert second.daily_calls == []  # the shared budget was not spent again
    assert infy.key in again.cached  # ... and the reuse is labelled with its fetch time
    datetime.fromisoformat(again.cached[infy.key])
    assert again.series[infy.key].frame["close"].equals(result.series[infy.key].frame["close"])
    cache.close()
