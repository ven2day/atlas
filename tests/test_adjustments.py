"""The separately sourced corporate-action overlay: parsing Yahoo's action columns, applying
dividend factors to another provider's raw closes without touching them, honest failure labels,
and the raw/adjusted separation. Synthetic fixtures only; no network."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd
import pytest

from src.domain.types import Instrument
from src.marketdata.adjustments import (
    CorporateAction,
    YahooCorporateActions,
    apply_corporate_actions,
)
from src.marketdata.dhan.history import DhanHistorySource
from src.marketdata.history import dividend_factors
from tests.test_dhan_sources import SETTLED_BEFORE, FakeDhan, daily_frame

INFY = Instrument.nse_equity("INFY", broker_tokens={"dhan": "1333"})
INDEX_KEY = "NSE:INDEX:NIFTY50"


def raw_frame(
    days: int = 10, start: date = date(2026, 1, 1), start_close: float = 100.0
) -> pd.DataFrame:
    index = pd.DatetimeIndex(
        [pd.Timestamp(start + timedelta(days=i)) for i in range(days)], name="date"
    )
    close = [start_close + i for i in range(days)]
    return pd.DataFrame(
        {
            "open": close,
            "high": [c + 2 for c in close],
            "low": [c - 1 for c in close],
            "close": close,
            "volume": [1000] * days,
        },
        index=index,
    )


def actions_download(by_ticker: Mapping[str, tuple[dict[date, float], dict[date, float]]]):
    """A fake ``yf.download`` returning the batched multi-index frame Yahoo produces."""

    def download(tickers: Sequence[str], **_kwargs: object) -> pd.DataFrame:
        frames = {}
        for ticker in tickers:
            dividends, splits = by_ticker.get(ticker, ({}, {}))
            days = sorted(set(dividends) | set(splits))
            index = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
            sub = pd.DataFrame(
                {
                    "Dividends": [dividends.get(d, 0.0) for d in days],
                    "Stock Splits": [splits.get(d, 0.0) for d in days],
                },
                index=index,
            )
            sub.columns = pd.MultiIndex.from_product([[ticker], sub.columns])
            frames[ticker] = sub
        return pd.concat(frames.values(), axis=1) if frames else pd.DataFrame()

    return download


# -- parsing -------------------------------------------------------------------------------------


async def test_actions_are_parsed_per_symbol_and_merged_on_the_same_day() -> None:
    source = YahooCorporateActions(
        download=actions_download(
            {
                "INFY.NS": ({date(2026, 3, 2): 18.0}, {}),
                "TCS.NS": ({date(2026, 4, 1): 10.0}, {date(2026, 4, 1): 2.0}),
            }
        )
    )
    actions = await source.fetch(["INFY", "TCS", "UNKNOWN"])
    assert set(actions) == {"INFY", "TCS"}  # a symbol Yahoo does not answer for is absent
    (infy,) = actions["INFY"]
    assert infy == CorporateAction(date(2026, 3, 2), dividend=18.0, split_ratio=0.0)
    (tcs,) = actions["TCS"]
    assert tcs.dividend == 10.0 and tcs.split_ratio == 2.0  # merged, not two half-records
    assert await source.fetch([]) == {}


# -- applying ------------------------------------------------------------------------------------


def test_dividends_scale_earlier_bars_using_the_series_own_closes() -> None:
    frame = raw_frame()  # closes 100..109
    action = CorporateAction(date(2026, 1, 6), dividend=10.0)
    out = apply_corporate_actions(frame, [action])
    expected = dividend_factors(frame["close"].to_numpy(), out["dividend"].to_numpy())
    assert list(out["factor"]) == pytest.approx(list(expected))
    # Bars before the ex-date scale by 1 - D/C_prev, with C_prev from *this* series (104.0).
    assert list(out["factor"].iloc[:5]) == pytest.approx([1 - 10 / 104.0] * 5)
    assert list(out["factor"].iloc[5:]) == [1.0] * 5
    # Raw prices are never modified; only the adjustment columns change.
    assert list(out["close"]) == list(frame["close"])
    assert "dividend" not in frame.columns  # the input frame is untouched (apply copies)
    adjusted_close = (out["close"] * out["factor"]).tolist()
    assert adjusted_close[0] == pytest.approx(100.0 * (1 - 10 / 104.0))
    assert adjusted_close[-1] == pytest.approx(109.0)


def test_an_ex_date_between_sessions_is_attributed_to_the_next_session() -> None:
    frame = raw_frame(days=4)  # Jan 1..4
    # Jan 3 is dropped from the series (as a non-session would be); its action lands on Jan 4.
    frame = frame.drop(frame.index[2])
    out = apply_corporate_actions(frame, [CorporateAction(date(2026, 1, 3), dividend=5.0)])
    assert out.loc[frame.index[2], "dividend"] == 5.0  # attributed to Jan 4, the next session
    assert out.loc[frame.index[1], "dividend"] == 0.0


def test_splits_are_recorded_but_not_applied() -> None:
    frame = raw_frame()
    out = apply_corporate_actions(frame, [CorporateAction(date(2026, 1, 6), split_ratio=2.0)])
    assert out.loc[out.index[5], "split"] == 2.0
    assert list(out["factor"]) == [1.0] * 10  # the documented vendor convention: splits are in raw


def test_a_dividend_that_cannot_be_adjusted_raises_instead_of_distorting() -> None:
    frame = raw_frame()
    with pytest.raises(ValueError, match="cannot adjust"):
        apply_corporate_actions(frame, [CorporateAction(date(2026, 1, 6), dividend=500.0)])


# -- the Dhan history overlay ---------------------------------------------------------------------


class FakeActions:
    def __init__(
        self,
        actions: Mapping[str, Sequence[CorporateAction]] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.actions = dict(actions or {})
        self.error = error
        self.seen: list[Sequence[str]] = []

    async def fetch(self, symbols: Sequence[str]) -> Mapping[str, Sequence[CorporateAction]]:
        self.seen.append(symbols)
        if self.error is not None:
            raise self.error
        return self.actions


async def test_the_overlay_adjusts_dhan_history_and_labels_its_provenance() -> None:
    ex_day = (SETTLED_BEFORE - timedelta(days=20)).isoformat()
    actions = FakeActions({"INFY": [CorporateAction(date.fromisoformat(ex_day), dividend=15.0)]})
    fake = FakeDhan(daily={"1333": daily_frame(70)})
    source = DhanHistorySource(client=fake, adjustments=actions)  # type: ignore[arg-type]
    result = await source.fetch([INFY], settled_before=SETTLED_BEFORE, expected_last=None)
    series = result.series[INFY.key]
    assert result.adjustments[INFY.key] == (
        "yahoo_corporate_actions: 1 dividend(s) adjusted, 0 split(s) recorded"
    )
    factors = series.frame["factor"]
    assert (factors.iloc[:-1] < 1.0).any() and factors.iloc[-1] == 1.0
    # Raw stays raw: the overlay only fills dividend/split/factor, never the prices.
    assert series.raw()["close"].equals(series.frame["close"])
    assert not series.adjusted()["close"].equals(series.raw()["close"])
    assert actions.seen == [["INFY"]]


async def test_a_failed_overlay_leaves_history_raw_and_says_so() -> None:
    actions = FakeActions(error=RuntimeError("yahoo unreachable"))
    fake = FakeDhan(daily={"1333": daily_frame(70)})
    source = DhanHistorySource(client=fake, adjustments=actions)  # type: ignore[arg-type]
    result = await source.fetch([INFY], settled_before=SETTLED_BEFORE, expected_last=None)
    assert INFY.key in result.series  # raw history still stands
    assert result.adjustments[INFY.key].startswith("unavailable: RuntimeError")
    assert list(result.series[INFY.key].frame["factor"].unique()) == [1.0]


async def test_bad_action_data_for_one_symbol_never_poisons_the_others() -> None:
    tcs = Instrument.nse_equity("TCS", broker_tokens={"dhan": "4000"})
    actions = FakeActions(
        {
            "INFY": [CorporateAction(SETTLED_BEFORE - timedelta(days=5), dividend=99999.0)],
            "TCS": [CorporateAction(SETTLED_BEFORE - timedelta(days=5), dividend=5.0)],
        }
    )
    fake = FakeDhan(daily={"1333": daily_frame(70), "4000": daily_frame(70)})
    source = DhanHistorySource(client=fake, adjustments=actions)  # type: ignore[arg-type]
    result = await source.fetch([INFY, tcs], settled_before=SETTLED_BEFORE, expected_last=None)
    assert result.adjustments[INFY.key].startswith("unavailable: bad corporate-action data")
    assert result.adjustments[tcs.key].startswith("yahoo_corporate_actions")
    assert list(result.series[INFY.key].frame["factor"].unique()) == [1.0]
    assert (result.series[tcs.key].frame["factor"] < 1.0).any()


async def test_indices_are_never_sent_to_the_action_overlay() -> None:
    index_frame = pd.DataFrame(raw_frame(70))
    index_frame["dividend"] = 0.0
    index_frame["split"] = 0.0
    index_frame["factor"] = 1.0
    from src.domain.types import MarketDataSource
    from src.marketdata.history import DailySeries

    actions = FakeActions()
    source = DhanHistorySource(client=FakeDhan(), adjustments=actions)  # type: ignore[arg-type]
    notes = await source._apply_adjustments(
        {INDEX_KEY: DailySeries(INDEX_KEY, index_frame, source=MarketDataSource.YFINANCE)}
    )
    assert notes == {} and actions.seen == []
