"""
RFC 6238 TOTP codes (HMAC-SHA1, 30 s step, 6 digits), which is what Dhan's token endpoint
expects in its ``totp`` query parameter.

Implemented on the standard library rather than pulling in a dependency: it is twelve lines of
HMAC, and the RFC's own test vectors pin it down (see ``tests/test_dhan_totp.py``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import struct
import time

DEFAULT_PERIOD_S = 30
DEFAULT_DIGITS = 6


def _decode_secret(secret: str) -> bytes:
    text = "".join(secret.split()).upper()
    padding = "=" * (-len(text) % 8)  # authenticator apps usually show the secret unpadded
    try:
        return base64.b32decode(text + padding, casefold=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("the TOTP secret is not valid base32") from exc


def totp_code(
    secret: str,
    *,
    at: float | None = None,
    period_s: int = DEFAULT_PERIOD_S,
    digits: int = DEFAULT_DIGITS,
) -> str:
    """The current ``digits``-wide code for ``secret`` (zero-padded)."""
    if period_s <= 0 or digits <= 0:
        raise ValueError("period and digits must be positive")
    moment = time.time() if at is None else at
    counter = int(moment) // period_s
    digest = hmac.new(_decode_secret(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    truncated = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(truncated % 10**digits).zfill(digits)
