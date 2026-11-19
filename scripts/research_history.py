"""
Historical event research: import, coverage, outcomes, walk-forward evaluation and reporting.

Subcommands:

    import      Import historical corporate announcements from a local file or a configured,
                authorized HTTP archive. Resumable, rate-limited, deduplicated, and it never
                turns a failed window into "there were no events".
    sync-rss    Sync the announcements the live NSE RSS collector stored in the event store into
                the research archive, with their real receipt times and coverage gaps. Repeatable
                and idempotent; the archive grows prospectively from the first collection.
    coverage    Show what has been imported, by source, symbol and event type, and which windows
                are still unknown.
    outcomes    Classify imported records with the versioned rule table, fetch price history with
                provenance, and compute 1/5/10/20-session outcomes. Incomplete horizons stay
                pending.
    walkforward Publish frozen forecasts at each event origin for three approaches, then score
                them against outcomes that completed after the origin.
    report      Render the persisted evaluation as Markdown and JSON.

Research and offline evaluation only: no broker credentials are used, no order is placed, and
nothing here touches the live trading path.

    uv run python scripts/research_history.py import --file var/research/events.json \\
        --from 2024-01-01 --to 2024-12-31 --symbol INFY --symbol TCS
    uv run python scripts/research_history.py sync-rss
    uv run python scripts/research_history.py coverage
    uv run python scripts/research_history.py outcomes --mode reconstructed
    uv run python scripts/research_history.py walkforward --run-id demo-1 --mode reconstructed
    uv run python scripts/research_history.py report --run-id demo-1 --markdown var/reports/wf.md

The repository ships **no** authorized historical NSE announcement endpoint. NSE offers no free
date-range API for corporate announcements; ``nsearchives.nseindia.com`` serves individual
documents only, and the filings page is driven by undocumented internal endpoints that OD-10
forbids scraping. So ``--source local_file`` (an export you are licensed to hold) is the supported
backfill path, ``--source http`` requires you to supply your own authorized ``--url``, and
``sync-rss`` grows the archive prospectively from what the live collector observed (the RSS feed
is a rolling window, never a historical source).
"""

import argparse
import asyncio
import json
import sys
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.config import get_settings
from src.config.settings import Settings
from src.domain.calendar import get_calendar
from src.domain.events import AnnouncementCoverageGap, AnnouncementReceived
from src.domain.types import Instrument
from src.engine.market import INDEX_KEY, INDEX_TICKER, build_history_source
from src.marketdata.history import DailySeries
from src.ops.exit_codes import ExitCode
from src.ops.process import run_entry_point
from src.reference.instruments import fetch_master
from src.reference.refresh import refresh_reference
from src.research import historical
from src.research.evaluation import CalibrationCell
from src.research.forecast import technical_state_by_date
from src.research.historical import (
    DEFAULT_LOOKBACK_DAYS,
    DEFAULT_YFINANCE_PERIOD,
    RULES_VERSION,
)
from src.research.importing import (
    HttpArchiveSource,
    ImportSourceError,
    LocalFileSource,
    SourceNotConfiguredError,
)
from src.research.metrics import EvaluationReport, Metrics, PairedComparison
from src.research.outcomes import (
    OUTCOME_HORIZONS,
    EventOutcome,
    sideways_band,
    summarize_outcomes,
)
from src.research.provenance import EvaluationMode
from src.research.rss_sync import EventStoreRssSource, actual_mode_records, gaps_from_events
from src.research.store import ResearchStore
from src.research.walkforward import WalkForwardConfig
from src.store.event_store import EventStore
from src.utils.market_time import now_ist


