"""The Dhan REST client against a mocked transport: request shapes, normalisation, chunking,
rate-limit/auth/entitlement errors and redaction. Nothing here touches the network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx2
import pytest
from pydantic import SecretStr

from src.config.errors import ConfigError
from src.marketdata.dhan.auth import DhanAuth
from src.marketdata.dhan.client import DhanClient, parse_candle_frame, parse_quotes
from src.marketdata.dhan.errors import (
    DhanAuthError,
    DhanError,
    DhanMalformedResponseError,
    DhanRateLimitError,
)

NOW = datetime(2026, 10, 5, 4, 45, 0, tzinfo=UTC)  # 10:15 IST

QUOTE_BODY = {
    "data": {
        "NSE_EQ": {
            "1333": {
                "last_price": 1542.0,
                "volume": 123456,
                "net_change": -4.28,
                "last_trade_time": "05/10/2026 10:14:55",
                "ohlc": {"open": 1548.0, "close": 1546.28, "high": 1550.0, "low": 1540.0},
            }
        }
    },
    "status": "success",
}

# 2026-09-30 and 2026-10-01 at 00:00 IST, as epoch seconds.
DAILY_BODY = {
    "open": [100.0, 101.0],
    "high": [102.0, 103.0],
    "low": [99.0, 100.0],
    "close": [101.0, 102.0],
    "volume": [1000, 2000],
    "timestamp": [1790706600, 1790793000],
    "open_interest": [0, 0],
}


class Recorder:
    def __init__(self, routes: dict[str, object]) -> None:
        self.routes = routes
        self.requests: list[httpx2.Request] = []

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        route = self.routes.get(key)
        if route is None:
            return httpx2.Response(404, json={"error": f"no route {key}"})
        if isinstance(route, httpx2.Response):
            return route
        status, body = route  # type: ignore[misc]
        return httpx2.Response(status, json=body)


def client_for(
    routes: dict[str, object], *, token: str = "static-token"
) -> tuple[DhanClient, Recorder]:
    recorder = Recorder(routes)
    auth = DhanAuth(client_id="1000000001", access_token=SecretStr(token))
    return DhanClient(
        auth=auth, client=httpx2.AsyncClient(transport=httpx2.MockTransport(recorder))
    ), recorder


# -- quotes -----------------------------------------------------------------------------------


async def test_quotes_are_batched_by_segment_and_normalised() -> None:
    dhan, recorder = client_for({"POST /v2/marketfeed/quote": (200, QUOTE_BODY)})
    out = await dhan.quotes(["1333", "1333"])
    request = recorder.requests[0]
    assert json.loads(request.content) == {"NSE_EQ": [1333]}
    assert request.headers["access-token"] == "static-token"
    assert request.headers["client-id"] == "1000000001"
    payload = out["1333"]
    assert payload.last_price == 1542.0
    assert payload.volume == 123456
    assert payload.prev_close == pytest.approx(1546.28)  # ltp - net_change
    assert payload.last_trade_time == "05/10/2026 10:14:55"


def test_parse_quotes_drops_untradeable_rows() -> None:
    body = {"data": {"NSE_EQ": {"1": {"last_price": 0}, "2": {"last_price": 12.5}}}}
    assert set(parse_quotes(body)) == {"2"}
    assert parse_quotes({"data": {}}) == {}
    with pytest.raises(DhanMalformedResponseError):
        parse_quotes({"status": "success"})


# -- historical -------------------------------------------------------------------------------


async def test_daily_history_request_shape() -> None:
    dhan, recorder = client_for({"POST /v2/charts/historical": (200, DAILY_BODY)})
    frame = await dhan.historical_daily(
        "1333", frm=datetime(2026, 9, 1).date(), to=datetime(2026, 10, 5).date()
    )
    body = json.loads(recorder.requests[0].content)
    assert body == {
        "securityId": "1333",
        "exchangeSegment": "NSE_EQ",
        "instrument": "EQUITY",
        "oi": False,
        "fromDate": "2026-09-01",
        "toDate": "2026-10-05",
    }
    assert len(frame) == 2 and frame.close == (101.0, 102.0)


async def test_intraday_windows_are_chunked_at_ninety_days_and_merged() -> None:
    dhan, recorder = client_for({"POST /v2/charts/intraday": (200, DAILY_BODY)})
    start = NOW - timedelta(days=200)
    frame = await dhan.historical_intraday("1333", start=start, end=NOW, interval_minutes=5)
    assert len(recorder.requests) == 3  # 200 days in 90-day chunks
    windows = [json.loads(r.content) for r in recorder.requests]
    for window in windows:
        frm = datetime.strptime(window["fromDate"], "%Y-%m-%d %H:%M:%S")
        to = datetime.strptime(window["toDate"], "%Y-%m-%d %H:%M:%S")
        assert to - frm <= timedelta(days=90)
        assert window["interval"] == "5"
    assert windows[-1]["toDate"] == NOW.strftime("%Y-%m-%d %H:%M:%S")
    assert len(frame) == 2  # the mocked chunks repeat the same two timestamps: deduped


async def test_intraday_rejects_undocumented_intervals() -> None:
    dhan, _ = client_for({})
    with pytest.raises(ValueError, match="interval"):
        await dhan.historical_intraday(
            "1", start=NOW - timedelta(days=1), end=NOW, interval_minutes=7
        )


def test_parse_candle_frame_refuses_ragged_arrays() -> None:
    bad = dict(DAILY_BODY)
    bad["volume"] = [1]
    with pytest.raises(DhanMalformedResponseError, match="length"):
        parse_candle_frame(bad)


# -- failures ---------------------------------------------------------------------------------


async def test_a_rejected_token_is_an_auth_error_and_invalidates_the_cache() -> None:
    calls = {"n": 0}

    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.url.host == "auth.dhan.co":
            calls["n"] += 1
            return httpx2.Response(
                200, json={"accessToken": f"t{calls['n']}", "expiryTime": "2026-10-06 10:00:00"}
            )
        return httpx2.Response(401, json={"error": "invalid token"})

    auth = DhanAuth(
        client_id="1",
        pin=SecretStr("123456"),
        totp_secret=SecretStr("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        now=lambda: NOW,
    )
    dhan = DhanClient(auth=auth, client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)))
    with pytest.raises(DhanAuthError):
        await dhan.profile()
    # The next call must mint a fresh token rather than replay the rejected one.
    with pytest.raises(DhanAuthError):
        await dhan.profile()
    assert calls["n"] == 2


async def test_rate_limits_surface_as_their_own_error() -> None:
    dhan, _ = client_for({"GET /v2/profile": (429, {"error": "slow down"})})
    with pytest.raises(DhanRateLimitError):
        await dhan.profile()


async def test_error_bodies_are_redacted_and_truncated() -> None:
    leaky = {"error": "boom accessToken=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig value"}
    dhan, _ = client_for({"GET /v2/profile": (400, leaky)})
    with pytest.raises(DhanError) as info:
        await dhan.profile()
    message = str(info.value)
    assert "eyJhbGciOiJIUzI1NiJ9" not in message
    assert "[REDACTED]" in message


async def test_the_entitlement_is_read_from_profile() -> None:
    dhan, _ = client_for(
        {
            "GET /v2/profile": (
                200,
                {"data": {"dataPlan": "Deactive", "dataValidity": "2026-01-01 00:00:00.0"}},
            )
        }
    )
    plan = await dhan.profile()
    assert plan.plan == "Deactive"


# -- auth -------------------------------------------------------------------------------------


async def test_token_generation_uses_the_documented_query_parameters() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(
            200, json={"accessToken": "jwt.jwt.jwt", "expiryTime": "2026-10-06 04:45:00"}
        )

    auth = DhanAuth(
        client_id="1000000001",
        pin=SecretStr("123456"),
        totp_secret=SecretStr("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        now=lambda: NOW,
    )
    assert await auth.token() == "jwt.jwt.jwt"
    params = seen[0].url.params
    assert params["dhanClientId"] == "1000000001"
    assert params["pin"] == "123456"
    assert params["totp"].isdigit() and len(params["totp"]) == 6
    # Cached while it is still fresh.
    assert await auth.token() == "jwt.jwt.jwt"
    assert len(seen) == 1


async def test_a_rejected_generation_never_echoes_the_pin_or_code() -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, text="pin=123456 totp=654321 denied")

    auth = DhanAuth(
        client_id="1",
        pin=SecretStr("123456"),
        totp_secret=SecretStr("GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"),
        client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        now=lambda: NOW,
    )
    with pytest.raises(DhanAuthError) as info:
        await auth.token()
    message = str(info.value)
    assert "123456" not in message and "654321" not in message


def test_credentials_are_checked_before_any_request() -> None:
    with pytest.raises(ConfigError, match="DHAN_ACCESS_TOKEN"):
        DhanAuth(client_id="1")
