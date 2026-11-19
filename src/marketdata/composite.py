"""Composing history sources: the primary serves the priced universe, a secondary serves the few
keys the primary cannot (today: the NIFTY 50 index, which Dhan's cash-equity master has no row
for and which the regime reads).

A key can also be *routed* to the primary (e.g. an index mapped through ``DHAN_SECURITY_IDS``)
and still be listed as a ``secondary_fallback_keys`` entry: when the primary then fails for it -
a rate limit, an outage - the secondary is asked as well, so one provider's bad day does not
leave the regime unknown. The primary's own series always wins when it exists.

Each bar keeps the ``source`` of the provider that served it, so a mixed day is visible in the
tape, the replay and the console rather than being blended into one anonymous series.
"""

from __future__ import annotations

import asyncio
from collections.abc import Collection, Mapping, Sequence
from datetime import date
from typing import Any, Protocol

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
        secondary_fallback_keys: Collection[str] = (),
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._secondary_keys = frozenset(secondary_keys)
        # Instruments only the primary prices (e.g. an index mapped by DHAN_SECURITY_IDS).
        self._extra_instruments = tuple(extra_instruments)
        # Keys the secondary also fetches, so a primary failure still has an answer.
        self._fallback_keys = frozenset(secondary_fallback_keys)

    async def fetch(
        self,
        instruments: Sequence[Instrument],
        *,
        settled_before: date,
        expected_last: date | None,
        extra: Mapping[str, str] | None = None,
    ) -> HistoryResult:
        extra_map = dict(extra or {})
        served_by_secondary = self._secondary_keys | self._fallback_keys
        secondary_extra = {k: v for k, v in extra_map.items() if k in served_by_secondary}
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
        # The primary wins wherever it answered; the secondary fills the rest (its own keys and
        # any fallback key the primary failed).
        from_primary = set(primary_result.series)
        series = {**secondary_result.series, **primary_result.series}

        def _kept(mapping: Mapping[str, Any], keys: set[str]) -> dict[str, Any]:
            return {k: v for k, v in mapping.items() if k in keys}

        from_secondary = set(secondary_result.series) - from_primary
        failed: dict[str, str] = {}
        for key in set(primary_result.failed) | set(secondary_result.failed):
            if key in series:
                continue
            reasons = list(
                dict.fromkeys(
                    r
                    for r in (primary_result.failed.get(key), secondary_result.failed.get(key))
                    if r
                )
            )
            failed[key] = (
                " | ".join(reasons) if len(reasons) > 1 else (reasons[0] if reasons else "")
            )
        return HistoryResult(
            series=series,
            failed=failed,
            repaired={
                **_kept(primary_result.repaired, from_primary),
                **_kept(secondary_result.repaired, from_secondary),
            },
            lagging={
                **_kept(primary_result.lagging, from_primary),
                **_kept(secondary_result.lagging, from_secondary),
            },
            cached={
                **_kept(primary_result.cached, from_primary),
                **_kept(secondary_result.cached, from_secondary),
            },
            adjustments={
                **_kept(primary_result.adjustments, from_primary),
                **_kept(secondary_result.adjustments, from_secondary),
            },
        )
