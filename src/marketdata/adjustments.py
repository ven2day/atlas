"""Corporate actions as a separately sourced adjustment table (for providers that serve none).

Dhan's ``/charts/historical`` returns prices with **no corporate-action data at all**: no
dividends, no split ratios, nothing to back-adjust with. The project's rule (see
:mod:`src.marketdata.history`) is that indicators run on *adjusted* history while live quotes,
fills and stops stay on *raw* prices. Rather than suppress the limitation or invent adjustment
factors, this module sources the adjustment table separately from Yahoo - the same source the
YFinance history path already verifies against - and applies it to the provider's raw series:

* Only **dividends** enter the adjustment factor, with exactly the construction the YFinance
  path verifies to 1e-7 against Yahoo's own ``Adj Close`` (:func:`~src.marketdata.history.
  dividend_factors`): for each ex-date ``e``, bars before it scale by ``1 - D_e / C_{e-1}``,
  with ``C`` taken from the series being adjusted (the provider's own closes), never from Yahoo's.
* **Splits** are recorded in the ``split`` column for visibility but are *not* applied: the
  convention this repository verified is that vendor daily history is served on the current
  share basis (splits already reflected). If a vendor ever proves otherwise, that is a
  documented open question, not something to paper over with a guessed factor.
* Raw prices are never modified. Only the ``dividend`` / ``split`` / ``factor`` columns change,
  so ``DailySeries.raw()`` stays byte-identical and ``DailySeries.adjusted()`` becomes the
  dividend-adjusted series.
* A failure to fetch actions leaves the series unadjusted (factor 1.0) and is *labelled*, so the
  caller keeps reporting ``dhan_history_unadjusted`` instead of silently mixing bases.
"""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Protocol

import pandas as pd

from src.marketdata.history import dividend_factors
from src.marketdata.symbols import yahoo_ticker

logger = logging.getLogger(__name__)

DEFAULT_ACTIONS_PERIOD = "2y"


@dataclass(frozen=True, slots=True)
class CorporateAction:
    """One ex-dated corporate action: a cash dividend per share and/or a split ratio."""

    ex_date: date
    dividend: float = 0.0  # cash per share, in the series' own currency
    split_ratio: float = 0.0  # e.g. 2.0 for a 2:1 split; recorded, not applied (see module doc)


class CorporateActionSource(Protocol):
    async def fetch(self, symbols: Sequence[str]) -> Mapping[str, Sequence[CorporateAction]]: ...


class YahooCorporateActions:
    """Dividends and splits from one batched Yahoo download, keyed by NSE symbol.

    Yahoo is used only as the *corporate-action table* here; its prices are never mixed into
    another provider's series. A symbol Yahoo does not answer for is simply absent from the
    result - the caller then leaves that series unadjusted and says so.
    """

    def __init__(
        self,
        *,
        download: Any = None,
        period: str = DEFAULT_ACTIONS_PERIOD,
        timeout_s: float = 60.0,
    ) -> None:
        self._download = download
        self._period = period
        self._timeout_s = timeout_s

    async def fetch(self, symbols: Sequence[str]) -> Mapping[str, Sequence[CorporateAction]]:
        unique = sorted(set(symbols))
        if not unique:
            return {}
        tickers = {symbol: yahoo_ticker(symbol) for symbol in unique}
        by_ticker = {ticker: symbol for symbol, ticker in tickers.items()}
        fetch = self._download or _default_download

        def work() -> pd.DataFrame:
            return fetch(
                sorted(set(tickers.values())),
                period=self._period,
                interval="1d",
                auto_adjust=False,
                actions=True,
                group_by="ticker",
                threads=True,
                progress=False,
            )

        frame = await asyncio.wait_for(asyncio.to_thread(work), self._timeout_s)
        return parse_actions_frame(frame, by_ticker)


def _default_download(*args: Any, **kwargs: Any) -> Any:
    import yfinance as yf

    return yf.download(*args, **kwargs)


def parse_actions_frame(
    frame: Any, by_ticker: Mapping[str, str]
) -> dict[str, list[CorporateAction]]:
    """``Dividends`` / ``Stock Splits`` columns of a batched download, per NSE symbol."""
    out: dict[str, list[CorporateAction]] = {}
    if frame is None or getattr(frame, "empty", True):
        return out
    available = set(frame.columns.get_level_values(0))
    for ticker, symbol in by_ticker.items():
        if ticker not in available:
            continue
        sub = frame[ticker]
        actions: dict[date, CorporateAction] = {}
        for column, is_dividend in (("Dividends", True), ("Stock Splits", False)):
            if column not in sub.columns:
                continue
            for ts, value in sub[column].items():
                try:
                    amount = float(value)
                except (TypeError, ValueError):
                    continue
                if not math.isfinite(amount) or amount <= 0:
                    continue
                day = ts.date() if hasattr(ts, "date") else ts
                prior = actions.get(day)
                if is_dividend:
                    actions[day] = CorporateAction(
                        day, dividend=amount, split_ratio=prior.split_ratio if prior else 0.0
                    )
                else:
                    actions[day] = CorporateAction(
                        day, dividend=prior.dividend if prior else 0.0, split_ratio=amount
                    )
        if actions:
            out[symbol] = [actions[day] for day in sorted(actions)]
    return out


def apply_corporate_actions(
    frame: pd.DataFrame, actions: Sequence[CorporateAction]
) -> pd.DataFrame:
    """Fill ``dividend`` / ``split`` / ``factor`` on a raw daily frame, in place of copies.

    The frame is indexed by session date with a ``close`` column (the provider's own raw closes).
    An ex-date that is not a session in this series (calendar mismatch) is attributed to the
    first session on or after it; one beyond the last session cannot adjust anything and is
    dropped. Raises ``ValueError`` when a dividend cannot be adjusted against the prior close -
    the caller then keeps the series unadjusted rather than applying a broken factor.
    """
    out = frame.copy()
    days = [ts.date() for ts in out.index]
    dividends = [0.0] * len(days)
    splits = [0.0] * len(days)
    for action in actions:
        index = next((i for i, day in enumerate(days) if day >= action.ex_date), None)
        if index is None:
            continue  # ex-date after the window: nothing before it to adjust
        if action.dividend > 0:
            dividends[index] += action.dividend
        if action.split_ratio > 0:
            splits[index] = action.split_ratio
    out["dividend"] = dividends
    out["split"] = splits
    out["factor"] = dividend_factors(out["close"].to_numpy(), out["dividend"].to_numpy())
    return out
