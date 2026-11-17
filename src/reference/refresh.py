"""
The pre-open reference refresh (plan M2.4/M2.5): universe, instrument master and price bands.

* The universe is **pinned** to a snapshot date for the experiment (plan D13): pass
  ``universe_day`` to load that snapshot, or omit it to use today's. ``universe_file``
  (``UNIVERSE_FILE``) replaces the download with a hand-written symbol list and takes precedence
  over both; such a list is not dated, so it is never pinned.
* The instrument master and bands are refreshed daily; when today's download fails the newest
  cached copy is used (stale). Bands are optional (defaults apply); a missing master falls back
  to a Rs 0.05 tick for every instrument. Every degradation is reported for an alert.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import httpx2

from src.config.errors import ConfigError
from src.domain.events import Alert
from src.domain.sink import EventSink
from src.reference.bands import BANDS_NAME, BandTable, fetch_bands, parse_bands
from src.reference.download import ReferenceDataError, Snapshot, latest_snapshot, snapshot_path
from src.reference.instruments import (
    SCRIP_MASTER_NAME,
    InstrumentSet,
    build_instruments,
    fetch_master,
    parse_master,
)
from src.reference.universe import (
    NIFTY50_NAME,
    UniverseMember,
    fetch_universe,
    load_custom_universe,
    load_universe,
)

# How many unsectored symbols to name in the alert before truncating.
_SECTOR_ALERT_LIMIT = 10


def _load_custom(path: Path, sectors: Mapping[str, str] | None) -> list[UniverseMember]:
    """``UNIVERSE_FILE`` as members; an unreadable or malformed list is a config error (exit 2),
    because the operator named the file and nothing else can supply the universe."""
    try:
        return load_custom_universe(path, sectors)
    except OSError as exc:
        raise ConfigError(f"UNIVERSE_FILE cannot be read ({path}): {exc.strerror or exc}") from exc
    except ValueError as exc:
        raise ConfigError(f"UNIVERSE_FILE {path.name} is not a usable symbol list: {exc}") from exc


@dataclass(frozen=True)
class ReferenceData:
    universe: list[UniverseMember]
    universe_day: date
    instruments: InstrumentSet
    snapshots: dict[str, Snapshot] = field(default_factory=dict)
    problems: dict[str, str] = field(default_factory=dict)  # alert key -> message


async def refresh_reference(
    reference_dir: Path,
    day: date,
    *,
    universe_day: date | None = None,
    universe_file: Path | None = None,
    universe_sectors: Mapping[str, str] | None = None,
    client: httpx2.AsyncClient | None = None,
) -> ReferenceData:
    """Load the custom, pinned or today's universe and today's master and bands.

    Raises :class:`ReferenceDataError` when no downloaded universe is available at all, and
    :class:`~src.config.errors.ConfigError` when ``universe_file`` was named but is unusable.
    """
    problems: dict[str, str] = {}
    snapshots: dict[str, Snapshot] = {}

    if universe_file is not None:
        universe, used_day = _load_custom(universe_file, universe_sectors), day
    elif universe_day is not None:
        pinned = snapshot_path(reference_dir, NIFTY50_NAME, universe_day, "csv")
        if not pinned.exists():
            raise ReferenceDataError(f"pinned universe snapshot {pinned.name} is missing")
        universe, used_day = load_universe(pinned), universe_day
    else:
        universe, snap = await fetch_universe(reference_dir, day, client=client)
        snapshots["universe"], used_day = snap, snap.day
        if not snap.fresh:
            problems["reference_stale:universe"] = (
                f"universe from {snap.day} (today's download failed: {snap.error})"
            )

    master = None
    try:
        master, snap = await fetch_master(reference_dir, day, client=client)
        snapshots["instrument_master"] = snap
        if not snap.fresh:
            problems["reference_stale:instrument_master"] = (
                f"instrument master from {snap.day} (today's download failed: {snap.error})"
            )
    except ReferenceDataError as exc:
        problems["reference_missing:instrument_master"] = f"{exc}; using a Rs 0.05 tick for all"

    bands: BandTable | None = None
    try:
        bands, snap = await fetch_bands(reference_dir, day, client=client)
        snapshots["bands"] = snap
        if not snap.fresh:
            problems["reference_stale:bands"] = f"price bands from {snap.day} ({snap.error})"
    except ReferenceDataError as exc:
        problems["reference_missing:bands"] = f"{exc}; using per-series default bands"

    instruments = build_instruments(universe, master, bands)
    if instruments.unmapped:
        problems["reference_unmapped"] = (
            f"{len(instruments.unmapped)} symbols missing from the instrument master "
            f"(fallback tick, no broker token): {', '.join(instruments.unmapped)}"
        )
    unsectored = sorted(m.symbol for m in universe if not m.industry)
    if unsectored:
        named = ", ".join(unsectored[:_SECTOR_ALERT_LIMIT])
        extra = len(unsectored) - _SECTOR_ALERT_LIMIT
        problems["reference_sector_unknown"] = (
            f"{len(unsectored)} universe symbol(s) have no sector ({named}"
            f"{f' and {extra} more' if extra > 0 else ''}) and share the risk engine's single "
            "'Unknown' bucket, where RISK_MAX_SECTOR_PCT caps them all together rather than "
            "each sector apart; add them to UNIVERSE_SECTORS"
        )
    return ReferenceData(universe, used_day, instruments, snapshots, problems)


def alert_reference(data: ReferenceData, sink: EventSink) -> None:
    for key, message in sorted(data.problems.items()):
        sink.emit(Alert(level="WARNING", key=key, message=message), source="reference")


def load_reference_offline(
    reference_dir: Path,
    day: date,
    *,
    universe_file: Path | None = None,
    universe_sectors: Mapping[str, str] | None = None,
) -> InstrumentSet | None:
    """The newest cached universe, instrument master and bands dated on or before ``day`` - no
    network (for replays). ``None`` when there is no universe at all: neither ``universe_file``
    nor a cached snapshot."""
    if universe_file is not None:
        members = _load_custom(universe_file, universe_sectors)
    else:
        snapshot = latest_snapshot(reference_dir, NIFTY50_NAME, "csv", day)
        if snapshot is None:
            return None
        members = load_universe(snapshot[1])
    master_snap = latest_snapshot(reference_dir, SCRIP_MASTER_NAME, "csv", day)
    bands_snap = latest_snapshot(reference_dir, BANDS_NAME, "csv", day)
    master = parse_master(master_snap[1].read_bytes()) if master_snap else None
    bands = parse_bands(bands_snap[1].read_bytes()) if bands_snap else None
    return build_instruments(members, master, bands)
