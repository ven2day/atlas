"""
The trading universe (plan D13, M2.4): the NIFTY 50 constituents as of a snapshot date.

Source (verified 2026-10-02): ``https://niftyindices.com/IndexConstituent/ind_nifty50list.csv``
with columns ``Company Name, Industry, Symbol, Series, ISIN Code``. The ``Industry`` column is
the sector map. Snapshots are cached as ``var/reference/nifty50_<date>.csv``; the experiment pins
one date for the whole month (held symbols are added by the engine).

``UNIVERSE_FILE`` replaces that download with a hand-written symbol list, for trading something
other than the index. Such a list carries no ISIN and no industry - Dhan's scrip master has
neither column - so sectors come from ``UNIVERSE_SECTORS`` and anything left out falls into the
risk engine's single ``Unknown`` bucket, where ``RISK_MAX_SECTOR_PCT`` then caps all of them
together rather than each sector apart.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import httpx2

from src.reference.download import Snapshot, fetch_snapshot

NIFTY50_URL = "https://niftyindices.com/IndexConstituent/ind_nifty50list.csv"
NIFTY50_NAME = "nifty50"
NIFTY50_SIZE = 50

DEFAULT_SERIES = "EQ"
_ISIN = re.compile(r"^IN[A-Z0-9]{9}\d$")
_COLUMNS = ("Company Name", "Industry", "Symbol", "Series", "ISIN Code")
# NSE trading symbols: ARE&M, BAJAJ-AUTO, 360ONE, LALPATHLAB.
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9&.\-]*$")


@dataclass(frozen=True, slots=True)
class UniverseMember:
    symbol: str
    series: str
    isin: str
    company: str
    industry: str


def parse_universe(
    body: bytes, *, expected_size: int | None = NIFTY50_SIZE
) -> list[UniverseMember]:
    """Parse and validate a niftyindices constituents CSV."""
    reader = csv.DictReader(io.StringIO(body.decode("utf-8-sig")))
    missing = [c for c in _COLUMNS if c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"constituents CSV lacks columns {missing}")
    members = [
        UniverseMember(
            symbol=row["Symbol"].strip(),
            series=row["Series"].strip(),
            isin=row["ISIN Code"].strip(),
            company=row["Company Name"].strip(),
            industry=row["Industry"].strip(),
        )
        for row in reader
    ]
    symbols = [m.symbol for m in members]
    if expected_size is not None and len(members) != expected_size:
        raise ValueError(f"expected {expected_size} constituents, got {len(members)}")
    if len(set(symbols)) != len(symbols):
        raise ValueError("duplicate symbols in the constituents CSV")
    bad = [m.symbol for m in members if not m.symbol or not _ISIN.match(m.isin) or not m.industry]
    if bad:
        raise ValueError(f"constituents with a bad symbol, ISIN or industry: {bad}")
    return members


async def fetch_universe(
    reference_dir: Path, day: date, *, client: httpx2.AsyncClient | None = None
) -> tuple[list[UniverseMember], Snapshot]:
    """Today's NIFTY 50 snapshot (cached), or the newest valid older one if the download fails."""
    snapshot = await fetch_snapshot(
        NIFTY50_URL,
        directory=reference_dir,
        name=NIFTY50_NAME,
        ext="csv",
        day=day,
        client=client,
        validate=parse_universe,
    )
    return load_universe(snapshot.path), snapshot


def load_universe(path: Path) -> list[UniverseMember]:
    return parse_universe(path.read_bytes())


def sector_map(members: list[UniverseMember]) -> dict[str, str]:
    """Symbol -> sector (the index provider's industry classification)."""
    return {m.symbol: m.industry for m in members}


# -- a hand-written universe (UNIVERSE_FILE) ----------------------------------------------------


def parse_symbol_list(text: str) -> list[tuple[str, str]]:
    """``(symbol, series)`` pairs from ``SYMBOL`` or ``SYMBOL:SERIES`` lines.

    Blank lines and ``#`` comments are skipped, symbols are upper-cased, and the series defaults
    to ``EQ``. Raises :class:`ValueError` naming the offending line for a malformed symbol, a
    duplicate, or a list with nothing in it.
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        symbol, _, series = line.partition(":")
        symbol = symbol.strip().upper()
        series = series.strip().upper() or DEFAULT_SERIES
        if not _SYMBOL.match(symbol):
            raise ValueError(f"line {lineno}: {raw.strip()!r} is not an NSE trading symbol")
        if symbol in seen:
            raise ValueError(f"line {lineno}: {symbol} is listed twice")
        seen.add(symbol)
        out.append((symbol, series))
    if not out:
        raise ValueError("the file lists no symbols")
    return out


def custom_universe(text: str, sectors: Mapping[str, str] | None = None) -> list[UniverseMember]:
    """A symbol list into members: no ISIN (nothing offline carries one for an arbitrary
    symbol), no company name (the scrip master fills that in later), and the sector from
    ``sectors`` when the operator supplied one."""
    lookup = {k.strip().upper(): v.strip() for k, v in (sectors or {}).items()}
    return [
        UniverseMember(
            symbol=symbol, series=series, isin="", company="", industry=lookup.get(symbol, "")
        )
        for symbol, series in parse_symbol_list(text)
    ]


def load_custom_universe(
    path: Path, sectors: Mapping[str, str] | None = None
) -> list[UniverseMember]:
    """Read ``path`` as a symbol list. Raises ``OSError`` when it cannot be read and
    ``ValueError`` when its contents are not a usable list."""
    return custom_universe(path.read_text(encoding="utf-8-sig"), sectors)
