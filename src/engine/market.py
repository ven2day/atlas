"""
Market data for the engine (plan M5.6): one service the decision engine, the RiskGate, the risk
monitor, the simulated broker and the exit manager all read, so they see the same prices.

* **History** is loaded once per day, pre-open, for the universe (NIFTY 50 ∪ held symbols - no
  position is ever unpriced) plus the NIFTY index for the regime; features are computed once.
* **Quotes** come from a :class:`QuoteSource` (YFinance live, or a tape in replay). Every new
  quote is passed to the listeners in order: the simulated broker first (fills), then the exit
  manager (targets, partials).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from datetime import date
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

from src.domain.events import Alert
from src.domain.sink import EventSink
from src.domain.types import Instrument, Quote
from src.features.technical import Features, compute_features
from src.marketdata.composite import HistorySourceLike
from src.marketdata.history import DailySeries, HistoryResult, alert_failures
from src.marketdata.yfinance_source import PollResult
from src.risk.snapshot import MarketFacts

if TYPE_CHECKING:
    from src.config.settings import Settings
    from src.marketdata.dhan.instruments import DhanMapping
    from src.store.tape import TapeWriter

logger = logging.getLogger(__name__)

INDEX_KEY = "NSE:INDEX:NIFTY50"  # history only: the regime's input
INDEX_TICKER = "^NSEI"

QuoteListener = Callable[[Quote], Awaitable[None]]


class QuoteSource(Protocol):
    @property
    def last_quotes(self) -> Mapping[str, Quote]: ...

    @property
    def next_delay_s(self) -> float: ...

    def set_prev_closes(self, closes: Mapping[str, float]) -> None: ...

    async def poll(self) -> PollResult: ...


class HistorySource(Protocol):
    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult: ...


class MarketService:
    def __init__(
        self,
        *,
        instruments: Mapping[str, Instrument],
        quotes: QuoteSource,
        history: HistorySource,
        sink: EventSink,
    ) -> None:
        self.instruments = dict(instruments)
        self._quotes = quotes
        self._history_source = history
        self._sink = sink
        self._history: HistoryResult = HistoryResult(series={})
        self._closes: dict[str, Decimal] = {}  # the last settled close per instrument
        self._features: dict[str, Features] = {}
        self._listeners: list[QuoteListener] = []

    def add_listener(self, listener: QuoteListener) -> None:
        self._listeners.append(listener)

    # -- history (once a day, pre-open) -----------------------------------------------------------

    async def load_history(self, session_date: date, previous_session: date | None) -> None:
        result = await self._history_source.fetch(
            list(self.instruments.values()),
            settled_before=session_date,
            expected_last=previous_session,
            extra={INDEX_KEY: INDEX_TICKER},
        )
        alert_failures(result, self._sink)
        self._history = result
        self._closes = {k: Decimal(str(s.last_close)) for k, s in result.series.items()
                        if k != INDEX_KEY}  # fmt: skip
        self._features = {}
        for key, series in result.series.items():
            if key == INDEX_KEY:
                continue
            try:
                self._features[key] = compute_features(series.adjusted(), key)
            except Exception as exc:
                logger.exception("features failed for %s", key)
                self._sink.emit(
                    Alert(level="WARNING", key=f"features_failed:{key}",
                          message=f"{type(exc).__name__}: {exc}"),
                    source="marketdata",
                )  # fmt: skip
        self._quotes.set_prev_closes(
            {k: s.last_close for k, s in result.series.items() if k != INDEX_KEY}
        )

    def daily(self, instrument_key: str) -> DailySeries | None:
        return self._history.series.get(instrument_key)

    def index_series(self) -> DailySeries | None:
        return self._history.series.get(INDEX_KEY)

    def is_lagging(self, instrument_key: str) -> bool:
        return instrument_key in self._history.lagging

    def features(self, instrument_key: str) -> Features | None:
        return self._features.get(instrument_key)

    # -- quotes ---------------------------------------------------------------------------------

    @property
    def next_delay_s(self) -> float:
        return self._quotes.next_delay_s

    async def poll(self) -> PollResult:
        result = await self._quotes.poll()
        for key in sorted(result.quotes):
            quote = result.quotes[key]
            for listener in self._listeners:
                try:
                    await listener(quote)
                except Exception as exc:  # one listener never starves the others
                    logger.exception("quote listener failed on %s", key)
                    self._sink.emit(
                        Alert(level="CRITICAL", key="quote_listener_failed",
                              message=f"{key}: {type(exc).__name__}: {exc}"),
                        source="marketdata",
                    )  # fmt: skip
        return result

    async def requote(self, instrument_keys: Collection[str]) -> Mapping[str, Quote]:
        await self.poll()
        latest = self._quotes.last_quotes
        return {k: latest[k] for k in instrument_keys if k in latest}

    def quotes(self) -> dict[str, Quote]:
        return dict(self._quotes.last_quotes)

    def quote(self, instrument_key: str) -> Quote | None:
        return self._quotes.last_quotes.get(instrument_key)

    def marks(self) -> dict[str, Decimal]:
        """The latest quotes; an instrument not quoted yet today is marked at its last settled
        close - before the open that is yesterday's close, the right start-of-day value (a held
        position must never start the risk day valued at cost)."""
        marks = dict(self._closes)
        marks.update({k: Decimal(str(q.ltp)) for k, q in self._quotes.last_quotes.items()})
        return marks

    def facts(self, instrument: Instrument) -> MarketFacts:
        """What the RiskGate needs: the latest quote, ATR and ADV from the settled history."""
        quote = self.quote(instrument.key)
        f = self._features.get(instrument.key)
        return MarketFacts(
            quote=quote,
            atr=Decimal(str(f.atr_14)) if f is not None and f.atr_14 else None,
            adv_shares=f.adv20_shares if f is not None else None,
            data_source=quote.source if quote is not None else None,
        )


# -- history source selection -------------------------------------------------------------------

INDEX_SYMBOL = "NIFTY50"


def dhan_index_instrument(overrides: Mapping[str, DhanMapping]) -> Instrument | None:
    """NIFTY 50 as a Dhan-priced instrument, when ``DHAN_SECURITY_IDS`` maps it (segment IDX_I).

    Dhan's cash-equity scrip master has no row for an index, so without that mapping the index
    has to come from the secondary source.
    """
    from src.marketdata.dhan.instruments import INDEX_SEGMENT

    mapping = overrides.get(INDEX_SYMBOL)
    if mapping is None or mapping.segment != INDEX_SEGMENT:
        return None
    return Instrument(
        key=INDEX_KEY, exchange="NSE", segment="INDEX", symbol=INDEX_SYMBOL, series="INDEX",
        broker_tokens={"dhan": mapping.security_id},
    )  # fmt: skip


def build_history_source(
    settings: Settings, *, tape: TapeWriter | None = None
) -> HistorySourceLike:
    """The daily-history source for the configured provider, index included.

    Provider selection lives here so that anything reading daily bars - the research path today,
    other read-only consumers later - agrees with the session about where prices come from and
    keeps each bar's own ``source`` rather than blending providers. Imports are local: this keeps
    the Dhan stack out of the import graph of a CLI-only or YFinance-only install.
    """
    from src.marketdata.composite import CompositeHistorySource
    from src.marketdata.history import YFinanceHistorySource

    if settings.market_data_provider != "dhan":
        return YFinanceHistorySource(tape=tape)

    from src.marketdata.dhan.history import DhanHistorySource
    from src.marketdata.dhan.setup import build_client, security_overrides

    overrides = security_overrides(settings)
    index_instrument = dhan_index_instrument(overrides)
    return CompositeHistorySource(
        primary=DhanHistorySource(client=build_client(settings), tape=tape, overrides=overrides),
        secondary=YFinanceHistorySource(tape=tape),  # the NIFTY index, for the regime
        secondary_keys=set() if index_instrument is not None else {INDEX_KEY},
        extra_instruments=[index_instrument] if index_instrument is not None else [],
    )
