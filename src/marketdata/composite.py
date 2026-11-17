"""
Composing history sources: the primary serves the priced universe, a secondary serves the few
keys the primary cannot (today: the NIFTY 50 index, which Dhan's cash-equity master has no row
for and which the regime reads).

Each bar keeps the ``source`` of the provider that served it, so a mixed day is visible in the
tape, the replay and the console rather than being blended into one anonymous series.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Protocol

from src.domain.types import Instrument
from src.marketdata.history import HistoryResult


class HistorySourceLike(Protocol):
    """The engine's ``HistorySource`` shape, restated locally to avoid an engine import."""

    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult: ...


class CompositeHistorySource:
    def __init__(
        self,
        *,
        primary: HistorySourceLike,
        secondary: HistorySourceLike,
        secondary_keys: Collection[str],
        extra_instruments: Sequence[Instrument] = (),
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._secondary_keys = frozenset(secondary_keys)
        # Instruments only the primary prices (e.g. an index mapped by DHAN_SECURITY_IDS).
        self._extra_instruments = tuple(extra_instruments)

    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult:
        extra_map = dict(extra or {})
        secondary_extra = {k: v for k, v in extra_map.items() if k in self._secondary_keys}
        primary_extra = {k: v for k, v in extra_map.items() if k not in self._secondary_keys}
        primary_result, secondary_result = await asyncio.gather(
            self._primary.fetch(
                [*instruments, *self._extra_instruments],
                settled_before=settled_before,
                expected_last=expected_last,
                extra=primary_extra or None,
            ),
            self._secondary.fetch(
                [],
                settled_before=settled_before,
                expected_last=expected_last,
                extra=secondary_extra or None,
            ),
        )
        series = {**primary_result.series, **secondary_result.series}
        failed = {
            key: reason
            for key, reason in {**primary_result.failed, **secondary_result.failed}.items()
            if key not in series
        }
        return HistoryResult(
            series=series,
            failed=failed,
            repaired={**primary_result.repaired, **secondary_result.repaired},
            lagging={**primary_result.lagging, **secondary_result.lagging},
        )