def _date(text: str) -> date:
    return date.fromisoformat(text)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RakshaQuant historical event research (offline; never places an order)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    imp = sub.add_parser("import", help="import historical announcements")
    imp.add_argument("--source", choices=["local_file", "http"], default="local_file")
    imp.add_argument("--file", type=Path, default=None, help="local JSON or CSV export")
    imp.add_argument("--from", dest="range_from", type=_date, required=True)
    imp.add_argument("--to", dest="range_to", type=_date, required=True)
    imp.add_argument("--symbol", action="append", default=[], help="repeatable; default all")
    imp.add_argument("--chunk-days", type=int, default=0, help="split the range into windows")
    imp.add_argument("--min-interval", type=float, default=1.0, help="seconds between requests")
    imp.add_argument("--max-retries", type=int, default=3)
    imp.add_argument("--no-resume", action="store_true", help="start fresh instead of resuming")
    imp.add_argument("--dry-run", action="store_true", help="read the source, write nothing")
    imp.add_argument("--url", default=None, help="http source: your own authorized archive URL")
    imp.add_argument("--records-path", default="records")
    imp.add_argument("--next-cursor-path", default=None)
    imp.add_argument("--param", action="append", default=[], metavar="KEY=VALUE")
    imp.add_argument("--header", action="append", default=[], metavar="NAME:VALUE")
    imp.add_argument("--field-map", default=None, help='JSON, e.g. {"symbol":"sym"}')

    cov = sub.add_parser("coverage", help="show imported coverage and unknown windows")
    cov.add_argument("--json", action="store_true", help="emit machine-readable JSON")

    syn = sub.add_parser(
        "sync-rss",
        help="sync the live RSS announcement collection (event store) into the research archive",
    )
    syn.add_argument("--from", dest="range_from", type=_date, default=None,
                     help="default: the earliest stored announcement")  # fmt: skip
    syn.add_argument("--to", dest="range_to", type=_date, default=None, help="default: today")
    syn.add_argument("--symbol", action="append", default=[], help="repeatable; default all")
    syn.add_argument("--dry-run", action="store_true", help="read the event store, write nothing")

    out = sub.add_parser("outcomes", help="classify, price and compute event outcomes")
    out.add_argument("--mode", choices=[m.value for m in EvaluationMode], default="reconstructed")
    out.add_argument("--horizons", default=",".join(str(h) for h in OUTCOME_HORIZONS))
    out.add_argument("--from", dest="range_from", type=_date, default=None)
    out.add_argument("--to", dest="range_to", type=_date, default=None)
    out.add_argument("--symbol", action="append", default=[])
    out.add_argument("--ingestion-lag-min", type=float, default=5.0)
    out.add_argument("--classification-lag-min", type=float, default=2.0)
    out.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS)
    out.add_argument("--period", default=DEFAULT_YFINANCE_PERIOD, help="YFinance period, e.g. 5y")
    out.add_argument("--dry-run", action="store_true")

    wf = sub.add_parser("walkforward", help="publish and score frozen walk-forward forecasts")
    wf.add_argument("--run-id", required=True)
    wf.add_argument("--mode", choices=[m.value for m in EvaluationMode], default="reconstructed")
    wf.add_argument("--horizons", default=",".join(str(h) for h in OUTCOME_HORIZONS))
    wf.add_argument("--min-train", type=int, default=20)
    wf.add_argument("--embargo", type=int, default=1, help="sessions purged before each origin")
    wf.add_argument("--holdout", type=float, default=0.25, help="final fraction kept out of tuning")
    wf.add_argument("--allow-pooling", action="store_true",
                    help="also publish the separately named pooled_event approach")  # fmt: skip
    wf.add_argument("--dry-run", action="store_true")

    rep = sub.add_parser("report", help="render a persisted evaluation")
    rep.add_argument("--run-id", default=None)
    rep.add_argument("--markdown", type=Path, default=None)
    rep.add_argument("--json", type=Path, default=None)
    return parser.parse_args(argv)


def _horizons(text: str) -> tuple[int, ...]:
    wanted = tuple(int(part) for part in text.split(",") if part.strip())
    invalid = [h for h in wanted if h not in OUTCOME_HORIZONS]
    if invalid:
        raise SystemExit(f"--horizons must come from {OUTCOME_HORIZONS}; got {invalid}")
    return wanted or OUTCOME_HORIZONS


