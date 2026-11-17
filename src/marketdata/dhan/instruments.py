"""
Mapping RakshaQuant instruments onto Dhan's identifiers.

A Dhan security id is **not** the ticker. By default it comes from the scrip master that
``src.reference.instruments`` downloads daily (``SEM_SMST_SECURITY_ID``, stored on each
``Instrument`` as ``broker_tokens["dhan"]``). ``DHAN_SECURITY_IDS`` overrides or extends that
with explicit ``SYMBOL=SEGMENT:ID`` pairs - the only way to price something the equity master
has no row for, such as the NIFTY 50 index (``NIFTY50=IDX_I:13``).

An instrument with neither a master row nor an override is reported unavailable; a symbol is
never guessed into a security id.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from src.config.errors import ConfigError
from src.domain.types import Instrument

# Annexure values (dhanhq.co/docs/v2/annexure): NSE cash equities, and indices.
EXCHANGE_SEGMENT = "NSE_EQ"
INSTRUMENT_TYPE = "EQUITY"
INDEX_SEGMENT = "IDX_I"
INDEX_INSTRUMENT = "INDEX"
TOKEN_KEY = "dhan"

# NSE_FNO needs expiry selection this experiment does not have; the other exchanges are out of
# scope for an NSE cash-equity paper book.
SUPPORTED_SEGMENTS = frozenset({EXCHANGE_SEGMENT, INDEX_SEGMENT})


@dataclass(frozen=True, slots=True)
class DhanMapping:
    segment: str
    instrument_type: str
    security_id: str


def parse_security_ids(text: str | None) -> dict[str, DhanMapping]:
    """``SYMBOL=SEGMENT:ID`` pairs, comma separated: ``NIFTY50=IDX_I:13,INFY=NSE_EQ:1333``."""
    out: dict[str, DhanMapping] = {}
    if not text:
        return out
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        symbol, _, rest = part.partition("=")
        segment, _, sid = rest.partition(":")
        symbol, segment, sid = symbol.strip().upper(), segment.strip().upper(), sid.strip()
        if not symbol or segment not in SUPPORTED_SEGMENTS or not sid.isdigit():
            raise ConfigError(
                f"DHAN_SECURITY_IDS: cannot read {part!r}; expected SYMBOL=SEGMENT:ID with "
                f"SEGMENT one of {sorted(SUPPORTED_SEGMENTS)} and a numeric Dhan security id"
            )
        out[symbol] = DhanMapping(
            segment=segment,
            instrument_type=INDEX_INSTRUMENT if segment == INDEX_SEGMENT else INSTRUMENT_TYPE,
            security_id=sid,
        )
    return out


def mapping_for(
    instrument: Instrument, overrides: Mapping[str, DhanMapping] | None = None
) -> DhanMapping | None:
    override = (overrides or {}).get(instrument.symbol)
    if override is not None:
        return override
    sid = instrument.broker_tokens.get(TOKEN_KEY)
    return DhanMapping(EXCHANGE_SEGMENT, INSTRUMENT_TYPE, sid) if sid else None


def security_id(
    instrument: Instrument, overrides: Mapping[str, DhanMapping] | None = None
) -> str | None:
    """Dhan's security id for an instrument, or None when nothing maps it."""
    mapping = mapping_for(instrument, overrides)
    return mapping.security_id if mapping is not None else None


def describe(instrument: Instrument, overrides: Mapping[str, DhanMapping] | None = None) -> str:
    """A log-safe description: symbol and whether a mapping exists, never a credential."""
    return f"{instrument.symbol} ({'mapped' if security_id(instrument, overrides) else 'no dhan security id'})"
