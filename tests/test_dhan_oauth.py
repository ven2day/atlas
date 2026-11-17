"""The OAuth consent flow and the token-file precedence, against mocked transports and a temp
file. The tokenId is pasted locally in real use; here it is a fixture."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx2
import pytest
from pydantic import SecretStr

from src.marketdata.dhan.auth import DhanAuth, DhanToken, read_token_file, write_token_file
from src.marketdata.dhan.errors import DhanAuthError
from src.marketdata.dhan.oauth import (
    Consent,
    consume_consent,
    consent_login_url,
    generate_consent,
    token_id_from_redirect,
)

NOW = datetime(2026, 10, 5, 4, 45, 0, tzinfo=UTC)  # 10:15 IST
API_KEY = "app-id-fixture"
API_SECRET = "app-secret-fixture"


def handler_for(routes: dict[str, tuple[int, object]]):
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        route = routes.get(f"{request.method} {request.url.path}")
        if route is None:
            return httpx2.Response(404, json={"error": "no route"})
        status, body = route
        return httpx2.Response(status, json=body)

    return handler, seen


async def test_generate_consent_sends_the_app_credentials_as_headers() -> None:
    handler, seen = handler_for(
        {"POST /app/generate-consent": (200, {"consentAppId": "consent-1", "status": "success"})}
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        consent = await generate_consent(
            client, base_url="https://auth.dhan.co", client_id="1000000001",
            api_key=API_KEY, api_secret=API_SECRET,
        )  # fmt: skip
    request = seen[0]
    assert request.url.params["client_id"] == "1000000001"
    assert request.headers["app_id"] == API_KEY
    assert request.headers["app_secret"] == API_SECRET
    assert consent == Consent("consent-1")
    assert consent_login_url("https://auth.dhan.co", consent) == (
        "https://auth.dhan.co/login/consentApp-login?consentAppId=consent-1"
    )


async def test_consume_consent_returns_the_token_and_its_expiry() -> None:
    handler, seen = handler_for(
        {
            "GET /app/consumeApp-consent": (
                200,
                {"accessToken": "jwt.from.consent", "expiryTime": "2026-10-06 04:45:00"},
            )
        }
    )
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        token = await consume_consent(
            client, base_url="https://auth.dhan.co", token_id="tok-9",
            api_key=API_KEY, api_secret=API_SECRET, now=NOW,
        )  # fmt: skip
    assert seen[0].url.params["tokenId"] == "tok-9"
    assert token.access_token == "jwt.from.consent"
    assert token.expiry == NOW + timedelta(hours=24)


@pytest.mark.parametrize(
    ("pasted", "expected"),
    [
        ("https://example.invalid/cb?tokenId=tok-9&x=1", "tok-9"),
        ("?tokenId=tok-9", "tok-9"),
        ("tok-9", "tok-9"),
        ("", None),
        ("https://example.invalid/cb?code=other", None),
    ],
)
def test_the_token_id_is_read_from_whatever_the_operator_pastes(
    pasted: str, expected: str | None
) -> None:
    assert token_id_from_redirect(pasted) == expected


async def test_a_rejected_consent_is_an_auth_error_without_the_secret() -> None:
    handler, _ = handler_for({"POST /app/generate-consent": (403, {"error": "app_secret wrong"})})
    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as client:
        with pytest.raises(DhanAuthError) as info:
            await generate_consent(
                client, base_url="https://auth.dhan.co", client_id="1",
                api_key=API_KEY, api_secret=API_SECRET,
            )  # fmt: skip
    assert API_SECRET not in str(info.value)


# -- the token file ------------------------------------------------------------------------------


def test_the_token_file_round_trips_and_expires(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    write_token_file(path, DhanToken("tok", NOW + timedelta(hours=2)))
    stored = read_token_file(path, now=NOW)
    assert stored is not None and stored.access_token == "tok"
    assert read_token_file(path, now=NOW + timedelta(hours=3)) is None
    assert json.loads(path.read_text(encoding="utf-8"))["access_token"] == "tok"


def test_a_missing_or_corrupt_token_file_is_simply_absent(tmp_path: Path) -> None:
    assert read_token_file(tmp_path / "nope.json", now=NOW) is None
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert read_token_file(broken, now=NOW) is None


async def test_a_stored_token_wins_until_it_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "dhan_token.json"
    write_token_file(path, DhanToken("from-file", NOW + timedelta(hours=2)))
    auth = DhanAuth(
        client_id="1", access_token=SecretStr("from-env"), token_file=path, now=lambda: NOW
    )
    assert await auth.token() == "from-file"
    auth.mark_rejected("from-file")
    assert await auth.token() == "from-env"  # falls through, never replays the dead file token