def _build_source(args: argparse.Namespace) -> Any:
    if args.source == "local_file":
        if args.file is None:
            raise SystemExit("--source local_file needs --file")
        return LocalFileSource(args.file)
    params = dict(pair.split("=", 1) for pair in args.param)
    headers = dict(pair.split(":", 1) for pair in args.header)
    field_map = json.loads(args.field_map) if args.field_map else None
    return HttpArchiveSource(
        url=args.url,
        params=params,
        headers={k.strip(): v.strip() for k, v in headers.items()},
        records_path=args.records_path,
        next_cursor_path=args.next_cursor_path,
        field_map=field_map,
    )


async def _resolve_instruments(
    settings: Settings, symbols: list[str], day: date
) -> tuple[dict[str, str], dict[str, Instrument], list[str]]:
    """``symbol -> instrument_key`` from today's universe and the full scrip master.

    The master matters: a historical announcement for a symbol that has since left the index still
    needs an instrument key. Symbols in neither are reported, not silently dropped.
    """
    notes: list[str] = []
    reference = await refresh_reference(
        settings.reference_dir,
        day,
        universe_file=settings.universe_file,
        universe_sectors=settings.universe_sectors,
    )
    universe = {i.symbol: i for i in reference.instruments.by_symbol.values()}
    notes.extend(f"{key}: {message}" for key, message in sorted(reference.problems.items()))
    master, _snapshot = await fetch_master(settings.reference_dir, day)
    by_symbol: dict[str, str] = {}
    instruments: dict[str, Instrument] = {}
    for symbol, instrument in universe.items():
        by_symbol[symbol] = instrument.key
        instruments[instrument.key] = instrument
    for (symbol, series), row in master.items():
        if series != "EQ" or symbol in by_symbol:
            continue
        instrument = Instrument.nse_equity(
            symbol, series=series, name=row.name, tick_size=row.tick_size,
            lot_size=row.lot_size, broker_tokens={"dhan": row.security_id},
        )  # fmt: skip
        by_symbol[symbol] = instrument.key
        instruments[instrument.key] = instrument
    if symbols:
        missing = sorted({s.upper() for s in symbols} - set(by_symbol))
        if missing:
            notes.append(
                f"{len(missing)} requested symbol(s) have no instrument mapping in the universe or "
                f"the scrip master and are excluded: {', '.join(missing[:12])}"
            )
    return by_symbol, instruments, notes


async def _fetch_prices(
    settings: Settings,
    instruments: Mapping[str, Instrument],
    *,
    wanted: list[str],
    by_symbol: Mapping[str, str],
    lookback_days: int,
    period: str,
    settled_before: date,
) -> tuple[dict[str, DailySeries], DailySeries | None, list[str]]:
    keys = [by_symbol[s.upper()] for s in wanted if s.upper() in by_symbol]
    targets = [instruments[k] for k in dict.fromkeys(keys)]
    notes: list[str] = []
    history = build_history_source(settings, lookback_days=lookback_days, period=period)
    result = await history.fetch(
        targets, settled_before=settled_before, expected_last=None,
        extra={INDEX_KEY: INDEX_TICKER},
    )  # fmt: skip
    for key, reason in sorted(result.failed.items()):
        notes.append(f"{key}: no daily history ({reason})")
    for key, last in sorted(result.lagging.items()):
        notes.append(f"{key}: history ends {last.isoformat()}")
    if result.cached:
        notes.append(
            f"{len(result.cached)} instrument(s) reused cached daily candles "
            f"(oldest fetched {min(result.cached.values())})"
        )
    unadjusted = {k: v for k, v in result.adjustments.items() if v.startswith("unavailable")}
    if unadjusted:
        sample = sorted(unadjusted)[0]
        notes.append(
            f"{len(unadjusted)} instrument(s) have no corporate-action adjustment ({sample}: "
            f"{unadjusted[sample]}); their indicators run on raw closes"
        )
    if not targets:
        notes.append("no instruments to price: no requested symbol has an instrument mapping")
    return dict(result.series), result.series.get(INDEX_KEY), notes


