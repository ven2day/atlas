"""
Daily history from Dhan's ``POST /charts/historical``, shaped into the engine's
``HistorySource`` protocol.

Honesty rules kept here:

* Only **settled** bars are returned: the request window ends at ``settled_before`` (the session
  date, and Dhan's ``toDate`` is non-inclusive), and any bar the response nonetheless carries at
  or after that date is dropped.
* Timestamps become IST session dates through
  :func:`~src.marketdata.dhan.timestamps.chart_session_date`; a timestamp that cannot be placed
  fails that instrument rather than shifting a candle onto a wrong day.
* Dhan serves no corporate actions, so the dividend back-adjustment factor is 1.0 and the
  adjusted series equals the raw one. That is a real limitation of the vendor, not something to
  paper over: the wiring emits a WARNING alert naming it, and every bar is stamped
  ``source=dhan`` so tapes, replays and the console can see where it came from.
* An instrument with no Dhan security id in the master is reported failed, never guessed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd

from src.domain.types import Instrument, MarketDataSource
from src.marketdata.dhan.client import DhanClient
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.instruments import DhanMapping, mapping_for
from src.marketdata.dhan.timestamps import DhanTimestampError, chart_session_date
from src.marketdata.history import DEFAULT_MIN_BARS, DailySeries, HistoryResult, record_history
from src.store.tape import TapeWriter

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 420  # ~19 months of sessions: comfortably past the 60-bar minimum

_Row = tuple[float, float, float, float, int]


class DhanHistorySource:
    def __init__(
        self,
        *,
        client: DhanClient,
        tape: TapeWriter | None = None,
        overrides: Mapping[str, DhanMapping] | None = None,
        min_bars: int = DEFAULT_MIN_BARS,
        lookback_days: int = LOOKBACK_DAYS,
        timeout_s: float = 120.0,
        concurrency: int = 4,
    ) -> None:
        self._client = client
        self._tape = tape
        self._overrides = overrides
        self._min_bars = min_bars
        self._lookback = timedelta(days=lookback_days)
        self._timeout_s = timeout_s
        self._concurrency = concurrency

    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(instrument: Instrument) -> tuple[str, DailySeries | None, str | None, int]:
            async with semaphore:
                try:
                    return await self._fetch_one(instrument, settled_before)
                except (DhanError, DhanTimestampError) as exc:
                    return instrument.key, None, str(exc), 0

        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(*(one(i) for i in instruments)), self._timeout_s
            )
        except TimeoutError:
            timeout_reason = f"dhan history timed out after {self._timeout_s:.0f}s"
            logger.warning(timeout_reason)
            outcomes = [(i.key, None, timeout_reason, 0) for i in instruments]

        series: dict[str, DailySeries] = {}
        failed: dict[str, str] = {}
        repaired: dict[str, int] = {}
        lagging: dict[str, date] = {}
        for key, daily, reason, fixed in outcomes:
            if daily is not None:
                series[key] = daily
                if fixed:
                    repaired[key] = fixed
                if expected_last is not None and daily.last_date < expected_last:
                    lagging[key] = daily.last_date
            elif reason is not None:
                failed[key] = reason
        # Indices and anything else the caller asked for by ticker: Dhan's equity master has no
        # row for them, and inventing an index security id would be worse than saying no.
        for key in extra or {}:
            failed.setdefault(key, "not an NSE cash equity in Dhan's master (routed elsewhere)")
        result = HistoryResult(series=series, failed=failed, repaired=repaired, lagging=lagging)
        if self._tape is not None:
            record_history(result, tape=self._tape, recorded_on=settled_before)
        return result

    async def _fetch_one(
        self, instrument: Instrument, settled_before: date
    ) -> tuple[str, DailySeries | None, str | None, int]:
        mapping = mapping_for(instrument, self._overrides)
        if mapping is None:
            return instrument.key, None, "no dhan security id in the instrument master", 0
        frame = await self._client.historical_daily(
            mapping.security_id,
            frm=settled_before - self._lookback,
            to=settled_before,
            segment=mapping.segment,
            instrument=mapping.instrument_type,
        )
        rows: dict[date, _Row] = {}
        repaired = 0
        for i, stamp in enumerate(frame.timestamps):
            day = chart_session_date(stamp)
            if day >= settled_before:
                continue
            o, h, low, c = frame.open[i], frame.high[i], frame.low[i], frame.close[i]
            hi, lo = max(o, h, low, c), min(o, h, low, c)
            if h != hi or low != lo:
                repaired += 1  # restore the ordering from the reported values; nothing invented
            rows[day] = (o, hi, lo, c, frame.volume[i])
        days = sorted(rows)
        if len(days) < self._min_bars:
            return (
                instrument.key,
                None,
                f"only {len(days)} settled bars from dhan (need {self._min_bars})",
                0,
            )
        frame_out = pd.DataFrame(
            {
                "open": [rows[d][0] for d in days],
                "high": [rows[d][1] for d in days],
                "low": [rows[d][2] for d in days],
                "close": [rows[d][3] for d in days],
                "volume": [rows[d][4] for d in days],
                # Dhan publishes no corporate actions: the adjustment factor is 1 by construction
                # and the limitation is announced by the caller (see src/engine/live.py).
                "dividend": 0.0,
                "split": 0.0,
                "factor": 1.0,
            },
            index=pd.DatetimeIndex([pd.Timestamp(d) for d in days], name="date"),
        )
        return (
            instrument.key,
            DailySeries(instrument.key, frame_out, source=MarketDataSource.DHAN),
            None,
            repaired,
        )
