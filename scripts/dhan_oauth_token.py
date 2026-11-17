"""Mint a DhanHQ access token through the OAuth consent flow and store it for the engine.

Dhan's app-key flow needs one human step (the browser login/consent), so this script does steps
1 and 3 of the documented flow and asks you for the ``tokenId`` the browser redirect ends with.
Everything you paste goes to stdin on this machine only - never into chat, a file you commit,
or a command-line argument. The resulting token is written to the token file (under the
gitignored state directory by default) with the expiry Dhan reported; the engine reads it there.

    uv run python scripts/dhan_oauth_token.py

Needs DHAN_CLIENT_ID, DHAN_API_KEY and DHAN_API_SECRET in .env.
"""

import asyncio
import getpass
import sys
from pathlib import Path

import httpx2

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.config import get_settings
from src.config.errors import ConfigError
from src.marketdata.dhan.auth import write_token_file
from src.marketdata.dhan.oauth import (
    consume_consent,
    consent_login_url,
    generate_consent,
    token_id_from_redirect,
)
from src.marketdata.dhan.setup import token_file_for
from src.ops.process import run_entry_point
from src.utils.market_time import now_ist


async def _run() -> int:
    settings = get_settings()
    if not settings.dhan_client_id or not settings.dhan_api_key or not settings.dhan_api_secret:
        raise ConfigError(
            "this script needs DHAN_CLIENT_ID, DHAN_API_KEY and DHAN_API_SECRET in .env "
            "(Dhan Web > My Profile > DhanHQ Trading APIs). For a headless setup without an "
            "app, use DHAN_PIN + DHAN_TOTP_SECRET instead and skip this script."
        )
    target = token_file_for(settings)
    async with httpx2.AsyncClient(timeout=30.0) as client:
        consent = await generate_consent(
            client,
            base_url=settings.dhan_auth_url,
            client_id=settings.dhan_client_id,
            api_key=settings.dhan_api_key.get_secret_value(),
            api_secret=settings.dhan_api_secret.get_secret_value(),
        )
    url = consent_login_url(settings.dhan_auth_url, consent)
    print("1) Open this URL in a browser, log in and consent:")
    print(f"   {url}")
    if settings.dhan_redirect_url:
        print(f"   (the browser will end at {settings.dhan_redirect_url} with ?tokenId=...)")
    print("2) Paste the tokenId, or the whole redirect URL, from the browser's address bar.")
    print("   It is read from stdin on this machine and never printed back.")
    pasted = getpass.getpass("tokenId / redirect URL: ")
    token_id = token_id_from_redirect(pasted)
    if not token_id:
        raise ConfigError("no tokenId found in what was pasted; copy the redirect URL verbatim")
    async with httpx2.AsyncClient(timeout=30.0) as client:
        token = await consume_consent(
            client,
            base_url=settings.dhan_auth_url,
            token_id=token_id,
            api_key=settings.dhan_api_key.get_secret_value(),
            api_secret=settings.dhan_api_secret.get_secret_value(),
            now=now_ist(),
        )
    write_token_file(target, token)
    print(f"3) Access token stored in {target} (valid until {token.expiry.isoformat()}).")
    print("   MARKET_DATA_PROVIDER=dhan will pick it up on the next run.")
    return 0


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    run_entry_point("dhan_oauth_token", main)