async def _cmd_import(args: argparse.Namespace, store: ResearchStore) -> int:
    source = _build_source(args)
    if args.dry_run:
        page = await source.fetch(
            range_from=args.range_from, range_to=args.range_to, symbols=args.symbol,
            cursor=None, limit=100000,
        )  # fmt: skip
        await source.aclose()
        print(f"[dry-run] {source.name} would yield {len(page.records)} record(s)")
        for record in page.records[:20]:
            print(f"  {record.published_at.date()} {record.symbol:<12} "
                  f"{record.event_type or '-':<24} {record.text()[:60]}")  # fmt: skip
        if len(page.records) > 20:
            print(f"  ... and {len(page.records) - 20} more")
        return ExitCode.OK
    try:
        report = await historical.import_history(
            store, source,
            range_from=args.range_from, range_to=args.range_to, symbols=args.symbol,
            resume=not args.no_resume, chunk_days=args.chunk_days,
            min_interval_s=args.min_interval, max_retries=args.max_retries,
        )  # fmt: skip
    except SourceNotConfiguredError as exc:
        print(f"[ERROR] {exc}")
        return ExitCode.CONFIG_ERROR
    except ImportSourceError as exc:
        print(f"[ERROR] import failed: {exc}")
        return ExitCode.CRASH
    print(f"import {report.import_id} -> {report.status}")
    print(f"  fetched={report.fetched} inserted={report.inserted} "
          f"duplicates={report.duplicates} rejected={report.rejected} "
          f"failed_windows={report.failures}")  # fmt: skip
    for window in report.coverage:
        print(f"  {window.range_from}..{window.range_to} {window.status:<10} "
              f"records={window.records} {window.note}")  # fmt: skip
    for note in report.notes:
        print(f"  ! {note}")
    return ExitCode.OK if report.status == "completed" else ExitCode.CRASH


def _cmd_coverage(args: argparse.Namespace, store: ResearchStore) -> int:
    summary = historical.coverage_summary(store)
    if args.json:
        print(json.dumps(summary, indent=2, default=str))
        return ExitCode.OK
    print(f"imported records: {summary['records']}   import runs: {summary['imports']}")
    print(f"by source     : {summary['by_source']}")
    print(f"by event type : {summary['by_event_type']}")
    print(f"by capture    : {summary['by_capture_kind']}")
    print(f"published from: {summary['oldest_published']}")
    print(f"published to  : {summary['newest_published']}")
    print(f"last received : {summary['last_received']}")
    print(f"duplicates skipped across imports: {summary['duplicates']}")
    symbols = summary["by_symbol"]
    print(f"symbols ({len(symbols)}):")
    for symbol, count in sorted(symbols.items()):
        print(f"  {symbol:<14} {count}")
    if summary["coverage_gaps"]:
        print("\ncollector coverage gaps (UNKNOWN windows, never 'no events'):")
        for gap in summary["coverage_gaps"]:
            print(f"  {gap['source']} {gap['gap_from']} -> {gap['gap_to']} ({gap['reason']})")
    if summary["incomplete_imports"]:
        print("\nincomplete imports (these windows are UNKNOWN, not empty):")
        for item in summary["incomplete_imports"]:
            print(f"  {item['import_id']} {item['source']} {item['status']} {item['range']}")
    if summary["rss_note"]:
        print(f"\nnote: {summary['rss_note']}")
    return ExitCode.OK


