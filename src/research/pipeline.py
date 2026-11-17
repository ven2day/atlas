"""One research run: from the event store to persisted, explainable candidate cards.

This is the only module in the package that reaches outside it. It gathers what the rest of the
package treats as given - the universe, settled daily bars, features, the regime and the stored
events - and hands them to :func:`src.research.cards.build_cards`. Everything downstream of that
call is pure, so the CLI, the web read model and the evaluation harness all see identical cards.

Three things it is careful about:

* **Point in time.** Events are filtered by ``known_at <= as_of``, and bars are fetched with
  ``settled_before`` set to the run's date, so the forming session is never in the sample.
* **No fabrication.** A provider failure, an unmapped symbol or a synthetic source becomes a
  named entry in ``data_gaps`` and an ``unsupported`` note on the card. Nothing is filled in.
* **Settlement before publication.** Previously published forecasts whose horizon has now elapsed
  are scored first, so the calibration reported alongside today's cards includes everything
  known at this instant.
"""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime

from src.brokers.simulated.costs import NSECostSchedule
from src.config.settings import Settings
from src.domain.calendar import CalendarCoverageError, NSECalendar, get_calendar
from src.domain.events import RegimeComputed
from src.domain.types import Regime
from src.engine.market import INDEX_KEY, INDEX_TICKER, build_history_source
from src.features.technical import Features, compute_features
from src.marketdata.history import DailySeries
from src.reference.refresh import refresh_reference
from src.research.cards import CandidateCard, build_cards
from src.research.evaluation import (
    CalibrationReport,
    EventStudyCell,
    calibration,
    event_study,
    round_trip_cost_pct,
    settle_forecasts,
)
from src.research.events import research_events
from src.research.forecast import DEFAULT_HORIZON
from src.research.mapping import map_events
from src.research.snapshot import reports_dir_for, write_snapshot
from src.research.store import ResearchStore, known_at_by_id
from src.store.event_store import EventStore
from src.utils.market_time import now_ist

logger = logging.getLogger(__name__)

# How many data gaps to name in a summary before truncating.
_GAP_LIMIT = 12


@dataclass(frozen=True, slots=True)
class ResearchRun:
    """The output of one run: the cards, and an account of what could not be produced."""

    as_of: datetime
    horizon: int
    cards: tuple[CandidateCard, ...]
    status_counts: Mapping[str, int]
    calibration: CalibrationReport
    study: tuple[EventStudyCell, ...]
    data_gaps: tuple[str, ...]
    persisted: bool
    settled: int = 0

    def summary(self) -> str:
        counts = ", ".join(f"{k}={v}" for k, v in sorted(self.status_counts.items())) or "none"
        return f"{len(self.cards)} candidate(s) [{counts}] at {self.as_of.isoformat()}"


def _latest_regime(store: EventStore) -> tuple[Regime | None, date | None]:
    """The most recent regime the engine computed. ``None`` when it has never run."""
    for event in reversed(store.read(types=["RegimeComputed"])):
        payload = event.payload
        if isinstance(payload, RegimeComputed):
            return payload.label, payload.bar_date
    return None, None


def _sessions_since(bar_date: date, as_of: date, calendar: NSECalendar) -> int | None:
    """Trading sessions between the last settled bar and the run date, or None when the calendar
    does not cover them (it fails closed outside its years rather than guessing)."""
    try:
        if not calendar.covers(bar_date) or not calendar.covers(as_of):
            return None
        return max(0, len(calendar.trading_days(bar_date, as_of)) - 1)
    except CalendarCoverageError:
        return None


