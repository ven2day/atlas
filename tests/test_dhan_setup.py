"""Provider selection, the Data API entitlement check, and the timestamp rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from src.config.errors import ConfigError
from src.config.settings import Settings
from src.domain.clock import ReplayClock
from src.domain.events import Alert
from src.domain.sink import RecordingSink
from src.marketdata.dhan.client import DataPlan
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.setup import check_data_plan, validate_market_data_provider
from src.marketdata.dhan.timestamps import (
    DhanTimestampError,
    chart_instant,
    chart_session_date,
    parse_last_trade_time,
)
from src.utils.market_time import IST

NOW = datetime(2026, 10, 5, 4, 45, 0, tzinfo=UTC)


def with_provider(settings: Settings, **overrides: object) -> Settings:
    return settings.model_copy(update=overrides)


# -- provider selection -------------------------------------------------------------------------


def test_the_default_provider_needs_nothing(settings: Settings) -> None:
    assert settings.market_data_provider == "yfinance"
    validate_market_data_provider(settings)  # no credentials, no complaint


def test_dhan_without_a_client_id_names_the_variable(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="DHAN_CLIENT_ID"):
        validate_market_data_provider(with_provider(settings, market_data_provider="dhan"))


def test_dhan_without_any_token_path_names_both_options(settings: Settings) -> None:
    with pytest.raises(ConfigError, match="DHAN_ACCESS_TOKEN") as info:
        validate_market_data_provider(
            with_provider(settings, market_data_provider="dhan", dhan_client_id="1")
        )
    assert "DHAN_TOTP_SECRET" in str(info.value) and "DHAN_PIN" in str(info.value)


def test_dhan_with_a_totp_pair_is_accepted(settings: Settings) -> None:
    validate_market_data_provider(
        with_provider(settings, market_data_provider="dhan", dhan_client_id="1",
                      dhan_pin="123456", dhan_totp_secret="GEZDGNBVGY3TQOJQ")
    )  # fmt: skip


# -- entitlement --------------------------------------------------------------------------------


class FakeProfile:
    def __init__(self, plan: str | None = "Active", error: DhanError | None = None) -> None:
        self._plan = plan
        self._error = error

    async def profile(self) -> DataPlan:
        if self._error is not None:
            raise self._error
        return DataPlan(plan=self._plan, validity="2026-12-01 00:00:00.0")


async def test_an_inactive_data_plan_stops_startup_with_guidance() -> None:
    with pytest.raises(ConfigError, match="Data API subscription"):
        await check_data_plan(FakeProfile("Deactive"))  # type: ignore[arg-type]


async def test_an_active_plan_passes() -> None:
    await check_data_plan(FakeProfile("Active"))  # type: ignore[arg-type]


async def test_an_unreadable_plan_warns_and_continues() -> None:
    sink = RecordingSink(ReplayClock(NOW))
    await check_data_plan(FakeProfile(error=DhanError("network down")), sink=sink)  # type: ignore[arg-type]
    assert [a.key for a in sink.payloads(Alert)] == ["dhan_plan_unknown"]


# -- timestamps ---------------------------------------------------------------------------------


def test_daily_stamps_resolve_to_their_ist_session_date_under_either_convention() -> None:
    midnight_ist = int(datetime(2026, 10, 1, 0, 0, tzinfo=IST).timestamp())
    midnight_utc = int(datetime(2026, 10, 1, 0, 0, tzinfo=UTC).timestamp())
    assert chart_session_date(midnight_ist) == datetime(2026, 10, 1).date()
    assert chart_session_date(midnight_utc) == datetime(2026, 10, 1).date()


def test_an_intraday_stamp_that_is_a_true_instant_is_read_as_ist() -> None:
    stamp = int(datetime(2026, 10, 1, 9, 15, tzinfo=IST).timestamp())
    assert chart_instant(stamp, intraday=True) == datetime(2026, 10, 1, 9, 15, tzinfo=IST)


def test_an_intraday_stamp_that_is_an_ist_wall_clock_stored_as_utc_is_detected() -> None:
    # 15:20 written into an epoch as if it were UTC: read as a true instant the bar lands at
    # 20:50 IST, after the session; the wall-clock reading is the one that fits the window.
    stamp = int(datetime(2026, 10, 1, 15, 20, tzinfo=UTC).timestamp())
    assert chart_instant(stamp, intraday=True) == datetime(2026, 10, 1, 15, 20, tzinfo=IST)


def test_an_intraday_stamp_outside_every_session_is_refused() -> None:
    stamp = int(datetime(2026, 10, 1, 3, 0, tzinfo=UTC).timestamp())
    with pytest.raises(DhanTimestampError):
        chart_instant(stamp, intraday=True)


def test_last_trade_time_is_ist_and_rejects_placeholders_and_the_future() -> None:
    assert parse_last_trade_time("05/10/2026 10:14:55", now=NOW) == datetime(
        2026, 10, 5, 10, 14, 55, tzinfo=IST
    )
    assert parse_last_trade_time("01/01/1980 00:00:00", now=NOW) is None
    assert parse_last_trade_time("05/10/2026 11:00:00", now=NOW) is None  # in the future
    assert parse_last_trade_time(None, now=NOW) is None
    assert parse_last_trade_time("not a time", now=NOW) is None
