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
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from src.research.cards import CandidateCard
from src.research.events import ResearchEvent
from src.research.forecast import Forecast
from src.research.importing import SourceRecord
from src.research.mapping import EventImpact
from src.store.sqlite import connect, migrate

FORECAST_COLUMNS = (
    "forecast_id", "instrument_key", "horizon_sessions", "status", "baseline", "as_of_utc",
    "bar_date", "regime", "p_up", "p_side", "p_down", "q10", "q50", "q90", "p_underperform",
    "sideways_band", "n_samples", "n_unconditional", "trend", "vol_band", "annualized_vol",
    "sigma_daily", "data_quality", "notes",
)  # fmt: skip


def _utc(moment: datetime) -> str:
    if moment.tzinfo is None:
        raise ValueError("research timestamps must be timezone-aware")
    return moment.astimezone(UTC).isoformat(timespec="microseconds")


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
        f"{_utc(prediction.as_of)}"
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
        """Immutable publications; retrying the same publication leaves it unchanged."""
        sql = (
            f"INSERT INTO research_forecasts ({','.join(FORECAST_COLUMNS)}) "
            f"VALUES ({','.join('?' * len(FORECAST_COLUMNS))}) "
            "ON CONFLICT(forecast_id) DO NOTHING"
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
        clauses = ["julianday(as_of_utc) = (SELECT MAX(julianday(as_of_utc)) FROM research_cards c2"
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
            " AND status = 'ok' AND bar_date IS NOT NULL"
            " ORDER BY julianday(as_of_utc) LIMIT ?",
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
            " ORDER BY julianday(as_of_utc) LIMIT ?",
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

    # -- historical import --------------------------------------------------------------------

    def save_source_records(
        self, records: Iterable[SourceRecord], *, batch: str, imported_at: datetime | None = None
    ) -> tuple[int, int, int]:
        """Store verbatim source records. Returns ``(inserted, duplicates, rejected)``.

        Deduplicated on the stable ``record_id``, so re-importing the same file is a no-op. An
        existing record whose ``content_hash`` differs is left alone and reported as a duplicate:
        the first receipt wins, and a correction arrives as a new record, not an overwrite.
        """
        stamp = _utc(imported_at or datetime.now(UTC))
        materialised = list(records)
        if not materialised:
            return (0, 0, 0)
        ids = [r.record_id for r in materialised]
        placeholders = ",".join("?" * len(ids))
        existing = {
            row["record_id"]
            for row in self.query(
                f"SELECT record_id FROM research_source_records WHERE record_id IN ({placeholders})",
                ids,
            )
        }
        statements: list[tuple[str, Sequence[Any]]] = []
        rejected = 0
        for record in materialised:
            if not record.symbol or record.published_at is None:
                rejected += 1
                continue
            if record.record_id in existing:
                continue
            statements.append(
                (
                    "INSERT INTO research_source_records (record_id, source, source_id, symbol,"
                    " event_type, published_at_utc, received_at_utc, document_url, raw,"
                    " content_hash, import_batch, imported_at_utc) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        record.record_id,
                        record.source,
                        record.source_id,
                        record.symbol,
                        record.event_type,
                        _utc(record.published_at),
                        # The receipt time is *now*, never the publication time: a record imported
                        # today is recorded as received today, however old the announcement is.
                        stamp,
                        record.document_url,
                        json.dumps(record.raw, sort_keys=True, default=str),
                        record.content_hash,
                        batch,
                        stamp,
                    ),
                )
            )
            existing.add(record.record_id)
        self._write(statements)
        duplicates = len(materialised) - rejected - len(statements)
        return (len(statements), duplicates, rejected)

    def begin_import(
        self, *, source: str, range_from: date, range_to: date, symbols: Sequence[str]
    ) -> str:
        import_id = hashlib.sha256(
            f"{source}|{range_from}|{range_to}|{','.join(sorted(symbols))}|"
            f"{datetime.now(UTC).isoformat()}".encode()
        ).hexdigest()[:24]
        self._write(
            [
                (
                    "INSERT INTO research_imports (import_id, source, status, started_at_utc,"
                    " range_from, range_to, symbols) VALUES (?,?,?,?,?,?,?)",
                    (
                        import_id,
                        source,
                        "running",
                        _utc(datetime.now(UTC)),
                        range_from.isoformat(),
                        range_to.isoformat(),
                        dumps(sorted(symbols)),
                    ),
                )
            ]
        )
        return import_id

    def resumable_import(
        self, source: str, range_from: date, range_to: date, symbols: Sequence[str]
    ) -> dict[str, Any] | None:
        """The newest unfinished import for exactly this request, so a retry resumes it."""
        rows = self.query(
            "SELECT * FROM research_imports WHERE source=? AND range_from=? AND range_to=?"
            " AND symbols=? AND status IN ('running','partial','failed')"
            " ORDER BY julianday(started_at_utc) DESC LIMIT 1",
            (source, range_from.isoformat(), range_to.isoformat(), dumps(sorted(symbols))),
        )
        return rows[0] if rows else None

    def update_import_cursor(self, import_id: str, cursor: str | None) -> None:
        self._write(
            [("UPDATE research_imports SET cursor=? WHERE import_id=?", (cursor, import_id))]
        )

    def finish_import(self, report: Any) -> None:
        self._write(
            [
                (
                    "UPDATE research_imports SET status=?, finished_at_utc=?, cursor=?,"
                    " fetched=?, inserted=?, duplicates=?, failures=?, coverage=?, notes=?"
                    " WHERE import_id=?",
                    (
                        report.status,
                        _utc(datetime.now(UTC)),
                        report.cursor,
                        report.fetched,
                        report.inserted,
                        report.duplicates,
                        report.failures,
                        dumps([asdict(w) for w in report.coverage]),
                        dumps(list(report.notes)),
                        report.import_id,
                    ),
                )
            ]
        )

    def imports(self, *, source: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if source is not None:
            clauses.append("source = ?")
            params.append(source)
        params.append(limit)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.query(
            f"SELECT * FROM research_imports{where} ORDER BY julianday(started_at_utc) DESC"
            " LIMIT ?",
            params,
        )

    def source_records(
        self,
        *,
        symbols: Sequence[str] = (),
        range_from: date | None = None,
        range_to: date | None = None,
        event_type: str | None = None,
        limit: int = 5000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if symbols:
            clauses.append(f"symbol IN ({','.join('?' * len(symbols))})")
            params.extend(symbols)
        if range_from is not None:
            clauses.append("published_at_utc >= ?")
            params.append(_utc(datetime.combine(range_from, time.min, tzinfo=UTC)))
        if range_to is not None:
            clauses.append("published_at_utc < ?")
            params.append(
                _utc(datetime.combine(range_to + timedelta(days=1), time.min, tzinfo=UTC))
            )
        if event_type is not None:
            clauses.append("event_type = ?")
            params.append(event_type)
        params.append(limit)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.query(
            f"SELECT * FROM research_source_records{where} ORDER BY published_at_utc LIMIT ?",
            params,
        )

    # -- versioned classifications -------------------------------------------------------------

    def save_classifications(self, rows: Iterable[Mapping[str, Any]]) -> int:
        sql = (
            "INSERT INTO research_classifications (classification_id, record_id,"
            " classified_at_utc, model, prompt_version, rules_version, retrospective, event_type,"
            " direction, materiality, confidence, supersedes, payload)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(classification_id) DO NOTHING"
        )
        return self._write([(sql, _classification_row(r)) for r in rows])

    def latest_classifications(self) -> dict[str, dict[str, Any]]:
        """``record_id`` -> its newest classification. Corrections win; nothing is lost."""
        out: dict[str, dict[str, Any]] = {}
        for row in self.query(
            "SELECT * FROM research_classifications ORDER BY julianday(classified_at_utc)"
        ):
            out[str(row["record_id"])] = row
        return out

    # -- price provenance and outcomes ----------------------------------------------------------

    def save_price_datasets(self, rows: Iterable[Mapping[str, Any]]) -> int:
        sql = (
            "INSERT INTO research_price_datasets (dataset_id, instrument_key, provider,"
            " retrieved_at_utc, requested_from, requested_to, available_from, available_to,"
            " sessions, missing_sessions, adjustment, benchmark_key, benchmark_sessions, notes)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(dataset_id) DO UPDATE SET retrieved_at_utc=excluded.retrieved_at_utc,"
            " available_from=excluded.available_from, available_to=excluded.available_to,"
            " sessions=excluded.sessions, missing_sessions=excluded.missing_sessions,"
            " benchmark_sessions=excluded.benchmark_sessions, notes=excluded.notes"
        )
        return self._write([(sql, _price_dataset_row(r)) for r in rows])

    def save_event_outcomes(self, rows: Iterable[Mapping[str, Any]]) -> int:
        sql = (
            "INSERT INTO research_event_outcomes (outcome_id, record_id, instrument_key,"
            " horizon_sessions, evaluation_mode, status, anchor_date, outcome_date,"
            " instrument_return, benchmark_return, abnormal_return, bucket, computed_at_utc,"
            " notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(outcome_id) DO UPDATE SET status=excluded.status,"
            " anchor_date=excluded.anchor_date, outcome_date=excluded.outcome_date,"
            " instrument_return=excluded.instrument_return,"
            " benchmark_return=excluded.benchmark_return,"
            " abnormal_return=excluded.abnormal_return, bucket=excluded.bucket,"
            " computed_at_utc=excluded.computed_at_utc, notes=excluded.notes"
        )
        return self._write([(sql, _outcome_row(r)) for r in rows])

    def event_outcomes(
        self, *, status: str | None = None, horizon: int | None = None, limit: int = 20000
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if horizon is not None:
            clauses.append("horizon_sessions = ?")
            params.append(horizon)
        params.append(limit)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.query(
            f"SELECT * FROM research_event_outcomes{where} ORDER BY anchor_date LIMIT ?", params
        )

    # -- walk-forward publications --------------------------------------------------------------

    def save_walkforward(self, rows: Iterable[Mapping[str, Any]]) -> int:
        """Immutable publications: a frozen forecast is never revised, only later scored."""
        sql = (
            "INSERT INTO research_walkforward (publication_id, run_id, fold, origin_date,"
            " record_id, instrument_key, horizon_sessions, approach, cohort, p_up, p_side,"
            " p_down, q10, q50, q90, n_train, fallback_used, configuration, published_at_utc)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(publication_id) DO NOTHING"
        )
        return self._write([(sql, _walkforward_row(r)) for r in rows])

    def walkforward(
        self,
        *,
        run_id: str | None = None,
        settled: bool | None = None,
        approach: str | None = None,
        limit: int = 50000,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = ?")
            params.append(run_id)
        if approach is not None:
            clauses.append("approach = ?")
            params.append(approach)
        if settled is True:
            clauses.append("settled_at_utc IS NOT NULL")
        elif settled is False:
            clauses.append("settled_at_utc IS NULL")
        params.append(limit)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        return self.query(
            f"SELECT * FROM research_walkforward{where} ORDER BY origin_date LIMIT ?", params
        )

    def settle_walkforward(self, publication_id: str, *, row: Mapping[str, Any]) -> None:
        self._write(
            [
                (
                    "UPDATE research_walkforward SET outcome_return=?, benchmark_return=?,"
                    " abnormal_return=?, outcome_bucket=?, settled_at_utc=?"
                    " WHERE publication_id=?",
                    (
                        row.get("outcome_return"),
                        row.get("benchmark_return"),
                        row.get("abnormal_return"),
                        row.get("outcome_bucket"),
                        _utc(datetime.now(UTC)),
                        publication_id,
                    ),
                )
            ]
        )

    def save_evaluation(self, row: Mapping[str, Any]) -> str:
        evaluation_id = str(row["evaluation_id"])
        self._write(
            [
                (
                    "INSERT INTO research_evaluations (evaluation_id, run_id, created_at_utc,"
                    " evaluation_mode, horizons, configuration, payload)"
                    " VALUES (?,?,?,?,?,?,?)"
                    " ON CONFLICT(evaluation_id) DO UPDATE SET payload=excluded.payload,"
                    " created_at_utc=excluded.created_at_utc",
                    (
                        evaluation_id,
                        str(row["run_id"]),
                        _utc(datetime.now(UTC)),
                        str(row["evaluation_mode"]),
                        dumps(list(row.get("horizons") or [])),
                        dumps(row.get("configuration") or {}),
                        dumps(row.get("payload") or {}),
                    ),
                )
            ]
        )
        return evaluation_id

    def latest_evaluation(self, *, run_id: str | None = None) -> dict[str, Any] | None:
        if run_id is not None:
            rows = self.query(
                "SELECT * FROM research_evaluations WHERE run_id=?"
                " ORDER BY julianday(created_at_utc) DESC LIMIT 1",
                (run_id,),
            )
        else:
            rows = self.query(
                "SELECT * FROM research_evaluations ORDER BY julianday(created_at_utc) DESC LIMIT 1"
            )
        return rows[0] if rows else None


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


# -- row shapers for the history tables ---------------------------------------------------------
# These take plain mappings rather than the research dataclasses so that ``outcomes.py`` and
# ``walkforward.py`` can depend on this module without this module depending back on them.


def _ts(value: Any) -> str:
    """A timestamp column: an aware datetime, or an already-ISO string."""
    if isinstance(value, datetime):
        return _utc(value)
    return str(value)


def _day(value: Any) -> str | None:
    """A date column, from a date, a datetime or an ISO string."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _classification_row(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["classification_id"]),
        str(row["record_id"]),
        _ts(row["classified_at"]),
        str(row["model"]),
        row.get("prompt_version"),
        row.get("rules_version"),
        1 if row.get("retrospective") else 0,
        row.get("event_type"),
        row.get("direction"),
        row.get("materiality"),
        row.get("confidence"),
        row.get("supersedes"),
        dumps(row.get("payload") or {}),
    )


def _price_dataset_row(row: Mapping[str, Any]) -> tuple[Any, ...]:
    sessions = row.get("benchmark_sessions")
    return (
        str(row["dataset_id"]),
        str(row["instrument_key"]),
        str(row["provider"]),
        _ts(row["retrieved_at"]),
        _day(row.get("requested_from")),
        _day(row.get("requested_to")),
        _day(row.get("available_from")),
        _day(row.get("available_to")),
        int(row.get("sessions") or 0),
        dumps(list(row.get("missing_sessions") or [])),
        str(row.get("adjustment") or ""),
        row.get("benchmark_key"),
        None if sessions is None else int(sessions),
        dumps(list(row.get("notes") or [])),
    )


def _outcome_row(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["outcome_id"]),
        str(row["record_id"]),
        str(row["instrument_key"]),
        int(row["horizon_sessions"]),
        str(row["evaluation_mode"]),
        str(row["status"]),
        _day(row.get("anchor_date")),
        _day(row.get("outcome_date")),
        row.get("instrument_return"),
        row.get("benchmark_return"),
        row.get("abnormal_return"),
        row.get("bucket"),
        _ts(row["computed_at"]),
        dumps(list(row.get("notes") or [])),
    )


def _walkforward_row(row: Mapping[str, Any]) -> tuple[Any, ...]:
    return (
        str(row["publication_id"]),
        str(row["run_id"]),
        int(row["fold"]),
        _day(row["origin_date"]),
        str(row["record_id"]),
        str(row["instrument_key"]),
        int(row["horizon_sessions"]),
        str(row["approach"]),
        str(row["cohort"]),
        row.get("p_up"),
        row.get("p_side"),
        row.get("p_down"),
        row.get("q10"),
        row.get("q50"),
        row.get("q90"),
        int(row.get("n_train") or 0),
        1 if row.get("fallback_used") else 0,
        dumps(row.get("configuration") or {}),
        _ts(row["published_at"]),
    )
