"""Error types for the Dhan data provider. Messages are safe to log and to show: they never
carry a token, PIN, TOTP secret or client id, and any server body they quote is redacted."""

from __future__ import annotations


class DhanError(Exception):
    """Any failure talking to Dhan's data APIs."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class DhanAuthError(DhanError):
    """The access token is missing, rejected or expired and could not be regenerated."""


class DhanRateLimitError(DhanError):
    """HTTP 429, or a documented per-second limit was about to be exceeded."""


class DhanMalformedResponseError(DhanError):
    """A 2xx response whose body does not match the documented shape."""
