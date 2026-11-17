"""
Live quotes from Dhan's batched quote API, shaped into the engine's ``QuoteSource`` protocol.

One ``POST /marketfeed/quote`` per poll covers the whole universe (the endpoint takes up to
1000 instruments and is limited to 1 request/second, so a single batched call is both the
cheapest and the fastest shape). A quote is accepted only with an honest exchange time:
``last_trade_time`` is parsed as IST and must be a plausible moment; Dhan's placeholder for an
instrument that never traded (``01/01/1980 00:00:00``) makes that instrument *missing* for the
poll rather than stamping a price with a fabricated time.

Staleness and backoff mirror :class:`~src.marketdata.yfinance_source.YFinanceQuoteSource` so the
engine, the risk gate and the console see identical behaviour from either provider; the
bookkeeping is deliberately duplicated rather than extracted, to leave the audited YFinance
source untouched.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from src.config.errors import ConfigError
from src.domain.clock import Clock
from src.domain.events import FeedRecovered, FeedStale, QuoteRejected
from src.domain.sink import EventSink
from src.domain.types import Instrument, MarketDataSource, Quote
from src.marketdata.dhan.client import DhanClient
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.instruments import DhanMapping, mapping_for
from src.marketdata.dhan.timestamps import parse_last_trade_time
from src.marketdata.validation import QuoteValidator, RawQuote
from src.marketdata.yfinance_source import PollResult
from src.store.tape import TapeWriter

logger = logging.getLogger(__name__)

SOURCE = MarketDataSource.DHAN
# A live REST quote: anything older than two minutes while the market is open is stale.
DEFAULT_STALE_AFTER_S = 120.0


class DhanQuoteSource:
    def __init__(
        self,
        instruments: Sequence[Instrument],
        *,
        clock: Clock,
        sink: EventSink,
        client: DhanClient,
        tape: TapeWriter | None = None,
        validator: QuoteValidator | None = None,
        market_open: Callable[[datetime], bool] | None = None,
        overrides: Mapping[str, DhanMapping] | None = None,
        poll_interval_s: float = 60.0,
        max_backoff_s: float = 900.0,
        stale_after_s: float = DEFAULT_STALE_AFTER_S,
    ) -> None:
        by_id: dict[str, str] = {}
        by_segment: dict[str, list[str]] = {}
        unmapped: list[str] = []
        for instrument in instruments:
            mapping = mapping_for(instrument, overrides)
            if mapping is None:
                unmapped.append(instrument.key)
                continue
            by_id[mapping.security_id] = instrument.key
            by_segment.setdefault(mapping.segment, []).append(mapping.security_id)
        if not by_id:
            raise ConfigError(
                "MARKET_DATA_PROVIDER=dhan but no instrument carries a Dhan security id: "
                "refresh the reference data (the scrip master maps the universe) or add "
                "DHAN_SECURITY_IDS overrides before running"
            )
        if unmapped:
            logger.warning("dhan quotes: %d instrument(s) have no security id: %s",
                           len(unmapped), ", ".join(sorted(unmapped)[:8]))  # fmt: skip
        self._by_id = by_id
        self._by_segment = by_segment
        self._unmapped = tuple(sorted(unmapped))
        self._clock = clock
        self._sink = sink
        self._client = client
        self._tape = tape
        self._validator = validator or QuoteValidator()
        self._market_open = market_open or (lambda _ts: True)
        self._poll_interval_s = poll_interval_s
        self._max_backoff_s = max_backoff_s
        self._stale_after_s = stale_after_s
        self._prev_close: dict[str, float] = {}
        self._failures = 0
        self._feed_down_since: datetime | None = None
        self._stale_since: dict[str, datetime] = {}
        self.last_quotes: dict[str, Quote] = {}

    # -- configuration -----------------------------------------------------------------------

    def set_prev_closes(self, closes: Mapping[str, float]) -> None:
        self._prev_close = dict(closes)

    @property
    def next_delay_s(self) -> float:
        if self._failures == 0:
            return self._poll_interval_s
        return min(self._max_backoff_s, self._poll_interval_s * 2.0**self._failures)

    @property
    def stale_keys(self) -> frozenset[str]:
        return frozenset(self._stale_since)

    @property
    def unmapped_keys(self) -> tuple[str, ...]:
        return self._unmapped

    # -- polling -----------------------------------------------------------------------------

    async def poll(self) -> PollResult:
        started = time.perf_counter()
        try:
            payloads = await self._client.quotes(self._by_segment)
        except DhanError as exc:
            return self._failed(str(exc), started)
        now = self._clock.now()
        raw: dict[str, RawQuote] = {}
        for sid, payload in payloads.items():
            key = self._by_id.get(sid)
            if key is None:
                continue
            exchange_ts = parse_last_trade_time(payload.last_trade_time, now=now)
            if exchange_ts is None:
                continue  # no honest trade time: missing this poll, never fabricated
            raw[key] = RawQuote(
                instrument_key=key,
                ltp=payload.last_price,
                volume_cum=payload.volume,
                exchange_ts=exchange_ts,
                prev_close=payload.prev_close,
            )
        return self._accept(raw, started, now)

    def _accept(self, raw: Mapping[str, RawQuote], started: float, now: datetime) -> PollResult:
        self._mark_feed_up(now)
        quotes: dict[str, Quote] = {}
        rejected: list[str] = []
        for key in sorted(raw):
            item = raw[key]
            if item.prev_close is None:
                item = item.with_prev_close(self._prev_close.get(key))
            reason = self._validator.check(item)
            if reason is not None:
                rejected.append(key)
                self._sink.emit(
                    QuoteRejected(
                        instrument_key=key, source=SOURCE, reason=reason, raw=item.as_json()
                    ),
                    source="marketdata",
                )
                continue
            quote = item.to_quote(receipt_ts=now, source=SOURCE)
            self._validator.accept(quote)
            quotes[key] = quote
        missing = tuple(sorted((set(self._by_id.values()) | set(self._unmapped)) - set(raw)))
        self._update_symbol_staleness(now, quotes, missing)
        if self._tape is not None and quotes:
            self._tape.add_quotes(quotes.values())
            self._tape.flush()
        self.last_quotes.update(quotes)
        return PollResult(
            ok=True,
            quotes=quotes,
            missing=missing,
            rejected=tuple(rejected),
            latency_s=time.perf_counter() - started,
        )

    # -- staleness and backoff -----------------------------------------------------------------

    def _failed(self, error: str, started: float) -> PollResult:
        self._failures += 1
        now = self._clock.now()
        logger.warning("Dhan quote poll failed (%d in a row): %s", self._failures, error)
        if self._feed_down_since is None:
            self._feed_down_since = now
            self._sink.emit(
                FeedStale(
                    source=SOURCE,
                    instrument_keys=tuple(sorted(self._by_id.values())),
                    reason=error,
                ),
                source="marketdata",
            )
        return PollResult(ok=False, quotes={}, error=error, latency_s=time.perf_counter() - started)

    def _mark_feed_up(self, now: datetime) -> None:
        self._failures = 0
        if self._feed_down_since is not None:
            down_for = (now - self._feed_down_since).total_seconds()
            self._feed_down_since = None
            self._sink.emit(FeedRecovered(source=SOURCE, stale_for_s=down_for), source="marketdata")

    def _update_symbol_staleness(
        self, now: datetime, quotes: Mapping[str, Quote], missing: Sequence[str]
    ) -> None:
        if not self._market_open(now):
            stale_now: set[str] = set(missing)  # off-hours, old quotes are expected
        else:
            stale_now = set(missing) | {
                key for key, q in quotes.items() if q.age_seconds(now) > self._stale_after_s
            }
        newly = sorted(stale_now - set(self._stale_since))
        recovered = sorted(set(self._stale_since) - stale_now)
        no_data = tuple(k for k in newly if k not in quotes)
        too_old = tuple(k for k in newly if k in quotes)
        if no_data:
            self._sink.emit(
                FeedStale(source=SOURCE, instrument_keys=no_data, reason="no data"),
                source="marketdata",
            )
        if too_old:
            self._sink.emit(
                FeedStale(
                    source=SOURCE,
                    instrument_keys=too_old,
                    age_s=max(quotes[k].age_seconds(now) for k in too_old),
                    reason=f"older than {self._stale_after_s:.0f}s",
                ),
                source="marketdata",
            )
        for key in newly:
            self._stale_since[key] = now
        if recovered:
            longest = max((now - self._stale_since[k]).total_seconds() for k in recovered)
            self._sink.emit(
                FeedRecovered(source=SOURCE, instrument_keys=tuple(recovered), stale_for_s=longest),
                source="marketdata",
            )
            for key in recovered:
                del self._stale_since[key]
