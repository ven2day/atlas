"""Durable state for the research layer, in the environment's own SQLite database.

This opens a **second connection** to the same file the event store uses rather than adding a
write path to :class:`~src.store.event_store.EventStore`. That is deliberate:

* the event store stays exactly as audited - research cannot append, project or rebuild events;
* WAL with the shared busy timeout lets a reader and a writer coexist, and the research tables
  (``0003_research.sql``) are not projections, so ``rebuild_projections()`` never touches them;
* research is a sibling of the trading path, and a crash in it cannot take the engine's store
  connection with it.

What is stored, and why it must be: a forecast is only meaningful once it can be scored against
an outcome it did not know. Persisting each published forecast point in time - with its sample
count, baseline and caveats - is what makes the calibration report in
:mod:`src.research.evaluation` an honest measurement instead of a recollection.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src.research.cards import CandidateCard
from src.research.events import ResearchEvent
from src.research.forecast import Forecast
from src.research.mapping import EventImpact
from src.store.sqlite import connect, migrate

FORECAST_COLUMNS = (
    "forecast_id", "instrument_key", "horizon_sessions", "status", "baseline", "as_of_utc",
    "bar_date", "regime", "p_up", "p_side", "p_down", "q10", "q50", "q90", "p_underperform",
    "sideways_band", "n_samples", "n_unconditional", "trend", "vol_band", "annualized_vol",
    "sigma_daily", "data_quality", "notes",
)  # fmt: skip


def _utc(moment: datetime) -> str:
    return moment.astimezone().isoformat() if moment.tzinfo else moment.isoformat()


def _json_default(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    return str(value)


def dumps(value: Any) -> str:
    """JSON for storage. ``Mapping`` keys that are not strings (lookbacks) become strings."""
    return json.dumps(value, default=_json_default, sort_keys=True)


def forecast_id(prediction: Forecast) -> str:
    """Stable id: the same instrument, horizon and bar always resolve to the same forecast."""
    raw = (
        f"{prediction.instrument_key}|{prediction.horizon_sessions}|"
        f"{prediction.bar_date.isoformat() if prediction.bar_date else ''}|"
        f"{prediction.as_of.date().isoformat()}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


class ResearchStore:
    """Read/write access to the ``research_*`` tables. Not a context the engine depends on."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: sqlite3.Connection = connect(path)
        migrate(self._conn)  # idempotent; creates the research tables on an existing database

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ResearchStore:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------------------------

    def _write(self, statements: Sequence[tuple[str, Sequence[Any]]]) -> int:
        """Run every statement in one transaction; all of a run's output lands or none does."""
        if not statements:
            return 0
        try:
            self._conn.execute("BEGIN IMMEDIATE")
            for sql, params in statements:
                self._conn.execute(sql, params)
            self._conn.execute("COMMIT")
        except sqlite3.Error:
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        return len(statements)

    def save_forecasts(self, predictions: Iterable[Forecast]) -> int:
        """Upsert published forecasts. Re-publishing the same bar overwrites, never duplicates."""
        sql = (
            f"INSERT INTO research_forecasts ({','.join(FORECAST_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(FORECAST_COLUMNS))}) "
            f"ON CONFLICT(forecast_id) DO UPDATE SET "
            + ",".join(f"{c}=excluded.{c}" for c in FORECAST_COLUMNS if c != "forecast_id")
        )
        return self._write([(sql, _forecast_row(p)) for p in predictions])

    def save_impacts(self, impacts: Iterable[EventImpact], known_at: Mapping[str, datetime]) -> int:
        sql = (
            "INSERT INTO research_event_impacts (event_id, instrument_key, asset, scope, channel,"
            " direction, materiality, confidence, basis, uncertain, reason, evidence, known_at_utc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(event_id, scope, instrument_key) DO UPDATE SET"
            " asset=excluded.asset, channel=excluded.channel, direction=excluded.direction,"
            " materiality=excluded.materiality, confidence=excluded.confidence,"
            " basis=excluded.basis, uncertain=excluded.uncertain, reason=excluded.reason,"
            " evidence=excluded.evidence, known_at_utc=excluded.known_at_utc"
        )
        rows: list[tuple[str, Sequence[Any]]] = []
        for impact in impacts:
            moment = known_at.get(impact.event_id)
            rows.append(
                (
                    sql,
                    (
                        impact.event_id,
                        impact.instrument_key or "",
                        impact.asset.value,
                        impact.scope.value,
                        impact.channel.value,
                        impact.direction.value,
                        impact.materiality.value if impact.materiality is not None else None,
                        impact.confidence,
                        impact.basis,
                        1 if impact.uncertain else 0,
                        impact.reason,
                        dumps([asdict(e) for e in impact.evidence]),
                        _utc(moment) if moment is not None else "",
                    ),
                )
            )
        return self._write(rows)

    def save_cards(self, cards: Iterable[CandidateCard]) -> int:
        sql = (
            "INSERT INTO research_cards (instrument_key, horizon_sessions, as_of_utc, status,"
            " asset, symbol, sector, confirmation, confidence, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(instrument_key, horizon_sessions, as_of_utc) DO UPDATE SET"
            " status=excluded.status, asset=excluded.asset, symbol=excluded.symbol,"
            " sector=excluded.sector, confirmation=excluded.confirmation,"
            " confidence=excluded.confidence, payload=excluded.payload"
        )
        return self._write(
            [
                (
                    sql,
                    (
                        card.instrument_key,
                        card.horizon_sessions,
                        _utc(card.as_of),
                        card.status.value,
                        card.asset.value,
                        card.symbol,
                        card.sector,
                        card.confirmation.value,
                        card.confidence,
                        dumps(asdict(card)),
                    ),
                )
                for card in cards
            ]
        )

    # -- reading -----------------------------------------------------------------------------

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        return [dict(row) for row in self._conn.execute(sql, params).fetchall()]

    def latest_cards(self, *, horizon: int | None = None, limit: int = 500) -> list[dict[str, Any]]:
        """The most recently published card per instrument, for the console's default view."""
        clauses = ["as_of_utc = (SELECT MAX(as_of_utc) FROM research_cards c2"
                   " WHERE c2.instrument_key = research_cards.instrument_key"
                   " AND c2.horizon_sessions = research_cards.horizon_sessions)"]  # fmt: skip
        params: list[Any] = []
        if horizon is not None:
            clauses.append("horizon_sessions = ?")
            params.append(horizon)
        params.append(limit)
        where = " AND ".join(clauses)
        return self.query(
            f"SELECT * FROM research_cards WHERE {where} ORDER BY status, symbol LIMIT ?", params
        )

    def unsettled_forecasts(self, *, limit: int = 1000) -> list[dict[str, Any]]:
        """Published forecasts whose horizon has not been scored yet."""
        return self.query(
            "SELECT * FROM research_forecasts WHERE outcome_settled_at IS NULL"
            " ORDER BY as_of_utc LIMIT ?",
            (limit,),
        )

    def settled_forecasts(
        self, *, horizon: int | None = None, regime: str | None = None, limit: int = 5000
    ) -> list[dict[str, Any]]:
        clauses = ["outcome_settled_at IS NOT NULL"]
        params: list[Any] = []
        if horizon is not None:
            clauses.append("horizon_sessions = ?")
            params.append(horizon)
        if regime is not None:
            clauses.append("regime = ?")
            params.append(regime)
        params.append(limit)
        return self.query(
            f"SELECT * FROM research_forecasts WHERE {' AND '.join(clauses)}"
            " ORDER BY as_of_utc LIMIT ?",
            params,
        )

    def record_outcome(
        self,
        forecast_row_id: str,
        *,
        outcome_return: float,
        outcome_benchmark_return: float | None,
        outcome_bucket: str,
        settled_at: datetime,
    ) -> None:
        """Score one published forecast against what actually happened."""
        self._write(
            [
                (
                    "UPDATE research_forecasts SET outcome_return=?, outcome_benchmark_return=?,"
                    " outcome_bucket=?, outcome_settled_at=? WHERE forecast_id=?",
                    (
                        outcome_return,
                        outcome_benchmark_return,
                        outcome_bucket,
                        _utc(settled_at),
                        forecast_row_id,
                    ),
                )
            ]
        )


def _forecast_row(prediction: Forecast) -> tuple[Any, ...]:
    return (
        forecast_id(prediction),
        prediction.instrument_key,
        prediction.horizon_sessions,
        prediction.status.value,
        prediction.baseline,
        _utc(prediction.as_of),
        prediction.bar_date.isoformat() if prediction.bar_date is not None else None,
        prediction.regime.value if prediction.regime is not None else None,
        prediction.p_up,
        prediction.p_side,
        prediction.p_down,
        prediction.q10,
        prediction.q50,
        prediction.q90,
        prediction.p_underperform_benchmark,
        prediction.sideways_band,
        prediction.n_samples,
        prediction.n_unconditional,
        prediction.trend,
        prediction.vol_band,
        prediction.annualized_vol,
        prediction.sigma_daily,
        prediction.data_quality,
        dumps(list(prediction.notes)),
    )


def known_at_by_id(events: Iterable[ResearchEvent]) -> dict[str, datetime]:
    """``event_id -> known_at``, the point-in-time stamp an impact is stored with."""
    return {event.event_id: event.known_at for event in events}