async def _cmd_sync_rss(args: argparse.Namespace, store: ResearchStore) -> int:
    """Make the live RSS collection available to research, repeatably and without backdating."""
    settings = get_settings()
    with EventStore(settings.db_path) as event_store:
        events = event_store.read(types=["AnnouncementReceived", "AnnouncementCoverageGap"])
    announcements = [
        (e.payload, e.seq) for e in events if isinstance(e.payload, AnnouncementReceived)
    ]
    gaps = [e.payload for e in events if isinstance(e.payload, AnnouncementCoverageGap)]
    if not announcements:
        print(
            "no stored RSS announcements: run a session with ANNOUNCEMENTS_ENABLED=true first "
            "(the collector polls NSE's official RSS feed during the session and pre-open)"
        )
        return ExitCode.OK
    range_from = args.range_from or min(a.published_at.date() for a, _ in announcements)
    range_to = args.range_to or now_ist().date()
    source = EventStoreRssSource(announcements)
    if args.dry_run:
        page = await source.fetch(
            range_from=range_from, range_to=range_to, symbols=args.symbol,
            cursor=None, limit=100000,
        )  # fmt: skip
        print(f"[dry-run] the event store would sync {len(page.records)} record(s) "
              f"({range_from}..{range_to}) and {len(gaps)} coverage gap(s)")  # fmt: skip
        return ExitCode.OK
    report = await historical.import_history(
        store, source, range_from=range_from, range_to=range_to, symbols=args.symbol
    )
    synced_gaps = store.save_coverage_gaps([g.as_row() for g in gaps_from_events(gaps)])
    print(f"sync-rss {report.import_id} -> {report.status}")
    print(f"  fetched={report.fetched} inserted={report.inserted} "
          f"duplicates={report.duplicates} rejected={report.rejected}")  # fmt: skip
    print(f"  coverage gaps synced: {synced_gaps} statement(s), {len(gaps)} known gap(s)")
    summary = historical.coverage_summary(store)
    print("\nnse_rss archive (prospective collection, not a historical backfill):")
    print(f"  published from : {summary['oldest_published'].get('nse_rss', '-')}")
    print(f"  published to   : {summary['newest_published'].get('nse_rss', '-')}")
    print(f"  last received  : {summary['last_received'].get('nse_rss', '-')}")
    print(f"  records        : {summary['by_source'].get('nse_rss', 0)}")
    print(f"  capture kinds  : {summary['by_capture_kind']}")
    if summary["coverage_gaps"]:
        print(f"  unknown windows: {len(summary['coverage_gaps'])} coverage gap(s) - treat as "
              "missing data, not as quiet periods")  # fmt: skip
    for note in report.notes:
        print(f"  ! {note}")
    return ExitCode.OK if report.status == "completed" else ExitCode.CRASH


