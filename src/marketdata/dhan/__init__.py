"""
DhanHQ v2 as a market-data provider: quotes and candles for NSE cash equities.

What lives here, and why:

* :mod:`src.marketdata.dhan.totp` - RFC 6238 codes from the account's TOTP secret, so an access
  token can be generated headlessly (Dhan's documented "Method 2").
* :mod:`src.marketdata.dhan.auth` - the access-token lifecycle (24 h validity; regenerated with a
  fresh TOTP code before it expires). An API key/secret is *not* an access token and is not used:
  Dhan's OAuth app flow needs an interactive browser consent step.
* :mod:`src.marketdata.dhan.client` - the REST surface used for data: ``POST /marketfeed/quote``
  (batched, 1 request/second), ``POST /charts/historical`` (daily), ``POST /charts/intraday``
  (1/5/15/25/60-minute, at most 90 days per request), ``GET /profile`` (the Data API entitlement).
* :mod:`src.marketdata.dhan.quotes` / :mod:`src.marketdata.dhan.history` - adapters onto the
  engine's ``QuoteSource`` / ``HistorySource`` protocols, so nothing downstream is
  provider-aware.

Market data only: this package never places, modifies or cancels an order, and imports nothing
from ``src.oms`` or ``src.brokers``.

Verified against https://dhanhq.co/docs/v2/ (authentication, market-quote, historical-data,
live-market-feed, rate limits) on 2026-11-14. The Data APIs are a paid subscription on top of
the free Trading APIs; ``GET /profile`` reports it as ``dataPlan`` / ``dataValidity``.
"""

from src.marketdata.dhan.auth import DhanAuth
from src.marketdata.dhan.client import DhanClient
from src.marketdata.dhan.errors import (
    DhanAuthError,
    DhanError,
    DhanMalformedResponseError,
    DhanRateLimitError,
)
from src.marketdata.dhan.history import DhanHistorySource
from src.marketdata.dhan.quotes import DhanQuoteSource
from src.marketdata.dhan.setup import build_client, check_data_plan, validate_market_data_provider

__all__ = [
    "DhanAuth",
    "DhanAuthError",
    "DhanClient",
    "DhanError",
    "DhanHistorySource",
    "DhanMalformedResponseError",
    "DhanQuoteSource",
    "DhanRateLimitError",
    "build_client",
    "check_data_plan",
    "validate_market_data_provider",
]
