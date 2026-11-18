"""The historical research pipeline: import, classify, price, outcome, walk-forward, report.

Everything here is offline-capable except the price fetch. The stages are separate pure-ish
functions rather than one monolith, so each can be exercised on fixtures without a network and
without a database:

``classify_records`` -> ``build_outcome_rows`` -> ``walkforward_publications`` ->
``score_publications`` -> ``evaluate`` -> ``render_markdown``

Three rules shape the design:

* **Nothing is backdated.** A record imported today keeps today as its receipt and recording time.
  Only the *evaluation mode* decides what availability is assumed, and ``RECONSTRUCTED`` says so
  on every row and in every report.
* **Retrospective classification is labelled.** Historical records are read by the current rule
  table (and, when a licensed source supplies them, by the source's own direction/materiality).
  The model and rules versions are recorded, ``retrospective`` is set, and the report repeats the
  limitation - because a reading produced years after the fact is not what a participant saw.
* **Incomplete stays incomplete.** An outcome whose horizon has not fully elapsed is ``pending``,
  and a pending publication is never scored.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from src.domain.calendar import NSECalendar
from src.domain.types import AnnouncementType
from src.marketdata.history import DailySeries
from src.research.importing import EventImporter, HistoricalSource, ImportReport
from src.research.mapping import rule_for
from src.research.metrics import EvaluationReport, evaluate
from src.research.outcomes import (
    OUTCOME_HORIZONS,
    EventOutcome,
    PriceProvenance,
    build_outcomes,
    build_provenance,
)
from src.research.provenance import (
    DEFAULT_CLASSIFICATION_LAG,
    DEFAULT_INGESTION_LAG,
    EvaluationMode,
    EventTimestamps,
    check_single_mode,
)
from src.research.store import ResearchStore, _utc
from src.research.walkforward import (
    Approach,
    Publication,
    WalkForwardConfig,
    cohort_of,
    plan_folds,
    pooled_cohort_of,
    publish,
    train_pool,
)

logger = logging.getLogger(__name__)

# Bumped whenever the rule table or the reading policy changes, so a classification can be tied
# to the exact reader that produced it.
RULES_VERSION = "research-mapping-rules-v1"
CLASSIFIER_NAME = "rule_table"
# How far back to ask a provider for daily bars when building a historical dataset.
DEFAULT_LOOKBACK_DAYS = 420
DEFAULT_YFINANCE_PERIOD = "2y"


class HistoricalDataError(RuntimeError):
    """The historical dataset could not be built, and the reason is not "no data"."""


@dataclass(frozen=True, slots=True)
class ClassificationRow:
    record_id: str
    classification_id: str
    classified_at: datetime
    model: str
    rules_version: str
    retrospective: bool
    event_type: str | None
    direction: str | None
    materiality: str | None
    confidence: float | None
    supersedes: str | None
    payload: Mapping[str, Any]

    def as_row(self) -> Mapping[str, Any]:
        return {
            "classification_id": self.classification_id,
            "record_id": self.record_id,
            "classified_at": self.classified_at,
            "model": self.model,
            "prompt_version": None,
            "rules_version": self.rules_version,
            "retrospective": self.retrospective,
            "event_type": self.event_type,
            "direction": self.direction,
            "materiality": self.materiality,
            "confidence": self.confidence,
            "supersedes": self.supersedes,
            "payload": dict(self.payload),
        }


@dataclass(frozen=True, slots=True)
class OutcomeBuild:
    """One pass of outcome construction, with the provenance that makes it auditable."""

    mode: EvaluationMode
    provenance: tuple[PriceProvenance, ...]
    outcomes: tuple[EventOutcome, ...]
    totals: Mapping[str, int]
    notes: tuple[str, ...]


def _parse_moment(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def classify_records(
    records: Sequence[Mapping[str, Any]],
    *,
    classified_at: datetime,
    existing: Mapping[str, Mapping[str, Any]] | None = None,
    rules_version: str = RULES_VERSION,
) -> list[ClassificationRow]:
    """Read imported records with the current rule table, versioned and flagged retrospective.

    A direction or materiality supplied by the licensed source wins over the rule table, and the
    row records which one decided. Records already classified by the same rules version are
    skipped, so re-running is idempotent; a new rules version writes a *new* row that supersedes
    the old one rather than overwriting it.
    """
    prior = existing or {}
    out: list[ClassificationRow] = []
    for record in records:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            continue
        previous = prior.get(record_id)
        if previous is not None and str(previous.get("rules_version") or "") == rules_version:
            continue  # already read by this exact reader
        raw = record.get("raw")
        fields: dict[str, Any] = {}
        if isinstance(raw, str):
            try:
                fields = json.loads(raw)
            except ValueError:
                fields = {}
        elif isinstance(raw, Mapping):
            fields = dict(raw)

        event_type_text = record.get("event_type") or fields.get("event_type")
        event_type: AnnouncementType | None = None
        if isinstance(event_type_text, str) and event_type_text:
            try:
                event_type = AnnouncementType(event_type_text.strip().lower())
            except ValueError:
                event_type = None

        direction: str | None = None
        materiality: str | None = None
        basis = "none"
        confidence: float | None = None
        source_direction = fields.get("direction")
        if isinstance(source_direction, str) and source_direction.strip():
            direction = source_direction.strip().lower()
            basis = "source"
            confidence = 1.0
        source_materiality = fields.get("materiality")
        if isinstance(source_materiality, str) and source_materiality.strip():
            materiality = source_materiality.strip().lower()

        if event_type is not None:
            _channel, rule_direction, _spillover = rule_for(event_type)
            if direction is None and rule_direction is not None:
                direction = rule_direction.value
                basis = "rule_table"
                confidence = 0.6  # the type alone: a prior, not a reading of the text
            elif direction is None:
                basis = "rule_table_undetermined"
        published = _parse_moment(record.get("published_at_utc"))
        retrospective = published is None or published < classified_at
        classification_id = hashlib.sha256(
            f"{record_id}|{rules_version}|{_utc(classified_at)}".encode()
        ).hexdigest()[:24]
        out.append(
            ClassificationRow(
                record_id=record_id,
                classification_id=classification_id,
                classified_at=classified_at,
                model=CLASSIFIER_NAME,
                rules_version=rules_version,
                retrospective=retrospective,
                event_type=event_type.value if event_type is not None else None,
                direction=direction,
                materiality=materiality,
                confidence=confidence,
                supersedes=str(previous["classification_id"]) if previous else None,
                payload={
                    "basis": basis,
                    "source": record.get("source"),
                    "symbol": record.get("symbol"),
                    "retrospective": retrospective,
                },
            )
        )
    return out


def timestamps_for(
    record: Mapping[str, Any],
    classification: Mapping[str, Any] | None,
    *,
    mode: EvaluationMode,
) -> EventTimestamps | None:
    """The four timestamps of one imported record.

    ``received_at`` and ``recorded_at`` are the *import* times, never the publication time. In
    ``RECONSTRUCTED`` mode the earlier availability is an assumption applied later, at
    :meth:`EventTimestamps.availability`, not by rewriting these fields.
    """
    published = _parse_moment(record.get("published_at_utc"))
    received = _parse_moment(record.get("received_at_utc")) or _parse_moment(
        record.get("imported_at_utc")
    )
    recorded = _parse_moment(record.get("imported_at_utc")) or received
    if published is None or received is None or recorded is None:
        return None
    if recorded < received:
        recorded = received
    classified = _parse_moment(classification.get("classified_at_utc")) if classification else None
    return EventTimestamps(
        published_at=published,
        received_at=received,
        classified_at=classified,
        recorded_at=recorded,
    )


def build_outcome_rows(
    records: Sequence[Mapping[str, Any]],
    classifications: Mapping[str, Mapping[str, Any]],
    series: Mapping[str, DailySeries],
    *,
    benchmark: DailySeries | None,
    instrument_of: Mapping[str, str],
    mode: EvaluationMode,
    computed_at: datetime,
    horizons: Sequence[int] = OUTCOME_HORIZONS,
    ingestion_lag: timedelta = DEFAULT_INGESTION_LAG,
    classification_lag: timedelta = DEFAULT_CLASSIFICATION_LAG,
) -> tuple[list[EventOutcome], list[str]]:
    """Outcomes for every imported record, plus notes about what could not be measured."""
    notes: list[str] = []
    enriched: list[dict[str, Any]] = []
    timestamps_by_record: dict[str, EventTimestamps] = {}
    unmapped: list[str] = []
    for record in records:
        record_id = str(record.get("record_id") or "")
        if not record_id:
            continue
        symbol = str(record.get("symbol") or "").upper()
        instrument_key = instrument_of.get(symbol)
        if instrument_key is None:
            unmapped.append(symbol)
            continue
        stamps = timestamps_for(record, classifications.get(record_id), mode=mode)
        if stamps is None:
            notes.append(f"{record_id}: missing a timestamp, so availability cannot be established")
            continue
        timestamps_by_record[record_id] = stamps
        enriched.append({**dict(record), "instrument_key": instrument_key})
    if unmapped:
        unique = sorted(set(unmapped))
        notes.append(
            f"{len(unmapped)} record(s) for {len(unique)} symbol(s) have no instrument mapping and "
            f"were excluded: {', '.join(unique[:12])}"
            + (f" and {len(unique) - 12} more" if len(unique) > 12 else "")
        )
    outcomes = build_outcomes(
        enriched,
        series,
        benchmark=benchmark,
        computed_at=computed_at,
        mode=mode,
        horizons=horizons,
        timestamps_by_record=timestamps_by_record,
        ingestion_lag=ingestion_lag,
        classification_lag=classification_lag,
    )
    return outcomes, notes


def _cohort_resolver(
    fields_by_record: Mapping[str, Mapping[str, str | None]], *, pooled: bool
) -> Callable[[EventOutcome], str]:
    """A cohort function bound to the classification fields, pooled or issuer-specific."""
    choose = pooled_cohort_of if pooled else cohort_of

    def resolve(outcome: EventOutcome) -> str:
        return choose(outcome, fields_by_record.get(outcome.record_id, {}))

    return resolve


def walkforward_publications(
    outcomes: Sequence[EventOutcome],
    *,
    run_id: str,
    config: WalkForwardConfig,
    published_at: datetime,
    fields_by_record: Mapping[str, Mapping[str, str | None]],
    technical_states: Mapping[str, Mapping[date, tuple[str, str]]],
    sideways_bands: Mapping[tuple[str, int], float | None],
) -> list[Publication]:
    """Freeze one publication per (settled outcome, approach), at that outcome's own origin.

    The origin is the event's anchor date - the first close after the information was available -
    so training is restricted to outcomes that completed strictly before it, minus the embargo.
    """
    settled = [o for o in outcomes if o.is_settled and o.anchor_date is not None]
    if not settled:
        return []
    modes = {o.evaluation_mode for o in settled}
    check_single_mode(modes)  # never pool actual with reconstructed
    origins = sorted({o.anchor_date for o in settled if o.anchor_date is not None})
    folds = {f.origin: f for f in plan_folds(origins, embargo_sessions=config.embargo_sessions,
                                             holdout_fraction=config.holdout_fraction)}  # fmt: skip

    by_instrument: dict[str, list[EventOutcome]] = defaultdict(list)
    for outcome in settled:
        by_instrument[outcome.instrument_key].append(outcome)

    approaches: list[Approach] = [
        Approach.UNCONDITIONAL,
        Approach.TECHNICAL,
        Approach.EVENT_CONDITIONED,
    ]
    if config.allow_pooling:
        approaches.append(Approach.POOLED_EVENT)

    publications: list[Publication] = []
    for outcome in sorted(settled, key=lambda o: (o.anchor_date or date.min, o.record_id)):
        assert outcome.anchor_date is not None
        fold = folds.get(outcome.anchor_date)
        if fold is None:
            continue
        fields = fields_by_record.get(outcome.record_id, {})
        cohort = cohort_of(outcome, fields)
        state = technical_states.get(outcome.instrument_key, {}).get(outcome.anchor_date)
        band = sideways_bands.get((outcome.instrument_key, outcome.horizon_sessions))
        instrument_pool = by_instrument[outcome.instrument_key]
        for approach in approaches:
            if approach is Approach.UNCONDITIONAL:
                pool, dropped = train_pool(
                    instrument_pool, fold=fold, horizon=outcome.horizon_sessions
                )
            elif approach is Approach.TECHNICAL:
                pool, dropped = train_pool(
                    instrument_pool,
                    fold=fold,
                    horizon=outcome.horizon_sessions,
                    technical_state=technical_states.get(outcome.instrument_key, {}),
                    origin_state=state,
                )
            else:
                pooled = approach is Approach.POOLED_EVENT
                scope = settled if pooled else instrument_pool
                pool, dropped = train_pool(
                    scope,
                    fold=fold,
                    horizon=outcome.horizon_sessions,
                    cohort=(pooled_cohort_of(outcome, fields) if pooled else cohort),
                    cohort_of_outcome=_cohort_resolver(fields_by_record, pooled=pooled),
                )
            publications.append(
                publish(
                    run_id=run_id,
                    fold=fold,
                    outcome_ref=outcome,
                    requested=approach,
                    cohort=cohort,
                    pools={approach: (pool, dropped)},
                    sideways_band=band,
                    config=config,
                    published_at=published_at,
                )
            )
    return publications


def score_publications(
    publications: Iterable[Publication], outcomes_by_key: Mapping[str, EventOutcome]
) -> tuple[list[Publication], list[Publication]]:
    """Split publications into those whose horizon has settled and those still pending."""
    scored: list[Publication] = []
    pending: list[Publication] = []
    for publication in publications:
        key = f"{publication.record_id}|{publication.horizon_sessions}"
        outcome = outcomes_by_key.get(key)
        if outcome is not None and outcome.is_settled:
            scored.append(publication)
        else:
            pending.append(publication)
    return scored, pending


def outcome_lookup(outcomes: Iterable[EventOutcome]) -> dict[str, EventOutcome]:
    return {f"{o.record_id}|{o.horizon_sessions}": o for o in outcomes}


def evaluate_publications(
    publications: Sequence[Publication],
    outcomes_by_key: Mapping[str, EventOutcome],
    *,
    run_id: str,
    mode: EvaluationMode,
    created_at: datetime,
    config: WalkForwardConfig,
    totals: Mapping[str, int],
) -> EvaluationReport:
    rows: dict[str, Mapping[str, Any]] = {}
    for key, outcome in outcomes_by_key.items():
        if outcome.is_settled:
            rows[key] = {
                "abnormal_return": outcome.abnormal_return,
                "outcome_bucket": outcome.bucket,
            }
    return evaluate(
        publications,
        rows,
        run_id=run_id,
        evaluation_mode=mode.value,
        created_at=created_at,
        configuration=config.as_dict(),
        totals=dict(totals),
        horizons=config.horizons,
    )


def provenance_rows(
    series: Mapping[str, DailySeries],
    *,
    provider: str,
    retrieved_at: datetime,
    calendar: NSECalendar,
    benchmark: DailySeries | None,
    benchmark_key: str | None,
    requested_from: date | None,
    requested_to: date | None,
    universe_is_current: bool,
) -> list[PriceProvenance]:
    return [
        build_provenance(
            item,
            provider=provider,
            retrieved_at=retrieved_at,
            calendar=calendar,
            requested_from=requested_from,
            requested_to=requested_to,
            benchmark=benchmark,
            benchmark_key=benchmark_key,
            universe_is_current=universe_is_current,
        )
        for item in series.values()
    ]


async def import_history(
    store: ResearchStore,
    source: HistoricalSource,
    *,
    range_from: date,
    range_to: date,
    symbols: Sequence[str] = (),
    resume: bool = True,
    chunk_days: int = 0,
    min_interval_s: float = 1.0,
    max_retries: int = 3,
) -> ImportReport:
    """Run one import. Retries and pacing are bounded; a failure is reported, never smoothed over."""
    importer = EventImporter(
        store,
        source,
        max_retries=max_retries,
        min_interval_s=min_interval_s,
        chunk_days=chunk_days,
    )
    try:
        return await importer.run(
            range_from=range_from, range_to=range_to, symbols=symbols, resume=resume
        )
    finally:
        await source.aclose()


def coverage_summary(store: ResearchStore, *, limit: int = 200) -> dict[str, Any]:
    """What has been imported, by source and symbol, and which windows remain unknown."""
    records = store.source_records(limit=limit)
    by_source: Counter[str] = Counter()
    by_symbol: Counter[str] = Counter()
    by_type: Counter[str] = Counter()
    newest: dict[str, str] = {}
    oldest: dict[str, str] = {}
    for row in records:
        source = str(row.get("source") or "")
        by_source[source] += 1
        by_symbol[str(row.get("symbol") or "")] += 1
        by_type[str(row.get("event_type") or "unclassified")] += 1
        published = str(row.get("published_at_utc") or "")
        if published:
            newest[source] = max(newest.get(source, ""), published)
            oldest[source] = min(oldest.get(source, published), published)
    imports = store.imports(limit=limit)
    incomplete = [
        {
            "import_id": i.get("import_id"),
            "source": i.get("source"),
            "status": i.get("status"),
            "range": f"{i.get('range_from')}..{i.get('range_to')}",
            "notes": i.get("notes"),
        }
        for i in imports
        if str(i.get("status")) != "completed"
    ]
    return {
        "records": len(records),
        "by_source": dict(by_source),
        "by_symbol": dict(sorted(by_symbol.items())),
        "by_event_type": dict(sorted(by_type.items())),
        "oldest_published": oldest,
        "newest_published": newest,
        "imports": len(imports),
        "incomplete_imports": incomplete,
        "warning": (
            "windows marked failed or incomplete are UNKNOWN, not empty: a missing response was "
            "never converted into 'there were no events'"
            if incomplete
            else ""
        ),
    }


def summarize(publications: Sequence[Publication]) -> dict[str, Any]:
    """Counts for a report header: publications, fallbacks, cohorts and horizons."""
    return {
        "publications": len(publications),
        "by_approach_requested": dict(Counter(p.requested_approach for p in publications)),
        "by_approach_used": dict(Counter(p.approach for p in publications)),
        "fallback_publications": sum(1 for p in publications if p.fallback_used),
        "holdout_publications": sum(1 for p in publications if p.is_holdout),
        "distinct_cohorts": len({p.cohort for p in publications}),
        "by_horizon": dict(Counter(p.horizon_sessions for p in publications)),
    }


def render_markdown(
    report: EvaluationReport,
    *,
    import_report: ImportReport | None = None,
    outcome_totals: Mapping[str, int] | None = None,
    provenance: Sequence[PriceProvenance] = (),
    limitations: Sequence[str] = (),
) -> str:
    """A Markdown report that states its assumptions before its numbers."""
    lines = [
        "# Historical research evaluation",
        "",
        f"- run: `{report.run_id}`",
        f"- created: {report.created_at.isoformat()}",
        f"- **evaluation mode: {report.evaluation_mode}**",
        f"- configuration fingerprint: `{report.configuration.get('fingerprint', '')}`",
        f"- horizons: {', '.join(str(h) for h in (report.configuration.get('horizons') or []))}",
        "",
    ]
    if report.evaluation_mode == EvaluationMode.RECONSTRUCTED.value:
        lines += [
            "> **Reconstructed history.** Availability is publication time plus assumed ingestion",
            "> and classification lags. These records were imported after the fact; the system did",
            "> not observe them live. Classifications are retrospective and versioned. These",
            "> figures must not be compared with forward-recorded calibration.",
            "",
        ]
    if import_report is not None:
        lines += [
            "## Import",
            "",
            f"- source: `{import_report.source}`  status: **{import_report.status}**",
            f"- range: {import_report.range_from} .. {import_report.range_to}",
            f"- symbols: {', '.join(import_report.symbols) or 'all'}",
            f"- fetched {import_report.fetched}, inserted {import_report.inserted}, "
            f"duplicates {import_report.duplicates}, rejected {import_report.rejected}, "
            f"failed windows {import_report.failures}",
            "",
            "| window | status | records | note |",
            "| --- | --- | --- | --- |",
        ]
        lines += [
            f"| {w.range_from} .. {w.range_to} | {w.status} | {w.records} | {w.note or ''} |"
            for w in import_report.coverage
        ]
        lines.append("")
        if import_report.status != "completed":
            lines += [
                "**Coverage is incomplete.** Windows marked `failed` or `incomplete` are unknown,",
                "not empty. Any statistic below is conditioned on the data that did arrive.",
                "",
            ]
    if provenance:
        lines += [
            "## Price provenance",
            "",
            "| instrument | provider | retrieved | available | sessions | missing | benchmark |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for item in provenance[:60]:
            lines.append(
                f"| `{item.instrument_key}` | {item.provider} | "
                f"{item.retrieved_at.date().isoformat()} | "
                f"{item.available_from} .. {item.available_to} | {item.sessions} | "
                f"{len(item.missing_sessions)} | {item.benchmark_sessions if item.benchmark_sessions is not None else 'none'} |"
            )
        if len(provenance) > 60:
            lines.append(f"| … | | | | | {len(provenance) - 60} more instruments | |")
        lines.append("")
        flagged = [n for item in provenance for n in item.notes]
        if flagged:
            lines += ["Provenance notes:", ""] + [f"- {n}" for n in dict.fromkeys(flagged)] + [""]
    if outcome_totals:
        lines += [
            "## Event outcomes",
            "",
            "| status | count |",
            "| --- | --- |",
        ]
        lines += [f"| {status} | {count} |" for status, count in sorted(outcome_totals.items())]
        lines += [
            "",
            "`pending` means the horizon has not fully elapsed; those observations are excluded",
            "from every statistic rather than counted as zero.",
            "",
        ]
    lines += [
        "## Totals",
        "",
        "| measure | count |",
        "| --- | --- |",
    ]
    lines += [f"| {k} | {v} |" for k, v in sorted(report.totals.items())]
    lines += ["", f"- event-model fallback rate: {report.fallback_rate}", ""]

    lines += ["## Forecast quality by approach", ""]
    for approach, metrics in sorted(report.by_approach.items()):
        lines += [
            f"### {approach}",
            "",
            f"- observations: **{metrics.n}**",
            f"- multiclass Brier: {metrics.brier}"
            + (
                f" (95% block-bootstrap CI {metrics.brier_ci[0]} … {metrics.brier_ci[1]})"
                if metrics.brier_ci
                else " (no interval: insufficient observations)"
            ),
            f"- q10–q90 interval coverage: {metrics.interval_coverage}"
            f", mean width: {metrics.interval_width}",
            f"- outcome shares: up {metrics.share_up}, sideways {metrics.share_side},"
            f" down {metrics.share_down}",
            f"- mean abnormal return: {metrics.mean_abnormal_return}"
            + (
                f" (CI {metrics.mean_abnormal_ci[0]} … {metrics.mean_abnormal_ci[1]})"
                if metrics.mean_abnormal_ci
                else " (no interval)"
            ),
            f"- median abnormal return: {metrics.median_abnormal_return}",
            f"- fallback publications: {metrics.fallback_count}",
            "",
        ]
        if metrics.reliability:
            lines += [
                "| predicted p(up) | n | predicted | realized |",
                "| --- | --- | --- | --- |",
            ]
            lines += [
                f"| {c.bucket} | {c.n} | "
                f"{'too few' if c.predicted is None else c.predicted} | "
                f"{'too few' if c.realized is None else c.realized} |"
                for c in metrics.reliability
            ]
            lines.append("")
        for note in metrics.notes:
            lines.append(f"> {note}")
        lines.append("")

    if report.comparisons:
        lines += [
            "## Paired comparisons (Brier loss, negative = better)",
            "",
            "| approach | baseline | n paired | mean diff | 95% CI | verdict |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for comparison in report.comparisons:
            interval = (
                f"{comparison.ci[0]} … {comparison.ci[1]}" if comparison.ci else "insufficient"
            )
            lines.append(
                f"| {comparison.approach} | {comparison.baseline} | {comparison.n_paired} | "
                f"{comparison.mean_loss_difference} | {interval} | {comparison.verdict} |"
            )
        lines.append("")

    lines += ["## Cohorts", ""]
    if report.cohort_sizes:
        lines += [
            "| cohort (issuer\\|type\\|direction\\|materiality) | publications |",
            "| --- | --- |",
        ]
        lines += [f"| `{cohort}` | {n} |" for cohort, n in sorted(report.cohort_sizes.items())]
    else:
        lines.append("No cohorts: nothing was published.")
    lines.append("")

    lines += ["## Assumptions and limitations", ""]
    lines += [f"- {note}" for note in report.notes]
    lines += [f"- {note}" for note in limitations]
    lines += [
        "- Close-to-close abnormal returns are a **reaction study**, not trade returns: no entry"
        " policy, fills, slippage or latency is modelled here.",
        "",
    ]
    return "\n".join(lines)