async def _cmd_outcomes(args: argparse.Namespace, store: ResearchStore) -> int:
    settings = get_settings()
    now = now_ist()
    mode = EvaluationMode(args.mode)
    horizons = _horizons(args.horizons)
    records = store.source_records(
        symbols=[s.upper() for s in args.symbol],
        range_from=args.range_from,
        range_to=args.range_to,
    )
    if not records:
        print("no imported records match; run `research_history.py import` first")
        return ExitCode.OK
    if mode is EvaluationMode.ACTUAL:
        eligible, excluded = actual_mode_records(records)
        records = [dict(row) for row in eligible]
        if excluded:
            print(f"ACTUAL mode: excluded {excluded} record(s) without a genuine recorded "
                  "receipt; backfilled imports stay in RECONSTRUCTED mode")  # fmt: skip
        if not records:
            print("no forward-captured records to evaluate: run a live session and `sync-rss` "
                  "first (ACTUAL mode reads only real recorded receipts)")  # fmt: skip
            return ExitCode.OK

    classifications = store.latest_classifications()
    new_rows = historical.classify_records(records, classified_at=now, existing=classifications)
    if new_rows and not args.dry_run:
        store.save_classifications([row.as_row() for row in new_rows])
    retrospective = sum(1 for row in new_rows if row.retrospective)
    print(f"classified {len(new_rows)} record(s) with {RULES_VERSION}"
          f" ({retrospective} retrospective)")  # fmt: skip
    classifications = store.latest_classifications()

    symbols = sorted({str(r["symbol"]) for r in records})
    by_symbol, instruments, notes = await _resolve_instruments(settings, symbols, now.date())
    series, benchmark, price_notes = await _fetch_prices(
        settings, instruments, wanted=symbols, by_symbol=by_symbol,
        lookback_days=args.lookback_days, period=args.period, settled_before=now.date(),
    )  # fmt: skip
    notes.extend(price_notes)
    calendar = get_calendar()
    provenance = historical.provenance_rows(
        series, provider=settings.market_data_provider, retrieved_at=now, calendar=calendar,
        benchmark=benchmark, benchmark_key=INDEX_KEY, requested_from=args.range_from,
        requested_to=args.range_to, universe_is_current=True,
    )  # fmt: skip

    outcomes, outcome_notes = historical.build_outcome_rows(
        records, classifications, series, benchmark=benchmark, instrument_of=by_symbol,
        mode=mode, computed_at=now, horizons=horizons,
        ingestion_lag=timedelta(minutes=args.ingestion_lag_min),
        classification_lag=timedelta(minutes=args.classification_lag_min),
    )  # fmt: skip
    notes.extend(outcome_notes)
    totals = summarize_outcomes(outcomes)
    print(f"\nmode={mode.value}  horizons={list(horizons)}  outcomes={len(outcomes)}")
    for status, count in sorted(totals.items()):
        print(f"  {status:<14} {count}")
    if args.dry_run:
        print("\n[dry-run] nothing written")
    else:
        store.save_price_datasets([p.as_row() for p in provenance])
        store.save_event_outcomes([o.as_row() for o in outcomes])
        print(f"\npersisted {len(provenance)} price dataset(s) and {len(outcomes)} outcome(s)")
    for note in dict.fromkeys(notes):
        print(f"  ! {note}")
    if mode is EvaluationMode.RECONSTRUCTED:
        print("\n  !! RECONSTRUCTED mode: availability is publication time plus assumed lags.")
        print("     These are not forward-recorded observations; do not pool them with mode A.")
    return ExitCode.OK


