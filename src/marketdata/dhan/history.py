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
* Dhan serves no corporate actions. When a :class:`~src.marketdata.adjustments.
  CorporateActionSource` is configured, its separately sourced adjustment table (Yahoo's
  published dividends) is applied to the ``factor`` column - raw prices stay untouched, and the
  adjustment provenance is reported per instrument in ``HistoryResult.adjustments``. Without it
  (or when it fails) the dividend back-adjustment factor is 1.0 and the adjusted series equals
  the raw one; that is a real limitation of the vendor, not something to paper over: the wiring
  emits a WARNING alert naming it, and every bar is stamped ``source=dhan`` so tapes, replays
  and the console can see where it came from.
* Fetched windows are cached per (security id, range, session date); a cache hit is reported in
  ``HistoryResult.cached`` with its original fetch time, so reused data is never passed off as
  freshly retrieved.
* An instrument with no Dhan security id in the master is reported failed, never guessed.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import date, timedelta

import pandas as pd

from src.domain.types import Instrument, MarketDataSource
from src.marketdata.adjustments import (
    CorporateAction,
    CorporateActionSource,
    apply_corporate_actions,
)
from src.marketdata.dhan.client import DhanClient
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.history_cache import DailyCandleCache
from src.marketdata.dhan.instruments import DhanMapping, mapping_for
from src.marketdata.dhan.timestamps import DhanTimestampError, chart_session_date
from src.marketdata.history import DEFAULT_MIN_BARS, DailySeries, HistoryResult, record_history
from src.store.tape import TapeWriter

logger = logging.getLogger(__name__)

LOOKBACK_DAYS = 420  # ~19 months of sessions: comfortably past the 60-bar minimum

_Row = tuple[float, float, float, float, int]
_Outcome = tuple[str, DailySeries | None, str | None, int, str | None]


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
        cache: DailyCandleCache | None = None,
        adjustments: CorporateActionSource | None = None,
    ) -> None:
        self._client = client
        self._tape = tape
        self._overrides = overrides
        self._min_bars = min_bars
        self._lookback = timedelta(days=lookback_days)
        self._timeout_s = timeout_s
        self._concurrency = concurrency
        self._cache = cache
        self._adjustments = adjustments

    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def one(instrument: Instrument) -> _Outcome:
            async with semaphore:
                try:
                    return await self._fetch_one(instrument, settled_before)
                except (DhanError, DhanTimestampError) as exc:
                    return instrument.key, None, str(exc), 0, None

        try:
            outcomes = await asyncio.wait_for(
                asyncio.gather(*(one(i) for i in instruments)), self._timeout_s
            )
        except TimeoutError:
            timeout_reason = f"dhan history timed out after {self._timeout_s:.0f}s"
            logger.warning(timeout_reason)
            outcomes = [(i.key, None, timeout_reason, 0, None) for i in instruments]

        series: dict[str, DailySeries] = {}
        failed: dict[str, str] = {}
        repaired: dict[str, int] = {}
        lagging: dict[str, date] = {}
        cached: dict[str, str] = {}
        for key, daily, reason, fixed, cached_at in outcomes:
            if daily is not None:
                series[key] = daily
                if fixed:
                    repaired[key] = fixed
                if cached_at is not None:
                    cached[key] = cached_at
                if expected_last is not None and daily.last_date < expected_last:
                    lagging[key] = daily.last_date
            elif reason is not None:
                failed[key] = reason
        # Indices and anything else the caller asked for by ticker: Dhan's equity master has no
        # row for them, and inventing an index security id would be worse than saying no.
        for key in extra or {}:
            failed.setdefault(key, "not an NSE cash equity in Dhan's master (routed elsewhere)")
        adjustments = await self._apply_adjustments(series)
        result = HistoryResult(series=series, failed=failed, repaired=repaired, lagging=lagging,
                               cached=cached, adjustments=adjustments)  # fmt: skip
        if self._tape is not None:
            record_history(result, tape=self._tape, recorded_on=settled_before)
        return result

    async def _apply_adjustments(self, series: dict[str, DailySeries]) -> dict[str, str]:
        """Overlay the separately sourced corporate-action table; label every outcome.

        Indices are skipped (they have no corporate actions). Any failure leaves the affected
        series unadjusted (factor 1.0) and is labelled ``unavailable: ...`` so callers keep
        reporting the limitation instead of silently mixing price bases.
        """
        notes: dict[str, str] = {}
        equity_keys = [key for key in series if ":INDEX:" not in key]
        if self._adjustments is None or not equity_keys:
            return notes
        symbols = [key.rsplit(":", 1)[-1] for key in equity_keys]
        fetch_error: str | None = None
        actions: Mapping[str, Sequence[CorporateAction]] = {}
        try:
            actions = await self._adjustments.fetch(symbols)
        except Exception as exc:  # the overlay is best-effort; raw history still stands
            fetch_error = f"{type(exc).__name__}: {exc}"
            logger.warning("corporate-action overlay failed: %s", fetch_error)
        for key in equity_keys:
            if fetch_error is not None:
                notes[key] = f"unavailable: {fetch_error}"
                continue
            symbol = key.rsplit(":", 1)[-1]
            item = series[key]
            symbol_actions = list(actions.get(symbol, ()))
            try:
                frame = apply_corporate_actions(item.frame, symbol_actions)
            except ValueError as exc:
                notes[key] = f"unavailable: bad corporate-action data ({exc})"
                continue
            series[key] = replace(item, frame=frame)
            dividends = sum(1 for a in symbol_actions if a.dividend > 0)
            splits = sum(1 for a in symbol_actions if a.split_ratio > 0)
            notes[key] = (
                "yahoo_corporate_actions: "
                f"{dividends} dividend(s) adjusted, {splits} split(s) recorded"
            )
        return notes

    async def _fetch_one(self, instrument: Instrument, settled_before: date) -> _Outcome:
        mapping = mapping_for(instrument, self._overrides)
        if mapping is None:
            return instrument.key, None, "no dhan security id in the instrument master", 0, None
        frm = settled_before - self._lookback
        frame = None
        cached_at: str | None = None
        cache_key: str | None = None
        if self._cache is not None:
            cache_key = DailyCandleCache.key(
                mapping.security_id, segment=mapping.segment, instrument=mapping.instrument_type,
                frm=frm, to=settled_before, session_date=settled_before,
            )  # fmt: skip
            hit = self._cache.get(cache_key)
            if hit is not None:
                frame, fetched_at = hit
                cached_at = fetched_at.isoformat(timespec="seconds")
        if frame is None:
            frame = await self._client.historical_daily(
                mapping.security_id,
                frm=frm,
                to=settled_before,
                segment=mapping.segment,
                instrument=mapping.instrument_type,
            )
            if self._cache is not None and cache_key is not None:
                self._cache.put(
                    cache_key, frame, security_id=mapping.security_id, segment=mapping.segment,
                    instrument=mapping.instrument_type, frm=frm, to=settled_before,
                    session_date=settled_before,
                )  # fmt: skip
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
                cached_at,
            )
        frame_out = pd.DataFrame(
            {
                "open": [rows[d][0] for d in days],
                "high": [rows[d][1] for d in days],
                "low": [rows[d][2] for d in days],
                "close": [rows[d][3] for d in days],
                "volume": [rows[d][4] for d in days],
                # Dhan publishes no corporate actions of its own. When an adjustment overlay is
                # configured it replaces these columns from its separately sourced table (see
                # _apply_adjustments); until then the factor is 1 by construction and the
                # limitation is announced by the caller (see src/engine/live.py).
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
            cached_at,
        )
