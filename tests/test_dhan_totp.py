"""RFC 6238 test vectors pin the TOTP implementation Dhan's token endpoint relies on."""

from __future__ import annotations

import pytest

from src.marketdata.dhan.totp import totp_code

# The RFC's shared secret, "12345678901234567890", in base32.
SECRET = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"

# RFC 6238 appendix B, SHA-1, 30-second step, 8 digits.
VECTORS = [
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
]  # fmt: skip


@pytest.mark.parametrize(("moment", "expected"), VECTORS)
def test_rfc_vectors_at_eight_digits(moment: int, expected: str) -> None:
    assert totp_code(SECRET, at=float(moment), digits=8) == expected


def test_six_digits_is_the_tail_of_the_rfc_vector() -> None:
    # Dhan asks for 6 digits: the same truncated counter value, modulo 10**6.
    assert totp_code(SECRET, at=59.0, digits=6) == "287082"


def test_the_code_turns_with_the_step() -> None:
    assert totp_code(SECRET, at=0.0) == totp_code(SECRET, at=29.0)
    assert totp_code(SECRET, at=30.0) != totp_code(SECRET, at=0.0)


def test_unpadded_and_lowercase_secrets_are_accepted() -> None:
    assert totp_code(SECRET.lower(), at=59.0, digits=8) == "94287082"
    assert totp_code(SECRET.rstrip("="), at=59.0, digits=8) == "94287082"


def test_a_secret_that_is_not_base32_is_refused() -> None:
    with pytest.raises(ValueError, match="base32"):
        totp_code("not-a-secret!", at=59.0)