async def _cmd_walkforward(args: argparse.Namespace, store: ResearchStore) -> int:
    settings = get_settings()
    now = now_ist()
    mode = EvaluationMode(args.mode)
    horizons = _horizons(args.horizons)
    rows = store.event_outcomes()
    rows = [r for r in rows if str(r["evaluation_mode"]) == mode.value]
    if not rows:
        print(f"no outcomes stored for mode={mode.value}; run `outcomes --mode {mode.value}` first")
        return ExitCode.OK

    def as_outcome(row: dict[str, Any]) -> EventOutcome:
        return EventOutcome(
            outcome_id=str(row["outcome_id"]),
            record_id=str(row["record_id"]),
            instrument_key=str(row["instrument_key"]),
            horizon_sessions=int(row["horizon_sessions"]),
            evaluation_mode=mode,
            status=str(row["status"]),
            computed_at=now,
            anchor_date=date.fromisoformat(row["anchor_date"]) if row.get("anchor_date") else None,
            outcome_date=(
                date.fromisoformat(row["outcome_date"]) if row.get("outcome_date") else None
            ),
            instrument_return=row.get("instrument_return"),
            benchmark_return=row.get("benchmark_return"),
            abnormal_return=row.get("abnormal_return"),
            bucket=row.get("bucket"),
        )

    outcomes = [as_outcome(row) for row in rows]
    settled = [o for o in outcomes if o.is_settled]
    print(f"outcomes: {len(outcomes)} total, {len(settled)} settled")
    if not settled:
        print("nothing settled yet: every horizon is still pending, so there is nothing to score")
        return ExitCode.OK

    classifications = store.latest_classifications()
    fields_by_record: dict[str, dict[str, str | None]] = {}
    for record_id, row in classifications.items():
        fields_by_record[record_id] = {
            "event_type": row.get("event_type"),
            "direction": row.get("direction"),
            "materiality": row.get("materiality"),
        }

    symbols = sorted({o.instrument_key.split(":")[-1] for o in settled})
    by_symbol, instruments, notes = await _resolve_instruments(settings, symbols, now.date())
    series, benchmark, price_notes = await _fetch_prices(
        settings, instruments, wanted=symbols, by_symbol=by_symbol,
        lookback_days=DEFAULT_LOOKBACK_DAYS, period=DEFAULT_YFINANCE_PERIOD,
        settled_before=now.date(),
    )  # fmt: skip
    notes.extend(price_notes)
    technical_states = {key: technical_state_by_date(item) for key, item in series.items()}
    bands: dict[tuple[str, int], float | None] = {}
    for outcome in settled:
        bars = series.get(outcome.instrument_key)
        if bars is None:
            continue
        close = bars.adjusted()["close"].astype(float)
        bands[(outcome.instrument_key, outcome.horizon_sessions)] = sideways_band(
            close, outcome.horizon_sessions
        )

    config = WalkForwardConfig(
        horizons=horizons,
        min_train_samples=args.min_train,
        embargo_sessions=args.embargo,
        holdout_fraction=args.holdout,
        allow_pooling=args.allow_pooling,
        rules_version=RULES_VERSION,
        evaluation_mode=mode.value,
    )
    publications = historical.walkforward_publications(
        outcomes, run_id=args.run_id, config=config, published_at=now,
        fields_by_record=fields_by_record, technical_states=technical_states,
        sideways_bands=bands,
    )  # fmt: skip
    lookup = historical.outcome_lookup(outcomes)
    scored, pending = historical.score_publications(publications, lookup)
    print(f"publications: {len(publications)} ({len(scored)} scorable, {len(pending)} pending)")

    totals = {
        "records": len({o.record_id for o in outcomes}),
        "outcomes": len(outcomes),
        "outcomes_settled": len(settled),
        "outcomes_pending": len(outcomes) - len(settled),
        "publications": len(publications),
        "publications_scored": len(scored),
        "publications_pending": len(pending),
    }
    report = historical.evaluate_publications(
        scored, lookup, run_id=args.run_id, mode=mode, created_at=now, config=config,
        totals=totals,
    )  # fmt: skip
    if args.dry_run:
        print("[dry-run] nothing written")
    else:
        store.save_walkforward([p.as_row() for p in publications])
        evaluation_id = store.save_evaluation({
            "evaluation_id": f"{args.run_id}|{config.fingerprint()}|{now.date().isoformat()}",
            "run_id": args.run_id,
            "evaluation_mode": mode.value,
            "horizons": list(horizons),
            "configuration": config.as_dict(),
            "payload": report.as_dict(),
        })  # fmt: skip
        print(f"persisted {len(publications)} publication(s), evaluation {evaluation_id}")

    print(f"\nfallback rate: {report.fallback_rate}")
    for approach, metrics in sorted(report.by_approach.items()):
        interval = f" CI {metrics.brier_ci[0]}..{metrics.brier_ci[1]}" if metrics.brier_ci else ""
        print(
            f"  {approach:<20} n={metrics.n:<5} brier={metrics.brier}{interval} "
            f"coverage={metrics.interval_coverage} mean_AR={metrics.mean_abnormal_return}"
        )
    for comparison in report.comparisons:
        print(f"  {comparison.approach} vs {comparison.baseline}: n={comparison.n_paired} "
              f"diff={comparison.mean_loss_difference} -> {comparison.verdict}")  # fmt: skip
    for note in report.notes:
        print(f"  ! {note}")
    for note in dict.fromkeys(notes):
        print(f"  ! {note}")
    return ExitCode.OK


