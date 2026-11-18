"""Offline tests for historical import, provenance, outcomes and walk-forward evaluation.

Every fixture here is synthetic and local: no network, no broker credentials, no orders. Dates
stay inside 2026 because the NSE calendar fails closed outside the years it covers.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import pytest

from src.domain.calendar import get_calendar
from src.domain.types import MarketDataSource
from src.marketdata.history import DailySeries
from src.research import historical
from src.research.historical import RULES_VERSION
from src.research.importing import (
    HttpArchiveSource,
    ImportSourceError,
    LocalFileSource,
    SourceNotConfiguredError,
    SourcePage,
    SourceRecord,
)
from src.research.metrics import evaluate
from src.research.outcomes import (
    STATUS_NO_BENCHMARK,
    STATUS_PENDING,
    STATUS_SETTLED,
    EventOutcome,
    event_outcome,
    summarize_outcomes,
)
from src.research.provenance import (
    EvaluationMode,
    EventTimestamps,
    MixedEvaluationModesError,
    check_single_mode,
)
from src.research.store import ResearchStore
from src.research.walkforward import (
    Approach,
    Publication,
    WalkForwardConfig,
    cohort_of,
    plan_folds,
    publish,
    train_pool,
)
from src.utils.market_time import IST

KEY = "NSE:EQ:INFY"
BENCH = "NSE:INDEX:NIFTY50"
CAL = get_calendar()
DAYS = CAL.trading_days(date(2026, 1, 2), date(2026, 9, 30))
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=IST)


def series(
    days: Sequence[date] = DAYS,
    *,
    key: str = KEY,
    source: MarketDataSource = MarketDataSource.YFINANCE,
    wave: float = 0.01,
) -> DailySeries:
    """A deterministic price path with enough variation to populate every outcome bucket."""
    steps = np.arange(len(days))
    close = 100 * np.exp(steps * 0.0015 + wave * np.sin(steps / 4.0))
    frame = pd.DataFrame(
        {c: close for c in ("open", "high", "low", "close")}, index=pd.to_datetime(days)
    )
    frame["volume"] = 1_000_000
    frame["factor"] = 1.0
    return DailySeries(key, frame, source)


def timestamps(
    published: datetime,
    *,
    received: datetime | None = None,
    classified: datetime | None = None,
) -> EventTimestamps:
    got = received or (published + timedelta(minutes=3))
    return EventTimestamps(
        published_at=published,
        received_at=got,
        classified_at=classified or (got + timedelta(minutes=1)),
        recorded_at=got + timedelta(minutes=1),
    )


def outcome(
    *,
    record_id: str,
    horizon: int,
    anchor: date,
    end: date,
    abnormal: float,
    bucket: str,
    key: str = KEY,
    mode: EvaluationMode = EvaluationMode.RECONSTRUCTED,
) -> EventOutcome:
    return EventOutcome(
        outcome_id=f"{record_id}|{horizon}",
        record_id=record_id,
        instrument_key=key,
        horizon_sessions=horizon,
        evaluation_mode=mode,
        status=STATUS_SETTLED,
        computed_at=NOW,
        anchor_date=anchor,
        outcome_date=end,
        instrument_return=abnormal,
        benchmark_return=0.0,
        abnormal_return=abnormal,
        bucket=bucket,
    )


def write_export(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text(json.dumps({"records": records}), encoding="utf-8")
    return path


class ScriptedSource:
    """A source that replays canned pages, optionally failing on a chosen page."""

    def __init__(self, pages: list[tuple[list[SourceRecord], str | None]], fail_on: int | None = None,
                 retryable: bool = True) -> None:  # fmt: skip
        self._pages = pages
        self._fail_on = fail_on
        self._retryable = retryable
        self.calls = 0

    @property
    def name(self) -> str:
        return "scripted"

    @property
    def supports_pagination(self) -> bool:
        return True

    async def fetch(self, *, range_from, range_to, symbols, cursor, limit) -> SourcePage:
        self.calls += 1
        if self._fail_on is not None and self.calls > self._fail_on:
            raise ImportSourceError("archive unreachable", retryable=self._retryable)
        index = 0 if cursor is None else int(cursor)
        records, next_cursor = self._pages[index]
        return SourcePage(records=tuple(records), next_cursor=next_cursor, answered=True)

    async def aclose(self) -> None:
        return None


def record(symbol: str, published: datetime, *, source_id: str | None = None,
           event_type: str = "order_win", **extra: Any) -> SourceRecord:  # fmt: skip
    raw: dict[str, Any] = {"symbol": symbol, "event_type": event_type, **extra}
    return SourceRecord(
        source="local_file", symbol=symbol, published_at=published, raw=raw,
        source_id=source_id, event_type=event_type,
    )  # fmt: skip


# -- import: deduplication, resumption and coverage ------------------------------------------------


async def test_reimporting_the_same_file_inserts_nothing(tmp_path: Path) -> None:
    path = write_export(
        tmp_path / "events.json",
        [
            {"symbol": "INFY", "event_type": "order_win", "published_at": "2026-03-04T10:00:00",
             "source_id": "A1", "title": "Wins contract"},
            {"symbol": "TCS", "event_type": "results", "published_at": "2026-03-05T10:00:00",
             "source_id": "A2", "title": "Q4 results"},
        ],
    )  # fmt: skip
    with ResearchStore(tmp_path / "research.db") as store:
        first = await historical.import_history(
            store, LocalFileSource(path), range_from=date(2026, 1, 1), range_to=date(2026, 6, 30)
        )
        second = await historical.import_history(
            store, LocalFileSource(path), range_from=date(2026, 1, 1), range_to=date(2026, 6, 30)
        )
        assert (first.inserted, first.duplicates) == (2, 0)
        assert (second.inserted, second.duplicates) == (0, 2)
        assert len(store.source_records()) == 2


async def test_record_identity_is_stable_without_a_source_id() -> None:
    published = datetime(2026, 3, 4, 10, 0, tzinfo=IST)
    a = record("INFY", published, title="Wins a contract")
    b = record("INFY", published, title="Wins a contract")
    c = record("INFY", published, title="Wins a different contract")
    assert a.record_id == b.record_id
    assert a.record_id != c.record_id


async def test_an_interrupted_import_resumes_and_completes(tmp_path: Path) -> None:
    pages = [
        ([record("INFY", datetime(2026, 3, 2, 10, tzinfo=IST), source_id="p1")], "1"),
        ([record("INFY", datetime(2026, 3, 3, 10, tzinfo=IST), source_id="p2")], None),
    ]
    with ResearchStore(tmp_path / "research.db") as store:
        broken = ScriptedSource(pages, fail_on=1)
        first = await historical.import_history(
            store, broken, range_from=date(2026, 3, 1), range_to=date(2026, 3, 31),
            max_retries=0, min_interval_s=0.0,
        )  # fmt: skip
        assert first.status == "partial" and first.inserted == 1
        assert any(w.status == "failed" for w in first.coverage)

        healthy = ScriptedSource(pages)
        second = await historical.import_history(
            store, healthy, range_from=date(2026, 3, 1), range_to=date(2026, 3, 31),
            min_interval_s=0.0,
        )  # fmt: skip
        assert second.status == "completed"
        assert len(store.source_records()) == 2


async def test_a_failed_window_is_never_reported_as_empty(tmp_path: Path) -> None:
    class AlwaysFails(ScriptedSource):
        async def fetch(self, *, range_from, range_to, symbols, cursor, limit) -> SourcePage:
            raise ImportSourceError("HTTP 503", retryable=False)

    with ResearchStore(tmp_path / "research.db") as store:
        report = await historical.import_history(
            store, AlwaysFails([]), range_from=date(2026, 1, 1), range_to=date(2026, 3, 31),
            min_interval_s=0.0,
        )  # fmt: skip
    assert report.status == "failed" and report.inserted == 0
    assert report.coverage and report.coverage[0].status == "failed"
    assert any("UNKNOWN" in note for note in report.notes)


async def test_pagination_walks_every_page(tmp_path: Path) -> None:
    pages = [
        (
            [record("INFY", datetime(2026, 3, 2, 10, tzinfo=IST), source_id=f"p{i}")]
            if i < 2
            else [],
            str(i + 1) if i < 2 else None,
        )  # fmt: skip
        for i in range(3)
    ]
    with ResearchStore(tmp_path / "research.db") as store:
        report = await historical.import_history(
            store, ScriptedSource(pages), range_from=date(2026, 3, 1), range_to=date(2026, 3, 31),
            min_interval_s=0.0,
        )  # fmt: skip
    assert report.status == "completed" and report.inserted == 2


async def test_an_unconfigured_http_archive_refuses_rather_than_guessing(tmp_path: Path) -> None:
    source = HttpArchiveSource(url=None)
    with pytest.raises(SourceNotConfiguredError, match="no authorized historical"):
        await source.fetch(range_from=date(2026, 1, 1), range_to=date(2026, 1, 2),
                           symbols=[], cursor=None, limit=10)  # fmt: skip
    with ResearchStore(tmp_path / "research.db") as store:
        report = await historical.import_history(
            store, HttpArchiveSource(url=None),
            range_from=date(2026, 1, 1), range_to=date(2026, 1, 2), min_interval_s=0.0,
        )  # fmt: skip
    assert report.status == "failed" and report.coverage[0].status == "failed"


# -- provenance: the four timestamps and the two modes ---------------------------------------------


async def test_receipt_is_never_backdated_to_publication(tmp_path: Path) -> None:
    old = datetime(2024, 5, 10, 10, 0, tzinfo=IST)
    path = write_export(
        tmp_path / "old.json",
        [{"symbol": "INFY", "event_type": "order_win", "published_at": old.isoformat(),
          "source_id": "OLD1", "title": "An old order win"}],
    )  # fmt: skip
    with ResearchStore(tmp_path / "research.db") as store:
        await historical.import_history(
            store, LocalFileSource(path), range_from=date(2024, 1, 1), range_to=date(2024, 12, 31)
        )
        row = store.source_records()[0]
    assert datetime.fromisoformat(row["published_at_utc"]).year == 2024
    received = datetime.fromisoformat(row["received_at_utc"])
    imported = datetime.fromisoformat(row["imported_at_utc"])
    # The receipt is now, not 2024: importing history must not pretend we saw it live.
    assert received.year >= 2026 and imported.year >= 2026
    assert received > datetime.fromisoformat(row["published_at_utc"])


def test_actual_and_reconstructed_availability_differ_and_are_labelled() -> None:
    published = datetime(2026, 3, 4, 10, 0, tzinfo=IST)
    stamps = timestamps(published, received=published + timedelta(days=400))
    reconstructed = stamps.availability(EvaluationMode.RECONSTRUCTED)
    actual = stamps.availability(EvaluationMode.ACTUAL)
    assert reconstructed == published + timedelta(minutes=5)
    assert actual > reconstructed
    assert actual.year == 2027  # the truth: we only got it 400 days later


def test_mixing_evaluation_modes_is_refused() -> None:
    with pytest.raises(MixedEvaluationModesError, match="cannot pool"):
        check_single_mode({EvaluationMode.ACTUAL, EvaluationMode.RECONSTRUCTED})
    assert check_single_mode({EvaluationMode.ACTUAL}) is EvaluationMode.ACTUAL


def test_classification_of_old_records_is_labelled_retrospective() -> None:
    rows = historical.classify_records(
        [
            {
                "record_id": "r1",
                "symbol": "INFY",
                "source": "local_file",
                "event_type": "order_win",
                "published_at_utc": "2024-05-10T04:30:00+00:00",
                "raw": json.dumps({"title": "wins a contract"}),
            }
        ],
        classified_at=NOW,
    )
    assert len(rows) == 1
    assert rows[0].retrospective is True
    assert rows[0].rules_version == RULES_VERSION
    assert rows[0].direction == "positive"  # order_win is positive by rule
    assert rows[0].materiality is None  # not supplied: never guessed


def test_a_source_supplied_direction_beats_the_rule_table() -> None:
    rows = historical.classify_records(
        [
            {
                "record_id": "r2",
                "event_type": "fundraise",
                "published_at_utc": "2026-03-04T04:30:00+00:00",
                "raw": json.dumps({"direction": "negative", "materiality": "major"}),
            }
        ],
        classified_at=NOW,
    )
    assert rows[0].direction == "negative" and rows[0].materiality == "major"


def test_reclassification_is_idempotent_and_supersedes_on_a_new_version() -> None:
    records = [{"record_id": "r3", "event_type": "order_win",
                "published_at_utc": "2026-03-04T04:30:00+00:00", "raw": "{}"}]  # fmt: skip
    first = historical.classify_records(records, classified_at=NOW)
    again = historical.classify_records(
        records, classified_at=NOW, existing={"r3": first[0].as_row()}
    )
    assert again == []  # same reader, same record: nothing to do
    newer = historical.classify_records(
        records, classified_at=NOW + timedelta(days=1),
        existing={"r3": first[0].as_row()}, rules_version="v2",
    )  # fmt: skip
    assert len(newer) == 1 and newer[0].supersedes == first[0].classification_id


# -- outcomes --------------------------------------------------------------------------------------


def test_an_incomplete_horizon_stays_pending() -> None:
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 3, 31))
    bars = series(days)
    # Published intraday three sessions from the end: a 1-session window still completes, a
    # 20-session one cannot. The incomplete one must stay pending, never zero.
    late = datetime.combine(days[-3], time(10, 0), tzinfo=IST)
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=20,
        timestamps=timestamps(late), mode=EvaluationMode.RECONSTRUCTED,
        series=bars, benchmark=None, computed_at=NOW,
    )  # fmt: skip
    early = event_outcome(
        record_id="r2", instrument_key=KEY, horizon=1,
        timestamps=timestamps(late), mode=EvaluationMode.RECONSTRUCTED,
        series=bars, benchmark=None, computed_at=NOW,
    )  # fmt: skip
    assert early.status == STATUS_SETTLED and early.anchor_date == days[-3]
    assert result.status == STATUS_PENDING
    assert result.abnormal_return is None and result.bucket is None
    assert result.anchor_date == days[-3]


def test_benchmark_endpoints_must_match_exactly() -> None:
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 6, 30))
    bars = series(days)
    # The instrument covers the whole window; the benchmark stops before the outcome date, so the
    # two do not share endpoints and no abnormal return may be claimed.
    truncated = series(days[:-8], key=BENCH)
    late = datetime.combine(days[-10], time(10, 0), tzinfo=IST)
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=5,
        timestamps=timestamps(late),
        mode=EvaluationMode.RECONSTRUCTED, series=bars, benchmark=truncated, computed_at=NOW,
    )  # fmt: skip
    assert result.status == STATUS_NO_BENCHMARK
    assert result.abnormal_return is None
    assert result.instrument_return is not None  # the raw leg is kept, the comparison is refused
    assert any("abnormal return is undefined" in note for note in result.notes)


def test_benchmark_covering_the_same_endpoints_yields_an_abnormal_return() -> None:
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 6, 30))
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=5,
        timestamps=timestamps(datetime(2026, 1, 5, 10, tzinfo=IST)),
        mode=EvaluationMode.RECONSTRUCTED, series=series(days),
        benchmark=series(days, key=BENCH, wave=0.004), computed_at=NOW,
    )  # fmt: skip
    assert result.status == STATUS_SETTLED
    assert result.benchmark_return is not None
    assert result.abnormal_return == pytest.approx(
        result.instrument_return - result.benchmark_return, abs=1e-6
    )


@pytest.mark.parametrize(
    ("hour", "expected_next"),
    [(9, False), (16, True)],
)
def test_an_after_close_event_anchors_to_the_next_session(hour: int, expected_next: bool) -> None:
    days = CAL.trading_days(date(2026, 3, 2), date(2026, 6, 30))
    publish_day = days[5]  # a real session, whatever the calendar says it is
    published = datetime.combine(publish_day, time(hour, 0), tzinfo=IST)
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=1,
        timestamps=timestamps(published), mode=EvaluationMode.RECONSTRUCTED,
        series=series(days), benchmark=None, computed_at=NOW,
    )  # fmt: skip
    assert result.status == STATUS_SETTLED
    assert result.anchor_date == days[6 if expected_next else 5]


def test_a_weekend_event_anchors_to_the_next_session() -> None:
    days = CAL.trading_days(date(2026, 3, 2), date(2026, 6, 30))
    saturday = datetime(2026, 3, 7, 11, 0, tzinfo=IST)
    assert saturday.weekday() == 5
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=1,
        timestamps=timestamps(saturday), mode=EvaluationMode.RECONSTRUCTED,
        series=series(days), benchmark=None, computed_at=NOW,
    )  # fmt: skip
    assert result.status == STATUS_SETTLED
    assert result.anchor_date == date(2026, 3, 9)  # the Monday after
    assert result.anchor_date > saturday.date()


def test_a_fabricated_price_source_is_refused() -> None:
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 6, 30))
    result = event_outcome(
        record_id="r", instrument_key=KEY, horizon=5,
        timestamps=timestamps(datetime(2026, 1, 5, 10, tzinfo=IST)),
        mode=EvaluationMode.RECONSTRUCTED,
        series=series(days, source=MarketDataSource.SYNTHETIC),
        benchmark=None, computed_at=NOW,
    )  # fmt: skip
    assert result.status != STATUS_SETTLED
    assert any("synthetic" in note for note in result.notes)


def test_outcome_counts_separate_settled_from_pending() -> None:
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 3, 31))
    bars = series(days)
    # Six sessions from the end: the 1- and 5-session windows complete, the 10- and 20- do not.
    late = datetime.combine(days[-6], time(10, 0), tzinfo=IST)
    built = [
        event_outcome(
            record_id=f"r{h}",
            instrument_key=KEY,
            horizon=h,
            timestamps=timestamps(late),
            mode=EvaluationMode.RECONSTRUCTED,
            series=bars,
            benchmark=None,
            computed_at=NOW,
        )
        for h in (1, 5, 10, 20)
    ]
    totals = summarize_outcomes(built)
    assert totals[STATUS_SETTLED] == 2
    assert totals[STATUS_PENDING] == 2
    assert sum(totals.values()) == 4


# -- walk-forward -----------------------------------------------------------------------------------


def test_folds_reserve_a_chronological_holdout() -> None:
    origins = [date(2026, 1, d) for d in range(1, 11)]
    folds = plan_folds(origins, embargo_sessions=1, holdout_fraction=0.3)
    assert len(folds) == 10
    assert [f.is_holdout for f in folds].count(True) == 3
    assert all(folds[i].origin < folds[i + 1].origin for i in range(len(folds) - 1))
    assert all(f.train_cutoff < f.origin for f in folds)


def test_training_excludes_outcomes_that_cross_the_origin() -> None:
    fold = plan_folds([date(2026, 6, 1)], embargo_sessions=1)[0]
    before = outcome(record_id="old", horizon=5, anchor=date(2026, 3, 2),
                     end=date(2026, 3, 9), abnormal=0.01, bucket="up")  # fmt: skip
    crossing = outcome(record_id="cross", horizon=5, anchor=date(2026, 5, 20),
                       end=date(2026, 6, 2), abnormal=0.02, bucket="up")  # fmt: skip
    pool, _ = train_pool([before, crossing], fold=fold, horizon=5)
    assert pool.tolist() == pytest.approx([0.01])  # only the fully completed one


def test_overlapping_windows_are_dropped_and_counted() -> None:
    fold = plan_folds([date(2026, 9, 1)], embargo_sessions=1)[0]
    first = outcome(record_id="a", horizon=5, anchor=date(2026, 3, 2),
                    end=date(2026, 3, 9), abnormal=0.01, bucket="up")  # fmt: skip
    overlapping = outcome(record_id="b", horizon=5, anchor=date(2026, 3, 5),
                          end=date(2026, 3, 12), abnormal=0.03, bucket="up")  # fmt: skip
    disjoint = outcome(record_id="c", horizon=5, anchor=date(2026, 4, 1),
                       end=date(2026, 4, 8), abnormal=0.02, bucket="down")  # fmt: skip
    pool, dropped = train_pool([first, overlapping, disjoint], fold=fold, horizon=5)
    assert dropped == 1 and len(pool) == 2


def test_thin_evidence_falls_back_and_says_so() -> None:
    config = WalkForwardConfig(horizons=(5,), min_train_samples=20)
    fold = plan_folds([date(2026, 9, 1)], embargo_sessions=1)[0]
    reference = outcome(record_id="ref", horizon=5, anchor=date(2026, 9, 1),
                        end=date(2026, 9, 8), abnormal=0.01, bucket="up")  # fmt: skip
    thin = np.asarray([0.01, 0.02], dtype=float)
    thick = np.asarray([float(i) / 1000 for i in range(40)], dtype=float)
    publication = publish(
        run_id="r", fold=fold, outcome_ref=reference, requested=Approach.EVENT_CONDITIONED,
        cohort="c", pools={Approach.EVENT_CONDITIONED: (thin, 0),
                           Approach.UNCONDITIONAL: (thick, 0)},  # fmt: skip
        sideways_band=0.02, config=config, published_at=NOW,
    )  # fmt: skip
    assert publication.fallback_used is True
    assert publication.approach == Approach.UNCONDITIONAL.value
    assert publication.requested_approach == Approach.EVENT_CONDITIONED.value
    assert publication.n_train == 40
    assert any("fell back" in note for note in publication.notes)


def test_publications_are_immutable_once_written(tmp_path: Path) -> None:
    config = WalkForwardConfig(horizons=(5,))
    fold = plan_folds([date(2026, 9, 1)], embargo_sessions=1)[0]
    reference = outcome(record_id="ref", horizon=5, anchor=date(2026, 9, 1),
                        end=date(2026, 9, 8), abnormal=0.01, bucket="up")  # fmt: skip
    pool = np.asarray([0.01] * 30, dtype=float)

    def make(p_up: float) -> Publication:
        base = publish(
            run_id="run", fold=fold, outcome_ref=reference,
            requested=Approach.UNCONDITIONAL, cohort="c",
            pools={Approach.UNCONDITIONAL: (pool, 0)}, sideways_band=0.02,
            config=config, published_at=NOW,
        )  # fmt: skip
        return replace(base, p_up=p_up)

    with ResearchStore(tmp_path / "research.db") as store:
        store.save_walkforward([make(0.30).as_row()])
        store.save_walkforward([make(0.99).as_row()])  # a later run must not rewrite history
        rows = store.walkforward(run_id="run")
    assert len(rows) == 1
    assert rows[0]["p_up"] == pytest.approx(0.30)


def test_pooled_cohorts_drop_the_issuer_and_are_separately_named() -> None:
    fields = {"event_type": "order_win", "direction": "positive", "materiality": "major"}
    reference = outcome(record_id="a", horizon=5, anchor=date(2026, 3, 2),
                        end=date(2026, 3, 9), abnormal=0.01, bucket="up")  # fmt: skip
    specific = cohort_of(reference, fields)
    pooled = historical.pooled_cohort_of(reference, fields)
    assert specific.startswith(KEY)
    assert pooled.startswith("POOLED") and KEY not in pooled
    assert Approach.POOLED_EVENT.value == "pooled_event"


# -- metrics and reporting ---------------------------------------------------------------------------


def _publications_and_outcomes(n: int = 60) -> tuple[list[Publication], dict[str, EventOutcome]]:
    config = WalkForwardConfig(horizons=(5,), min_train_samples=5)
    days = CAL.trading_days(date(2026, 1, 2), date(2026, 9, 30))
    publications: list[Publication] = []
    lookup: dict[str, EventOutcome] = {}
    for index in range(n):
        anchor = days[index * 3]
        end = days[min(index * 3 + 5, len(days) - 1)]
        record_id = f"r{index}"
        realized = 0.03 if index % 3 == 0 else -0.03 if index % 3 == 1 else 0.0
        bucket = "up" if realized > 0.02 else "down" if realized < -0.02 else "side"
        lookup[f"{record_id}|5"] = outcome(
            record_id=record_id, horizon=5, anchor=anchor, end=end,
            abnormal=realized, bucket=bucket,
        )  # fmt: skip
        fold = plan_folds([anchor], embargo_sessions=1)[0]
        for approach, p_up in ((Approach.UNCONDITIONAL, 0.34), (Approach.EVENT_CONDITIONED, 0.7)):
            base = publish(
                run_id="run", fold=fold, outcome_ref=lookup[f"{record_id}|5"],
                requested=approach, cohort=f"{KEY}|order_win|positive|major",
                pools={approach: (np.asarray([realized] * 6, dtype=float), 0)},
                sideways_band=0.02, config=config, published_at=NOW,
                min_train_samples=5,
            )  # fmt: skip
            publications.append(replace(base, p_up=p_up, p_side=0.2, p_down=round(0.8 - p_up, 6)))
    return publications, lookup


def test_evaluation_is_deterministic_and_reports_uncertainty() -> None:
    publications, lookup = _publications_and_outcomes()
    rows = {
        key: {"abnormal_return": o.abnormal_return, "outcome_bucket": o.bucket}
        for key, o in lookup.items()
    }
    first = evaluate(publications, rows, run_id="run", evaluation_mode="reconstructed",
                     created_at=NOW, configuration={}, totals={}, horizons=(5,))  # fmt: skip
    second = evaluate(publications, rows, run_id="run", evaluation_mode="reconstructed",
                      created_at=NOW, configuration={}, totals={}, horizons=(5,))  # fmt: skip
    assert first.as_dict() == second.as_dict()
    scored = first.by_approach[Approach.EVENT_CONDITIONED.value]
    assert scored.n == 60 and scored.brier is not None
    assert scored.brier_ci is not None and scored.brier_ci[0] <= scored.brier_ci[1]
    assert scored.interval_coverage is not None
    assert first.comparisons, "a paired comparison against each baseline is expected"
    assert any("moving-block bootstrap" in note for note in first.notes)
    assert any("after-cost" in note for note in first.notes)


def test_a_tiny_sample_is_not_promoted() -> None:
    publications, lookup = _publications_and_outcomes(n=4)
    rows = {
        key: {"abnormal_return": o.abnormal_return, "outcome_bucket": o.bucket}
        for key, o in lookup.items()
    }
    report = evaluate(publications, rows, run_id="run", evaluation_mode="reconstructed",
                      created_at=NOW, configuration={}, totals={}, horizons=(5,))  # fmt: skip
    for metrics in report.by_approach.values():
        assert metrics.brier_ci is None  # no interval on four observations
        assert any("must not be read as a result" in note for note in metrics.notes)
    for comparison in report.comparisons:
        assert comparison.ci is None
        assert "insufficient" in comparison.verdict


def test_pending_outcomes_are_excluded_from_scoring_but_counted() -> None:
    publications, lookup = _publications_and_outcomes(n=30)
    dropped = f"{publications[0].record_id}|{publications[0].horizon_sessions}"
    rows = {
        key: {"abnormal_return": o.abnormal_return, "outcome_bucket": o.bucket}
        for key, o in lookup.items()
        if key != dropped  # this horizon has not settled yet
    }
    report = evaluate(publications, rows, run_id="run", evaluation_mode="reconstructed",
                      created_at=NOW, configuration={}, totals={}, horizons=(5,))  # fmt: skip
    assert report.totals["publications_unscored"] >= 1
    assert report.totals["publications_scored"] == (
        len(publications) - report.totals["publications_unscored"]
    )
    assert any("has not settled" in note for note in report.notes)


def test_the_report_labels_reconstruction_and_the_reaction_study_caveat() -> None:
    publications, lookup = _publications_and_outcomes(n=30)
    rows = {
        key: {"abnormal_return": o.abnormal_return, "outcome_bucket": o.bucket}
        for key, o in lookup.items()
    }
    report = evaluate(publications, rows, run_id="run", evaluation_mode="reconstructed",
                      created_at=NOW, configuration={"fingerprint": "abc"},
                      totals={"records": 30}, horizons=(5,))  # fmt: skip
    markdown = historical.render_markdown(report, limitations=["a stated limitation"])
    assert "Reconstructed history" in markdown
    assert "reaction study" in markdown
    assert "a stated limitation" in markdown
    assert "fallback rate" in markdown
    assert "| cohort" in markdown.lower() or "## Cohorts" in markdown


async def test_coverage_summary_flags_unknown_windows(tmp_path: Path) -> None:
    path = write_export(
        tmp_path / "events.json",
        [{"symbol": "INFY", "event_type": "order_win",
          "published_at": "2026-03-04T10:00:00", "source_id": "A1", "title": "Wins"}],
    )  # fmt: skip
    with ResearchStore(tmp_path / "research.db") as store:
        assert historical.coverage_summary(store)["records"] == 0
        await historical.import_history(
            store,
            LocalFileSource(path),
            range_from=date(2026, 3, 1),
            range_to=date(2026, 3, 31),
            min_interval_s=0.0,
        )

        class Fails(ScriptedSource):
            async def fetch(self, *, range_from, range_to, symbols, cursor, limit) -> SourcePage:
                raise ImportSourceError("boom", retryable=False)

        await historical.import_history(
            store,
            Fails([]),
            range_from=date(2026, 1, 1),
            range_to=date(2026, 2, 28),
            min_interval_s=0.0,
        )
        summary = historical.coverage_summary(store)
    assert summary["records"] == 1
    assert summary["by_event_type"] == {"order_win": 1}
    assert summary["incomplete_imports"], "a failed import must be visible in coverage"
    assert "UNKNOWN" in summary["warning"]
