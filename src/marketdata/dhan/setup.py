"""
Startup checks and construction helpers for ``MARKET_DATA_PROVIDER=dhan``.

Credential problems and a missing Data API subscription fail fast with a
:class:`~src.config.errors.ConfigError` (exit code 2), the same convention as a misconfigured
LLM role, and both messages name the exact environment variables or the Dhan Web page to visit
- never a secret value.
"""

from __future__ import annotations

from pathlib import Path

from src.config.errors import ConfigError
from src.config.settings import Settings
from src.domain.events import Alert
from src.domain.sink import EventSink
from src.marketdata.dhan.auth import TOKEN_FILE_NAME, DhanAuth
from src.marketdata.dhan.client import DhanClient
from src.marketdata.dhan.errors import DhanError
from src.marketdata.dhan.history_cache import DailyCandleCache
from src.marketdata.dhan.instruments import DhanMapping, parse_security_ids
from src.marketdata.dhan.pacer import SqlitePacer

PROVIDER = "dhan"
# Dhan's per-second caps are per account, and more than one process may share the account (the
# live engine and a research run): both pacing and the daily-candle cache live under the state
# directory so every process on this machine cooperates through them.
PACER_FILE = "dhan_pacer.sqlite3"
HISTORY_CACHE_FILE = "dhan_history_cache.sqlite3"


def security_overrides(settings: Settings) -> dict[str, DhanMapping]:
    """``DHAN_SECURITY_IDS`` as parsed mappings (empty when unset)."""
    return parse_security_ids(settings.dhan_security_ids)


def build_pacer(settings: Settings) -> SqlitePacer:
    return SqlitePacer(settings.state_dir / PACER_FILE)


def build_history_cache(settings: Settings) -> DailyCandleCache:
    return DailyCandleCache(settings.state_dir / HISTORY_CACHE_FILE)


def token_file_for(settings: Settings) -> Path:
    """Where the OAuth helper stores its minted token: under the gitignored state directory."""
    return settings.dhan_token_file or settings.state_dir / TOKEN_FILE_NAME


def validate_market_data_provider(settings: Settings) -> None:
    """Raise one ``ConfigError`` listing everything missing for the selected provider."""
    if settings.market_data_provider != PROVIDER:
        return
    problems: list[str] = []
    if not settings.dhan_client_id:
        problems.append("DHAN_CLIENT_ID is not set")
    if bool(settings.dhan_api_key) != bool(settings.dhan_api_secret):
        problems.append("DHAN_API_KEY and DHAN_API_SECRET must be set together (or not at all)")
    has_token_file = token_file_for(settings).exists()
    has_static = settings.dhan_access_token is not None
    has_totp = settings.dhan_pin is not None and settings.dhan_totp_secret is not None
    if not (has_token_file or has_static or has_totp):
        problems.append(
            "no way to obtain an access token: run scripts/dhan_oauth_token.py once (it uses "
            "DHAN_API_KEY/DHAN_API_SECRET and writes a token file), or set DHAN_ACCESS_TOKEN, or "
            "set both DHAN_PIN and DHAN_TOTP_SECRET for headless renewal"
        )
    if problems:
        raise ConfigError("MARKET_DATA_PROVIDER=dhan: " + "; ".join(problems) + ".")


def build_auth(settings: Settings) -> DhanAuth:
    return DhanAuth(
        client_id=settings.dhan_client_id or "",
        pin=settings.dhan_pin,
        totp_secret=settings.dhan_totp_secret,
        access_token=settings.dhan_access_token,
        token_file=token_file_for(settings),
        base_url=settings.dhan_auth_url,
    )


def build_client(settings: Settings, auth: DhanAuth | None = None) -> DhanClient:
    return DhanClient(
        auth=auth or build_auth(settings),
        base_url=settings.dhan_base_url,
        pacer=build_pacer(settings),  # the account's budget is shared across processes
    )


async def check_data_plan(client: DhanClient, *, sink: EventSink | None = None) -> None:
    """``GET /profile`` reports the Data API subscription as ``dataPlan`` / ``dataValidity``.

    ``Deactive`` stops startup: quoting Dhan prices without the entitlement would only produce
    4xx responses later, in the middle of a session. An unreadable plan (network down, odd
    response) warns and continues - the per-request errors then say what happened.
    """
    try:
        plan = await client.profile()
    except DhanError as exc:
        if sink is not None:
            sink.emit(
                Alert(level="WARNING", key="dhan_plan_unknown",
                      message=f"could not read the Dhan data plan: {exc}"),
                source="marketdata",
            )  # fmt: skip
        return
    status = (plan.plan or "").strip().lower()
    if status == "active":
        return
    if status == "deactive":
        raise ConfigError(
            "this Dhan account's Data API subscription is not active (GET /profile reports "
            f"dataPlan={plan.plan}, dataValidity={plan.validity}). Market data APIs are a paid "
            "add-on to the free Trading APIs: subscribe in Dhan Web > My Profile > DhanHQ "
            "Trading APIs (Dhan publishes the current price there and in its help centre), or "
            "set MARKET_DATA_PROVIDER=yfinance."
        )
    if sink is not None:
        sink.emit(
            Alert(level="WARNING", key="dhan_plan_unknown",
                  message="GET /profile returned no dataPlan; continuing on per-request errors"),
            source="marketdata",
        )  # fmt: skip