async def run_research(
    settings: Settings,
    *,
    horizon: int = DEFAULT_HORIZON,
    as_of: datetime | None = None,
    store: EventStore | None = None,
    persist: bool = True,
    settle: bool = True,
) -> ResearchRun:
    """Run one research pass and (by default) persist what it published.

    ``store`` may be supplied to reuse an already-open event store; otherwise one is opened on
    ``settings.db_path`` and closed again. The research tables are written through a separate
    :class:`~src.research.store.ResearchStore` connection either way.
    """
    moment = as_of or now_ist()
    day = moment.date()
    gaps: list[str] = []

    owns_store = store is None
    event_store = store or EventStore(settings.db_path)
    research_store = ResearchStore(settings.db_path) if persist or settle else None
    try:
        reference = await refresh_reference(
            settings.reference_dir,
            day,
            universe_file=settings.universe_file,
            universe_sectors=settings.universe_sectors,
        )
        # ``by_symbol`` is keyed by bare symbol; events, bars, features and cards all key on the
        # instrument key ("NSE:EQ:INFY"), so re-key here exactly as the live session does.
        instruments = {i.key: i for i in reference.instruments.by_symbol.values()}
        for key, message in sorted(reference.problems.items()):
            gaps.append(f"{key}: {message}")

        events = research_events(event_store, instruments=instruments, as_of=moment)
        if not events:
            gaps.append(
                "no announcements are stored: the research path reads the engine's announcement "
                "pipeline, so it needs at least one session with ANNOUNCEMENTS_ENABLED=true "
                "(or a replayed day) before it can name a catalyst"
            )

        # Instruments this run must price: everything with a catalyst, plus anything a previous
        # run published a forecast for that is still awaiting an outcome.
        wanted = {e.instrument_key for e in events if e.instrument_key}
        unsettled: list[dict[str, object]] = (
            research_store.unsettled_forecasts() if research_store is not None and settle else []
        )
        for row in unsettled:
            key = str(row.get("instrument_key") or "")
            if key and key not in instruments:
                gaps.append(
                    f"{key}: a published forecast awaits an outcome but the instrument is not in "
                    "today's universe, so it cannot be settled"
                )
            elif key:
                wanted.add(key)

        targets = [instruments[key] for key in sorted(wanted) if key in instruments]
        series: dict[str, DailySeries] = {}
        features: dict[str, Features] = {}
        benchmark: DailySeries | None = None

        # The index is always fetched: it is the benchmark for relative strength and the
        # underperformance estimate, even on a run where nothing has a catalyst.
        calendar = get_calendar()
        try:
            previous = calendar.previous_trading_day(day) if calendar.covers(day) else None
        except CalendarCoverageError:
            previous = None
            gaps.append(
                f"the NSE calendar does not cover {day.isoformat()}: bar staleness is not "
                "checked against a session calendar"
            )
        history = build_history_source(settings)
        result = await history.fetch(
            targets,
            settled_before=day,
            expected_last=previous,
            extra={INDEX_KEY: INDEX_TICKER},
        )
        series = dict(result.series)
        benchmark = result.series.get(INDEX_KEY)
        if benchmark is None:
            gaps.append(
                "no benchmark (NIFTY 50) series: relative strength and underperformance are "
                "not estimated on any card"
            )
        for key, reason in sorted(result.failed.items()):
            gaps.append(f"{key}: no daily history ({reason})")
        for key, last in sorted(result.lagging.items()):
            gaps.append(f"{key}: daily history lags, last settled bar {last.isoformat()}")
        for key, item in series.items():
            try:
                features[key] = compute_features(item.adjusted(), key)
            except (ValueError, KeyError) as exc:
                gaps.append(f"{key}: features could not be computed ({exc})")

        regime, _regime_bar = _latest_regime(event_store)
        if regime is None:
            gaps.append(
                "no RegimeComputed event is stored: market context is reported without a regime "
                "(the engine computes it pre-open, so run a session or a replay first)"
            )

        ages: dict[str, int] = {}
        for key, item in series.items():
            age = _sessions_since(item.last_date, day, calendar)
            if age is not None:
                ages[key] = age

        cards = build_cards(
            events=events,
            instruments=instruments,
            series=series,
            features=features,
            regime=regime,
            benchmark=benchmark,
            as_of=moment,
            horizon=horizon,
            sessions_since=ages,
        )

        cost_pct: float | None = None
        try:
            cost_pct = round_trip_cost_pct(NSECostSchedule.from_yaml())
        except (OSError, ValueError, KeyError) as exc:
            gaps.append(f"the NSE cost schedule could not be read ({exc}): setup returns are gross")

        settled = 0
        report = CalibrationReport(
            n=0, brier=None, cells=(), by_horizon={}, by_regime={},
            notes=("nothing was persisted, so there is no calibration history",),
        )  # fmt: skip
        study: tuple[EventStudyCell, ...] = ()
        if research_store is not None:
            if settle:
                settled = settle_forecasts(
                    research_store, series, benchmark=benchmark, as_of=moment
                )
            predictions = [c.forecast for c in cards if c.forecast is not None]
            research_store.save_forecasts(predictions)
            research_store.save_cards(cards)
            current = [e for e in events if e.is_current]
            research_store.save_impacts(map_events(current), known_at_by_id(current))
            report = calibration(research_store.settled_forecasts())
        study = tuple(
            event_study(
                map_events([e for e in events if e.is_current]),
                series,
                benchmark=benchmark,
                known_at=known_at_by_id(events),
                regime_by_date=_regime_by_date(event_store),
                cost_pct=cost_pct,
            )
        )

        run = ResearchRun(
            as_of=moment,
            horizon=horizon,
            cards=tuple(cards),
            status_counts=Counter(c.status.value for c in cards),
            calibration=report,
            study=study,
            data_gaps=tuple(gaps[:_GAP_LIMIT])
            if len(gaps) <= _GAP_LIMIT
            else (*gaps[:_GAP_LIMIT], f"and {len(gaps) - _GAP_LIMIT} more"),
            persisted=research_store is not None,
            settled=settled,
        )
        if persist:
            # The console serves the latest run from this snapshot; failing to write it must not
            # discard the run itself, which the caller may still want on the terminal.
            try:
                write_snapshot(reports_dir_for(settings), run, cards)
            except OSError as exc:
                logger.warning("could not publish the research snapshot: %s", exc)
        return run
    finally:
        if research_store is not None:
            research_store.close()
        if owns_store:
            event_store.close()


def _regime_by_date(store: EventStore) -> dict[date, str]:
    """Session date -> regime label, so an event study can group by the regime it happened in."""
    out: dict[date, str] = {}
    for event in store.read(types=["RegimeComputed"]):
        payload = event.payload
        if isinstance(payload, RegimeComputed):
            out[payload.session_date] = payload.label.value
    return out


def unsupported_assets(cards: Sequence[CandidateCard]) -> tuple[str, ...]:
    """The symbols a run could not price at all, for an explicit "not supported" report."""
    return tuple(sorted({c.symbol or c.instrument_key for c in cards if not c.context.bars}))