def _cmd_report(args: argparse.Namespace, store: ResearchStore) -> int:
    row = store.latest_evaluation(run_id=args.run_id)
    if row is None:
        print("no evaluation has been persisted; run `walkforward` first")
        return ExitCode.OK
    payload = json.loads(row["payload"])

    def cells(items: Any) -> tuple[CalibrationCell, ...]:
        return tuple(
            CalibrationCell(
                bucket=str(c["bucket"]),
                n=int(c["n"]),
                predicted=c.get("predicted"),
                realized=c.get("realized"),
            )  # fmt: skip
            for c in (items or [])
        )

    by_approach = {
        name: Metrics(
            approach=name,
            n=int(m["n"]),
            brier=m.get("brier"),
            brier_ci=tuple(m["brier_ci"]) if m.get("brier_ci") else None,
            reliability=cells(m.get("reliability")),
            interval_coverage=m.get("interval_coverage"),
            interval_width=m.get("interval_width"),
            share_up=m.get("share_up"),
            share_side=m.get("share_side"),
            share_down=m.get("share_down"),
            mean_abnormal_return=m.get("mean_abnormal_return"),
            mean_abnormal_ci=(tuple(m["mean_abnormal_ci"]) if m.get("mean_abnormal_ci") else None),
            median_abnormal_return=m.get("median_abnormal_return"),
            fallback_count=int(m.get("fallback_count") or 0),
            notes=tuple(m.get("notes") or ()),
        )
        for name, m in (payload.get("by_approach") or {}).items()
    }
    comparisons = tuple(
        PairedComparison(
            approach=str(c["approach"]),
            baseline=str(c["baseline"]),
            n_paired=int(c["n_paired"]),
            mean_loss_difference=c.get("mean_loss_difference"),
            ci=tuple(c["ci"]) if c.get("ci") else None,
            wins=c.get("wins"),
            verdict=str(c.get("verdict") or ""),
            notes=tuple(c.get("notes") or ()),
        )  # fmt: skip
        for c in (payload.get("comparisons") or [])
    )
    report = EvaluationReport(
        run_id=str(row["run_id"]),
        evaluation_mode=str(row["evaluation_mode"]),
        created_at=datetime.fromisoformat(str(row["created_at_utc"])),
        configuration=json.loads(row["configuration"] or "{}"),
        totals=payload.get("totals") or {},
        cohort_sizes=payload.get("cohort_sizes") or {},
        by_approach=by_approach,
        comparisons=comparisons,
        fallback_rate=payload.get("fallback_rate"),
        notes=tuple(payload.get("notes") or ()),
    )
    markdown = historical.render_markdown(
        report,
        limitations=[
            "historical imports were classified retrospectively with "
            f"{RULES_VERSION}; that is not what a participant saw at the time",
            "the evaluated instrument set is derived from the current scrip master and universe, "
            "so delisted issuers are absent (survivorship)",
        ],
    )
    if args.markdown is not None:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(markdown, encoding="utf-8")
        print(f"wrote {args.markdown}")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report.as_dict(), indent=2, default=str), encoding="utf-8")
        print(f"wrote {args.json}")
    if args.markdown is None and args.json is None:
        print(markdown)
    return ExitCode.OK


async def _amain(args: argparse.Namespace, store: ResearchStore) -> int:
    if args.command == "import":
        return await _cmd_import(args, store)
    if args.command == "sync-rss":
        return await _cmd_sync_rss(args, store)
    if args.command == "outcomes":
        return await _cmd_outcomes(args, store)
    if args.command == "walkforward":
        return await _cmd_walkforward(args, store)
    return _cmd_coverage(args, store)


def main() -> int:
    args = _parse_args()
    if args.command == "report":
        with ResearchStore(get_settings().db_path) as store:
            return _cmd_report(args, store)
    if args.command == "coverage":
        with ResearchStore(get_settings().db_path) as store:
            return _cmd_coverage(args, store)
    with ResearchStore(get_settings().db_path) as store:
        return asyncio.run(_amain(args, store))


if __name__ == "__main__":
    run_entry_point("research_history", main)
