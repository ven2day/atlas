"""
A thin, rate-limited DhanHQ v2 REST client for **market data only**.

Endpoints (verified against dhanhq.co/docs/v2 on 2026-11-14):

* ``POST /marketfeed/quote`` - batched quotes, up to 1000 instruments per request, keyed by
  exchange segment; documented limit **1 request/second**.
* ``POST /charts/historical`` - daily candles; ``toDate`` is non-inclusive; data back to the
  scrip's inception. Documented Data API limit **5 requests/second**.
* ``POST /charts/intraday`` - 1/5/15/25/60-minute candles for the last 5 years, at most
  **90 days per request**; larger windows are fetched in chunks here.
* ``GET  /profile`` - the account's Data API entitlement (``dataPlan``, ``dataValidity``).

Responses arrive as parallel arrays (charts) or nested dicts (quotes); both are normalised into
small frozen dataclasses so nothing above this layer parses vendor JSON. Every error message is
redacted and carries no credential.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

import httpx2

from src.marketdata.dhan.auth import DhanAuth
from src.marketdata.dhan.errors import (
    DhanAuthError,
    DhanError,
    DhanMalformedResponseError,
    DhanRateLimitError,
)
from src.marketdata.dhan.instruments import EXCHANGE_SEGMENT, INSTRUMENT_TYPE
from src.ops.logging_config import redact

BASE_URL = "https://api.dhan.co/v2"

# dhanhq.co/docs/v2: per-second limits, Data APIs 5 and Quote APIs 1.
DATA_REQUESTS_PER_SECOND = 5.0
QUOTE_REQUESTS_PER_SECOND = 1.0
QUOTE_BATCH_LIMIT = 1000
INTRADAY_INTERVALS = (1, 5, 15, 25, 60)
INTRADAY_MAX_WINDOW = timedelta(days=90)

DAILY_DATE_FORMAT = "%Y-%m-%d"
INTRADAY_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class _Limiter:
    """A minimum-interval gate: enough for the documented per-second caps, without a queue."""

    def __init__(self, per_second: float) -> None:
        self._interval = 1.0 / per_second
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            due = self._last + self._interval
            if due > now:
                await asyncio.sleep(due - now)
            self._last = time.monotonic()


@dataclass(frozen=True, slots=True)
class CandleFrame:
    """Normalised candles, oldest first, timestamps as epoch seconds exactly as Dhan sent them."""

    timestamps: tuple[int, ...]
    open: tuple[float, ...]
    high: tuple[float, ...]
    low: tuple[float, ...]
    close: tuple[float, ...]
    volume: tuple[int, ...]

    def __len__(self) -> int:
        return len(self.timestamps)


@dataclass(frozen=True, slots=True)
class QuotePayload:
    security_id: str
    last_price: float
    volume: int
    prev_close: float | None
    last_trade_time: str | None


@dataclass(frozen=True, slots=True)
class DataPlan:
    plan: str | None  # "Active" / "Deactive" per the docs
    validity: str | None


def _numbers(body: dict[str, Any], field: str) -> tuple[float, ...]:
    raw = body.get(field)
    if not isinstance(raw, list):
        raise DhanMalformedResponseError(f"chart response has no '{field}' array")
    out: list[float] = []
    for value in raw:
        number = float(value)
        if not math.isfinite(number):
            raise DhanMalformedResponseError(f"chart response '{field}' holds a non-finite value")
        out.append(number)
    return tuple(out)


def parse_candle_frame(body: Any) -> CandleFrame:
    """The parallel-array chart response into a :class:`CandleFrame`."""
    if not isinstance(body, dict):
        raise DhanMalformedResponseError("chart response is not a JSON object")
    stamps = body.get("timestamp")
    if not isinstance(stamps, list):
        raise DhanMalformedResponseError("chart response has no 'timestamp' array")
    open_ = _numbers(body, "open")
    high = _numbers(body, "high")
    low = _numbers(body, "low")
    close = _numbers(body, "close")
    volumes = _numbers(body, "volume")
    lengths = {len(stamps), len(open_), len(high), len(low), len(close), len(volumes)}
    if len(lengths) != 1:
        raise DhanMalformedResponseError(f"chart arrays disagree in length: {sorted(lengths)}")
    return CandleFrame(
        timestamps=tuple(int(s) for s in stamps),
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=tuple(int(v) for v in volumes),
    )


def parse_quotes(body: Any, segment: str = EXCHANGE_SEGMENT) -> dict[str, QuotePayload]:
    """The nested ``/marketfeed/quote`` response into payloads keyed by security id."""
    if not isinstance(body, dict):
        raise DhanMalformedResponseError("quote response is not a JSON object")
    data = body.get("data")
    if not isinstance(data, dict):
        raise DhanMalformedResponseError("quote response has no 'data' object")
    segment_data = data.get(segment)
    if segment_data is None:
        return {}
    if not isinstance(segment_data, dict):
        raise DhanMalformedResponseError(f"quote response segment '{segment}' is not an object")
    out: dict[str, QuotePayload] = {}
    for security_id, item in segment_data.items():
        if not isinstance(item, dict):
            continue
        last = item.get("last_price")
        if not isinstance(last, (int, float)) or not math.isfinite(float(last)) or float(last) <= 0:
            continue
        net_change = item.get("net_change")
        prev: float | None = None
        if isinstance(net_change, (int, float)) and math.isfinite(float(net_change)):
            # Documented as "absolute change in LTP from previous day closing price".
            prev = float(last) - float(net_change)
        volume = item.get("volume")
        trade_time = item.get("last_trade_time")
        out[str(security_id)] = QuotePayload(
            security_id=str(security_id),
            last_price=float(last),
            volume=int(volume)
            if isinstance(volume, (int, float)) and math.isfinite(float(volume))
            else 0,
            prev_close=prev,
            last_trade_time=str(trade_time) if isinstance(trade_time, str) else None,
        )
    return out


class DhanClient:
    def __init__(
        self,
        *,
        auth: DhanAuth,
        base_url: str = BASE_URL,
        client: httpx2.AsyncClient | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self._auth = auth
        self._base = base_url.rstrip("/")
        self._client = client if client is not None else httpx2.AsyncClient(timeout=timeout_s)
        self._owns_client = client is None
        self._data = _Limiter(DATA_REQUESTS_PER_SECOND)
        self._quotes = _Limiter(QUOTE_REQUESTS_PER_SECOND)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # -- transport ---------------------------------------------------------------------------

    async def _call(self, path: str, *, limiter: _Limiter, body: dict[str, Any] | None) -> Any:
        await limiter.wait()
        token = await self._auth.token()
        headers = {
            "access-token": token,
            "client-id": self._auth.client_id,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        try:
            response = await self._client.request(
                "POST" if body is not None else "GET",
                f"{self._base}{path}",
                headers=headers,
                json=body,
            )
        except httpx2.HTTPError as exc:
            raise DhanError(f"dhan {path}: network failure ({type(exc).__name__})") from exc
        if response.status_code in (401, 403):
            self._auth.invalidate()
            raise DhanAuthError(
                f"dhan {path}: the access token was rejected (HTTP {response.status_code}); "
                "regenerate it, or check DHAN_ACCESS_TOKEN / DHAN_PIN / DHAN_TOTP_SECRET",
                status=response.status_code,
            )
        if response.status_code == 429:
            raise DhanRateLimitError(
                f"dhan {path}: rate limited (HTTP 429); the documented caps are 5 data and "
                "1 quote request per second",
                status=429,
            )
        if response.status_code >= 400:
            raise DhanError(
                f"dhan {path}: HTTP {response.status_code}: {redact(response.text[:300])}",
                status=response.status_code,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise DhanMalformedResponseError(f"dhan {path}: response is not JSON") from exc

    # -- endpoints ---------------------------------------------------------------------------

    async def profile(self) -> DataPlan:
        """``GET /profile``: the Data API subscription state (``dataPlan`` / ``dataValidity``)."""
        body = await self._call("/profile", limiter=self._data, body=None)
        if not isinstance(body, dict):
            raise DhanMalformedResponseError("profile response is not a JSON object")
        data = body.get("data") if isinstance(body.get("data"), dict) else body
        plan = data.get("dataPlan") if isinstance(data, dict) else None
        validity = data.get("dataValidity") if isinstance(data, dict) else None
        return DataPlan(
            plan=str(plan) if plan is not None else None,
            validity=str(validity) if validity is not None else None,
        )

    async def quotes(self, by_segment: Mapping[str, Sequence[str]]) -> dict[str, QuotePayload]:
        """Batched full quotes (``POST /marketfeed/quote``): one request per segment, each
        chunked at 1000 instruments."""
        out: dict[str, QuotePayload] = {}
        for segment, ids in by_segment.items():
            # The master's security ids are numeric; anything else is master-data drift and is
            # left out (that instrument then reads as missing, never as a guessed id).
            clean = [str(s) for s in dict.fromkeys(ids) if str(s).isdigit()]
            for start in range(0, len(clean), QUOTE_BATCH_LIMIT):
                chunk = clean[start : start + QUOTE_BATCH_LIMIT]
                body = await self._call(
                    "/marketfeed/quote",
                    limiter=self._quotes,
                    body={segment: [int(i) for i in chunk]},
                )
                out |= parse_quotes(body, segment)
        return out

    async def historical_daily(
        self,
        security_id: str,
        *,
        frm: date,
        to: date,
        segment: str = EXCHANGE_SEGMENT,
        instrument: str = INSTRUMENT_TYPE,
    ) -> CandleFrame:
        """Daily candles (``POST /charts/historical``); ``to`` is non-inclusive, per the docs."""
        if to <= frm:
            raise ValueError("the daily window must be non-empty (toDate is non-inclusive)")
        body = await self._call(
            "/charts/historical",
            limiter=self._data,
            body={
                "securityId": str(security_id),
                "exchangeSegment": segment,
                "instrument": instrument,
                "oi": False,
                "fromDate": frm.strftime(DAILY_DATE_FORMAT),
                "toDate": to.strftime(DAILY_DATE_FORMAT),
            },
        )
        return parse_candle_frame(body)

    async def historical_intraday(
        self,
        security_id: str,
        *,
        start: datetime,
        end: datetime,
        interval_minutes: int,
        segment: str = EXCHANGE_SEGMENT,
        instrument: str = INSTRUMENT_TYPE,
    ) -> CandleFrame:
        """Minute candles (``POST /charts/intraday``), fetched in <=90-day chunks and merged."""
        if interval_minutes not in INTRADAY_INTERVALS:
            raise ValueError(
                f"interval must be one of {INTRADAY_INTERVALS}, got {interval_minutes}"
            )
        if end <= start:
            raise ValueError("the intraday window must be non-empty")
        merged: dict[int, tuple[float, float, float, float, int]] = {}
        cursor = start
        while cursor < end:
            chunk_end = min(cursor + INTRADAY_MAX_WINDOW, end)
            body = await self._call(
                "/charts/intraday",
                limiter=self._data,
                body={
                    "securityId": str(security_id),
                    "exchangeSegment": segment,
                    "instrument": instrument,
                    "interval": str(interval_minutes),
                    "oi": False,
                    "fromDate": cursor.strftime(INTRADAY_DATE_FORMAT),
                    "toDate": chunk_end.strftime(INTRADAY_DATE_FORMAT),
                },
            )
            frame = parse_candle_frame(body)
            for i, stamp in enumerate(frame.timestamps):
                merged[stamp] = (
                    frame.open[i],
                    frame.high[i],
                    frame.low[i],
                    frame.close[i],
                    frame.volume[i],
                )
            cursor = chunk_end
        stamps = tuple(sorted(merged))
        rows = [merged[s] for s in stamps]
        return CandleFrame(
            timestamps=stamps,
            open=tuple(r[0] for r in rows),
            high=tuple(r[1] for r in rows),
            low=tuple(r[2] for r in rows),
            close=tuple(r[3] for r in rows),
            volume=tuple(r[4] for r in rows),
        )
