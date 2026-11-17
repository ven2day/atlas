"""The TOTP access-token lifecycle: minting, persistence, and the two-minute generation limit
that Dhan reports as an HTTP 200 error body rather than a 4xx."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx2
import pytest
from pydantic import SecretStr

from src.marketdata.dhan.auth import (
    DhanAuth,
    DhanToken,
    failure_reason,
    read_token_file,
    write_token_file,
)
from src.marketdata.dhan.errors import DhanAuthError
from src.utils.market_time import IST

NOW = datetime(2026, 10, 5, 4, 45, 0, tzinfo=UTC)  # 10:15 IST
PIN = SecretStr("123456")
TOTP = SecretStr("GEZDGNBVGY3TQOJQ")  # the RFC 6238 test seed
ROUTE = "POST /app/generateAccessToken"
MINTED = {"accessToken": "jwt.from-totp", "expiryTime": "2026-10-06 04:45:00"}
RATE_LIMITED = {"message": "Token can be generated once every 2 minutes.", "status": "error"}


def handler_for(routes: dict[str, tuple[int, Any]]):
    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        route = routes.get(f"{request.method} {request.url.path}")
        if route is None:
            return httpx2.Response(404, json={"error": "no route"})
        status, body = route
        return httpx2.Response(status, json=body)

    seen: list[httpx2.Request] = []
    return handler, seen


def totp_auth(client: httpx2.AsyncClient, token_file: Path | None = None) -> DhanAuth:
    return DhanAuth(
        client_id="1000000001", pin=PIN, totp_secret=TOTP, token_file=token_file,
        client=client, now=lambda: NOW,
    )  # fmt: skip


# -- persistence ---------------------------------------------------------------------------------


async def test_a_minted_token_is_written_to_the_token_file(tmp_path: Path) -> None:
    handler, seen = handler_for({ROUTE: (200, MINTED)})
    path = tmp_path / "dhan_token.json"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert await totp_auth(client, path).token() == "jwt.from-totp"
    assert len(seen) == 1
    stored = read_token_file(path, now=NOW)
    assert stored is not None and stored.access_token == "jwt.from-totp"
    assert stored.expiry == datetime(2026, 10, 6, 4, 45, tzinfo=IST)


async def test_a_live_token_file_is_reused_and_never_hits_the_two_minute_limit(
    tmp_path: Path,
) -> None:
    """The regression: an unpersisted mint made every restart re-generate, and a second
    generation inside two minutes fails the whole start."""
    path = tmp_path / "dhan_token.json"
    write_token_file(path, DhanToken("from-file", NOW + timedelta(hours=2)))
    handler, seen = handler_for({})  # no routes: a mint would 404 and fail loudly
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert await totp_auth(client, path).token() == "from-file"
    assert seen == []


async def test_an_expired_stored_token_is_replaced_by_a_fresh_mint(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    write_token_file(path, DhanToken("stale", NOW - timedelta(minutes=1)))
    handler, seen = handler_for({ROUTE: (200, MINTED)})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert await totp_auth(client, path).token() == "jwt.from-totp"
    assert len(seen) == 1
    stored = read_token_file(path, now=NOW)
    assert stored is not None and stored.access_token == "jwt.from-totp"


async def test_minting_without_a_token_file_still_works() -> None:
    handler, _ = handler_for({ROUTE: (200, MINTED)})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        assert await totp_auth(client).token() == "jwt.from-totp"


# -- refusals ------------------------------------------------------------------------------------


async def test_the_two_minute_limit_is_reported_in_dhans_own_words() -> None:
    handler, _ = handler_for({ROUTE: (200, RATE_LIMITED)})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DhanAuthError) as info:
            await totp_auth(client).token()
    message = str(info.value)
    assert "once every 2 minutes" in message and "200" in message
    assert PIN.get_secret_value() not in message
    assert TOTP.get_secret_value() not in message


async def test_a_refusal_with_no_reason_at_all_still_fails_clearly() -> None:
    handler, _ = handler_for({ROUTE: (200, {})})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DhanAuthError, match="refused"):
            await totp_auth(client).token()


async def test_a_rejected_mint_is_not_persisted(tmp_path: Path) -> None:
    handler, _ = handler_for({ROUTE: (200, RATE_LIMITED)})
    path = tmp_path / "dhan_token.json"
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DhanAuthError):
            await totp_auth(client, path).token()
    assert not path.exists()


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (RATE_LIMITED, "Token can be generated once every 2 minutes."),
        ({"status": "failed", "remarks": {"errorMessage": "Invalid TOTP"}}, "Invalid TOTP"),
        ({"status": "failed", "remarks": {"errorCode": "DH-905"}}, "DH-905"),
        ({"status": "error"}, "status=error"),
        ({"message": "   "}, None),
        ({}, None),
        ("not an object", None),
        (None, None),
    ],
)
def test_failure_reason_reads_every_shape_dhan_answers_with(
    body: Any, expected: str | None
) -> None:
    assert failure_reason(body) == expected
