"""
DhanHQ's OAuth consent flow (documented "Method 3"), for accounts that have an app key/secret.

Three steps, of which only the middle one needs a human:

1. ``POST {auth}/app/generate-consent?client_id=..`` with ``app_id`` / ``app_secret`` headers
   returns a ``consentAppId``.
2. The operator opens ``{auth}/login/consentApp-login?consentAppId=..`` in a browser, logs in and
   consents; Dhan redirects to the app's registered redirect URL with ``?tokenId=..``.
3. ``{auth}/app/consumeApp-consent?tokenId=..`` with the same headers returns the access token
   (24 h validity).

Step 2 cannot be automated - it is the operator's login - so this module only does 1 and 3;
``scripts/dhan_oauth_token.py`` drives the whole thing with the tokenId entered locally on
stdin. The app key/secret identify the *application*; they are not an access token and are
never sent to the data APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from urllib.parse import parse_qs, urlparse

import httpx2

from src.marketdata.dhan.auth import DhanToken, parse_expiry
from src.marketdata.dhan.errors import DhanAuthError
from src.ops.logging_config import redact

CONSENT_PATH = "/app/generate-consent"
CONSENT_LOGIN_PATH = "/login/consentApp-login"
CONSUME_PATH = "/app/consumeApp-consent"


@dataclass(frozen=True, slots=True)
class Consent:
    consent_app_id: str


def consent_login_url(base_url: str, consent: Consent) -> str:
    """The URL the operator opens in their browser (step 2)."""
    return f"{base_url.rstrip('/')}{CONSENT_LOGIN_PATH}?consentAppId={consent.consent_app_id}"


def token_id_from_redirect(text: str) -> str | None:
    """Pull ``tokenId`` out of whatever the operator pastes back: a full redirect URL, a query
    string, or the bare id."""
    candidate = text.strip()
    if not candidate:
        return None
    if "=" not in candidate and "?" not in candidate and "://" not in candidate:
        return candidate
    query = parse_qs(urlparse(candidate).query)
    values = query.get("tokenId") or query.get("token_id")
    return values[0] if values else None


async def generate_consent(
    client: httpx2.AsyncClient,
    *,
    base_url: str,
    client_id: str,
    api_key: str,
    api_secret: str,
) -> Consent:
    response = await client.post(
        f"{base_url.rstrip('/')}{CONSENT_PATH}",
        params={"client_id": client_id},
        headers={"app_id": api_key, "app_secret": api_secret},
    )
    if response.status_code in (401, 403):
        raise DhanAuthError(
            f"Dhan rejected the app key/secret for generate-consent (HTTP {response.status_code});"
            " check DHAN_API_KEY and DHAN_API_SECRET in Dhan Web > DhanHQ Trading APIs",
            status=response.status_code,
        )
    if response.status_code >= 400:
        raise DhanAuthError(
            f"generate-consent failed (HTTP {response.status_code}): {redact(response.text[:300])}",
            status=response.status_code,
        )
    try:
        body = response.json()
        consent_id = str(body["consentAppId"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DhanAuthError("generate-consent returned no consentAppId") from exc
    return Consent(consent_id)


async def consume_consent(
    client: httpx2.AsyncClient,
    *,
    base_url: str,
    token_id: str,
    api_key: str,
    api_secret: str,
    now: datetime,
) -> DhanToken:
    response = await client.get(
        f"{base_url.rstrip('/')}{CONSUME_PATH}",
        params={"tokenId": token_id},
        headers={"app_id": api_key, "app_secret": api_secret},
    )
    if response.status_code in (401, 403):
        raise DhanAuthError(
            f"consume-consent rejected the app key/secret or tokenId (HTTP {response.status_code})",
            status=response.status_code,
        )
    if response.status_code >= 400:
        raise DhanAuthError(
            f"consume-consent failed (HTTP {response.status_code}): {redact(response.text[:300])}",
            status=response.status_code,
        )
    try:
        body = response.json()
        token = str(body["accessToken"])
    except (ValueError, KeyError, TypeError) as exc:
        raise DhanAuthError("consume-consent returned no accessToken") from exc
    return DhanToken(token, parse_expiry(str(body.get("expiryTime") or ""), now))
