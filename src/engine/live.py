"""
Running the v2 engine behind a front end (plan M5.6, M12.2): the CLI paints it through an
:class:`EngineView` (``src/dashboard/cli.py``); the web console needs none - it reads the
store's projections and the engine's live state itself (``src/web/``).

* :func:`run_paper` - today's session on the wall clock with YFinance data (taped for replay),
  the pinned NIFTY 50 universe and the simulated broker, for every book of the experiment
  (``src/config/experiment.yaml``: A deterministic, B typed veto, C LLM veto). **Paper only**:
  the v2 engine has no live broker path; a live ``EXECUTION_MODE`` is ignored with a warning.
* :func:`run_demo` - the same engine on the bundled tape, paced, in the ``demo`` environment
  (no decision model or LLM is called: book B's veto is scripted, C's advisor abstains).

Both front ends show every book of the experiment from the same read model
(``src/web/queries.py``); the paired comparison is the daily report's.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from datetime import date, datetime, time
from pathlib import Path
from typing import Protocol

from src.config.errors import ConfigError
from src.config.limits import load_risk_limits
from src.config.settings import Settings
from src.decision_models.cascade import Cascade
from src.decision_models.setup import build_cascade
from src.decision_models.tasks.announcements import (
    AnnouncementPipeline,
    EventClassifier,
    RuleEventClassifier,
    unclassified,
)
from src.domain.calendar import get_calendar
from src.domain.clock import Clock, ReplayClock, WallClock, now_ist
from src.domain.events import Alert, AnnouncementReceived
from src.domain.types import Instrument
from src.engine.demo import DEMO_DAY, DEMO_TAPE, ScriptedVeto, demo_instruments, pace
from src.engine.market import INDEX_KEY, HistorySource, QuoteSource
from src.engine.runner import Engine, build_engine, held_instruments
from src.evaluation.books import build_advisors, engine_config
from src.evaluation.daily_report import (
    ReportInputs,
    build_report,
    send_summary,
    telegram_summary,
    write_report,
)
from src.evaluation.experiment import DEFAULT_EXPERIMENT_PATH, ExperimentConfig, load_experiment
from src.evaluation.review import review_day
from src.llm.registry import validate_roles
from src.llm.router import LLMRouter, StoreResponseCache
from src.llm.setup import build_router
from src.marketdata.adjustments import YahooCorporateActions
from src.marketdata.announcements import AnnouncementIngestor, watermark
from src.marketdata.composite import CompositeHistorySource
from src.marketdata.dhan import (
    DhanHistorySource,
    DhanQuoteSource,
    build_client,
    check_data_plan,
    validate_market_data_provider,
)
from src.marketdata.dhan.instruments import INDEX_SEGMENT, DhanMapping
from src.marketdata.dhan.setup import build_history_cache, security_overrides
from src.marketdata.history import YFinanceHistorySource
from src.marketdata.replay import TapeHistorySource, TapeQuoteSource
from src.marketdata.validation import QuoteValidator, band_lookup
from src.marketdata.yfinance_source import YFinanceQuoteSource
from src.notifications.telegram import TelegramNotifier
from src.reference.refresh import alert_reference, refresh_reference
from src.store.event_store import EventStore
from src.store.sink import StoreSink
from src.store.tape import TapeWriter, read_bars, read_quotes
from src.utils.market_time import IST

logger = logging.getLogger(__name__)

PAPER_MODES = frozenset({"local_paper", "shadow"})
DM_CACHE = "dm_cache"  # decision-model answers, by state and questions
DEMO = "demo"
STOP_GRACE_S = 30.0  # plan M9.3: a cooperative stop, then cancellation
PAINT_EVERY_S = 1.0


class EngineView(AbstractAsyncContextManager[object], Protocol):
    """A front end that paints the running engine (the CLI dashboard): entered for the run,
    painted every second and once more when the engine has stopped."""

    async def paint(self, engine: Engine) -> None: ...


# The symbol half of INDEX_KEY. Mapping it in DHAN_SECURITY_IDS (NIFTY50=IDX_I:13) lets Dhan
# serve the index the regime reads; without it, YFinance does.
INDEX_SYMBOL = "NIFTY50"


def _dhan_index_instrument(overrides: Mapping[str, DhanMapping]) -> Instrument | None:
    mapping = overrides.get(INDEX_SYMBOL)
    if mapping is None or mapping.segment != INDEX_SEGMENT:
        return None
    return Instrument(
        key=INDEX_KEY, exchange="NSE", segment="INDEX", symbol=INDEX_SYMBOL, series="INDEX",
        broker_tokens={"dhan": mapping.security_id},
    )  # fmt: skip


async def run_paper(
    settings: Settings,
    view: EngineView | None = None,
    *,
    stop: asyncio.Event | None = None,
    clock: Clock | None = None,
    on_engine: Callable[[Engine], None] | None = None,
) -> int:
    if settings.environment == DEMO:
        raise ConfigError("the demo environment runs synthetic data: use --demo")
    validate_roles(settings)  # a misconfigured enabled LLM role fails startup (exit 2)
    validate_market_data_provider(settings)  # and so does a provider without its credentials
    mode = settings.execution_mode
    if mode not in PAPER_MODES:
        logger.warning("EXECUTION_MODE=%s ignored: the v2 engine trades on the simulated "
                       "broker only (paper)", mode)  # fmt: skip
    clock = clock or WallClock()
    calendar = get_calendar()
    limits = load_risk_limits()
    experiment = load_experiment(settings.experiment_file or DEFAULT_EXPERIMENT_PATH)
    config = engine_config(experiment, environment=settings.environment, limits=limits,
                           halt_file=settings.halt_file)  # fmt: skip
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    with EventStore(settings.db_path) as store:
        sink = StoreSink(store, clock, "engine")
        reference = await refresh_reference(
            settings.reference_dir, now_ist(clock).date(),
            universe_file=settings.universe_file, universe_sectors=settings.universe_sectors,
        )  # fmt: skip
        alert_reference(reference, sink)
        universe = list(reference.instruments.by_symbol.values())
        priced = {i.key: i for i in universe}
        for book_id in config.books:
            priced |= held_instruments(store, book_id)
        # One Laya for the classifier and Book B; every answer is cached for replays (M8.5).
        cascade = build_cascade(settings, sink=sink, cache=StoreResponseCache(store, DM_CACHE))
        router = build_router(settings, clock=clock, sink=sink, store=store)
        tape = TapeWriter(settings.tape_dir)
        validator = QuoteValidator(band_lookup(priced.values()))
        quotes: QuoteSource
        history: HistorySource
        if settings.market_data_provider == "dhan":
            overrides = security_overrides(settings)
            index_instrument = _dhan_index_instrument(overrides)
            client = build_client(settings)
            await check_data_plan(client, sink=sink)  # a Deactive data plan stops startup
            quotes = DhanQuoteSource(
                list(priced.values()), clock=clock, sink=sink, client=client, tape=tape,
                validator=validator, market_open=calendar.is_market_open, overrides=overrides,
            )  # fmt: skip
            history = CompositeHistorySource(
                primary=DhanHistorySource(
                    client=client, tape=tape, overrides=overrides,
                    cache=build_history_cache(settings),  # settled candles: fetch once, reuse
                    adjustments=YahooCorporateActions(),  # Dhan serves none; Yahoo's table does
                ),  # fmt: skip
                secondary=YFinanceHistorySource(tape=tape),  # the NIFTY index, for the regime
                secondary_keys=set() if index_instrument else {INDEX_KEY},
                extra_instruments=[index_instrument] if index_instrument else [],
                # Even when DHAN_SECURITY_IDS routes the index to Dhan, Yahoo stays as its
                # fallback: a Dhan rate limit must not leave the regime unknown.
                secondary_fallback_keys={INDEX_KEY} if index_instrument else set(),
            )  # fmt: skip
            sink.emit(Alert(level="WARNING", key="dhan_history_unadjusted",
                            message="MARKET_DATA_PROVIDER=dhan: Dhan itself publishes no "
                                    "corporate actions. Daily history keeps Dhan's raw prices "
                                    "(quotes, fills and stops stay raw); indicators use a "
                                    "dividend adjustment from a separately sourced Yahoo "
                                    "corporate-action table. When that overlay is unavailable "
                                    "for a symbol, a 'history_unadjusted' warning names it and "
                                    "its indicators fall back to raw closes."),
                  source="marketdata")  # fmt: skip
        else:
            quotes = YFinanceQuoteSource(
                list(priced.values()), clock=clock, sink=sink, tape=tape,
                validator=validator, market_open=calendar.is_market_open,
            )  # fmt: skip
            history = YFinanceHistorySource(tape=tape)
        engine = build_engine(
            config=config, clock=clock, calendar=calendar, store=store, quotes=quotes,
            history=history, universe=universe, limits=limits,
            announcements=_announcements(settings, store, clock, sink, priced, cascade),
        )  # fmt: skip
        advisors = build_advisors(experiment, sink=sink, clock=clock, calendar=calendar,
                                  cascade=cascade, router=router, events=engine.events_for,
                                  regime=lambda: engine.regime)  # fmt: skip
        for book_id, advisor in advisors.items():
            engine.set_advisor(book_id, advisor)
        engine.reporter = make_reporter(engine, experiment, settings, settings.reports_dir,
                                    notify=True, router=router)  # fmt: skip
        if on_engine is not None:
            on_engine(engine)
        return await _drive(engine, view, stop)


def _announcements(
    settings: Settings,
    store: EventStore,
    clock: Clock,
    sink: StoreSink,
    instruments: dict[str, Instrument],
    cascade: Cascade | None,
) -> AnnouncementPipeline | None:
    if not settings.announcements_enabled:
        return None
    stored = [p for e in store.read(types=["AnnouncementReceived"])
              if isinstance(p := e.payload, AnnouncementReceived)]  # fmt: skip
    seen, newest = watermark(stored)
    equities = [i for i in instruments.values() if i.series == "EQ"]
    ingestor = AnnouncementIngestor(instruments=equities, clock=clock, sink=sink,
                                    url=settings.announcements_url, seen=seen,
                                    last_newest=newest)  # fmt: skip
    classified = [r["event_id"] for r in store.query("SELECT event_id FROM typed_events")]
    backlog = unclassified(stored, classified)
    if cascade is None:
        # One actionable startup warning (not a per-cycle repeat): the deterministic subject
        # rules keep classifying, and the message says exactly what to configure for more.
        logger.warning("no decision model (laya not installed, no TYPESAFE_API_KEY): "
                       "announcements are classified by subject rules only")  # fmt: skip
        sink.emit(Alert(level="WARNING", key="decision_models_unavailable",
                        message="no decision model configured: announcements are classified by "
                                "deterministic NSE subject rules only; direction and materiality "
                                "stay UNKNOWN. Enable the local model (uv sync --extra "
                                "decision-local, DECISION_LAYA_ENABLED=true) or set "
                                "TYPESAFE_API_KEY for the remote model."), source="engine")  # fmt: skip
        return AnnouncementPipeline(ingestor=ingestor,
                                    classifier=RuleEventClassifier(sink=sink, clock=clock),
                                    backlog=backlog)  # fmt: skip
    classifier = EventClassifier(cascade=cascade, sink=sink, clock=clock)
    return AnnouncementPipeline(ingestor=ingestor, classifier=classifier, backlog=backlog)


async def run_demo(
    settings: Settings,
    view: EngineView | None = None,
    *,
    stop: asyncio.Event | None = None,
    step_s: float = 30.0,
    wall_s: float = 0.1,
    on_engine: Callable[[Engine], None] | None = None,
    tape_dir: Path = DEMO_TAPE,
) -> int:
    """Replay the bundled fixture tape (one recorded session) through the real engine, paced:
    ``step_s`` of session time every ``wall_s`` of real time."""
    if settings.environment != DEMO:
        raise ConfigError("the demo runs only in ENVIRONMENT=demo (its own state directory)")
    validate_roles(settings)
    calendar = get_calendar()
    day = DEMO_DAY
    bars, quotes = read_bars(tape_dir, day), read_quotes(tape_dir, day)
    if not bars or not quotes:
        raise ConfigError(f"the demo tape for {day} is missing (scripts/build_demo_tape.py)")
    clock = ReplayClock(datetime.combine(day, time(9, 0), IST))
    settings.state_dir.mkdir(parents=True, exist_ok=True)
    path = demo_store_path(settings)
    for stale in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        stale.unlink(missing_ok=True)  # every demo starts from a clean book
    limits = load_risk_limits()
    experiment = load_experiment(settings.experiment_file or DEFAULT_EXPERIMENT_PATH)
    config = engine_config(experiment, environment=settings.environment, limits=limits,
                           halt_file=settings.halt_file)  # fmt: skip
    with EventStore(path) as store:
        engine = build_engine(
            config=config, clock=clock, calendar=calendar, store=store,
            quotes=TapeQuoteSource(quotes, clock=clock), history=TapeHistorySource(bars),
            universe=demo_instruments(), limits=limits,
        )  # fmt: skip
        sink = StoreSink(store, clock, "engine")
        advisors = build_advisors(experiment, sink=sink, clock=clock, calendar=calendar,
                                  cascade=None, router=None, events=engine.events_for,
                                  regime=lambda: engine.regime)  # fmt: skip
        for book_id, spec in experiment.books.items():  # no model runs in the demo: script it
            if spec.advisor == "typed_veto":
                advisors[book_id] = ScriptedVeto(book_id=book_id, sink=sink)
        for book_id, advisor in advisors.items():
            engine.set_advisor(book_id, advisor)
        engine.reporter = make_reporter(engine, experiment, settings,
                                    settings.state_dir / "reports", notify=False)  # fmt: skip
        if on_engine is not None:
            on_engine(engine)
        done = asyncio.Event()
        exit_at = datetime.combine(day, time(16, 0), IST)
        pacer = asyncio.create_task(pace(clock, exit_at, step_s=step_s, wall_s=wall_s, done=done))
        try:
            return await _drive(engine, view, stop)
        finally:
            done.set()
            await asyncio.gather(pacer, return_exceptions=True)


def demo_store_path(settings: Settings) -> Path:
    """The demo's own store, recreated by every demo run. Not the environment's ``db_path``:
    the entry point keeps that open for its process events, and Windows can't delete an open
    file."""
    return settings.state_dir / "demo.db"


_PENDING: set[asyncio.Task[bool]] = set()  # fire-and-forget summaries (kept from GC)


def make_reporter(
    engine: Engine,
    experiment: ExperimentConfig,
    settings: Settings,
    reports_dir: Path,
    *,
    notify: bool,
    router: LLMRouter | None = None,
) -> Callable[[date], Awaitable[None]]:
    """The REPORT step: the daily report to ``reports_dir``, a Telegram summary (5 s timeout,
    fire-and-forget: the report is on disk whether or not the message goes out), then the nightly
    review when a ``router`` is given (role ``review``; a no-op when the role is not configured)."""

    async def report(day: date) -> None:
        lows = {b: float(book.tracker.state.low_equity) for b, book in engine.books.items()
                if book.tracker.state is not None}  # fmt: skip
        inputs = ReportInputs(
            day=day, capital=experiment.capital_inr, books=engine.config.books,
            advisors={b: spec.advisor for b, spec in experiment.books.items()},
            experiment=experiment.experiment, nifty_closes=engine.index_closes(),
            equal_weight_day_pct=engine.equal_weight_day_pct(),
            infra_cost_inr_per_day=settings.infra_cost_inr_per_day, intraday_low_equity=lows,
        )  # fmt: skip
        result = build_report(engine.store, inputs, engine.calendar)
        _, md_path = write_report(result, reports_dir)
        engine.sink.emit(Alert(level="INFO", key="daily_report", message=f"written {md_path.name}"),
                         source="engine")  # fmt: skip
        if notify:
            notifier = TelegramNotifier()
            if notifier.enabled:
                task = asyncio.create_task(send_summary(telegram_summary(result),
                                                        notifier.send_message))  # fmt: skip
                _PENDING.add(task)
                task.add_done_callback(_PENDING.discard)
        if router is not None:
            try:
                await review_day(engine.store, router, day, clock=engine.clock, sink=engine.sink)
            except Exception as exc:  # the review is advisory: never fails the report
                logger.exception("nightly review failed")
                engine.sink.emit(Alert(level="WARNING", key="nightly_review_failed",
                                       message=f"{type(exc).__name__}: {exc}"),
                                 source="engine")  # fmt: skip

    return report


async def _paint(view: EngineView | None, engine: Engine) -> None:
    if view is None:
        return
    try:
        await view.paint(engine)
    except Exception:  # the view never stops trading
        logger.exception("view refresh failed")


async def _drive(
    engine: Engine,
    view: EngineView | None,
    stop: asyncio.Event | None,
    *,
    grace_s: float = STOP_GRACE_S,
) -> int:
    """Run the engine; paint the view every second. When ``stop`` is set the engine stops
    cooperatively at its next state boundary; only after ``grace_s`` is it cancelled (and an
    order submission in flight still completes: ``OMS.submit`` is shielded)."""
    async with view if view is not None else contextlib.nullcontext():
        run = asyncio.create_task(engine.run(), name="engine")

        async def refresh() -> None:
            while not run.done():
                await _paint(view, engine)
                await asyncio.sleep(PAINT_EVERY_S)

        painter = asyncio.create_task(refresh(), name="view")
        stopper = asyncio.create_task(stop.wait() if stop else asyncio.Event().wait())
        await asyncio.wait({run, stopper}, return_when=asyncio.FIRST_COMPLETED)
        if not run.done():  # asked to stop
            engine.request_stop()
            finished, _ = await asyncio.wait({run}, timeout=grace_s)
            if not finished:
                logger.error("the engine did not stop within %.0f s: cancelling", grace_s)
                run.cancel()
        stopper.cancel()
        results = await asyncio.gather(run, stopper, return_exceptions=True)
        painter.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await painter
        await _paint(view, engine)
    outcome = results[0]
    if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
        raise outcome
    return outcome if isinstance(outcome, int) else 0
