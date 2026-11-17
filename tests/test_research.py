"""Offline regression checks for research timing, evidence and publication integrity."""

from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from src.domain.calendar import get_calendar
from src.domain.events import AnnouncementReceived, RegimeComputed, make_event
from src.domain.types import AnnouncementType, EventDirection, Materiality, Regime, TypedEvent
from src.marketdata.history import DailySeries
from src.research.context import Confirmation, MarketContext, TrendState
from src.research.evaluation import event_study
from src.research.events import AssetClass, EventChannel, ResearchEvent, research_events
from src.research.forecast import Forecast, ForecastStatus, forecast
from src.research.mapping import EventImpact, Evidence, ImpactDirection, ImpactScope
from src.research.pipeline import _latest_regime
from src.research.status import CandidateStatus, decide_status
from src.research.store import ResearchStore, _utc, forecast_id
from src.research.timing import first_close_after
from src.store.event_store import EventStore
from src.utils.market_time import IST

T0 = datetime(2026, 10, 5, 10, tzinfo=IST)
KEY = "NSE:EQ:INFY"


def catalyst(event_id: str = "a1", known: datetime = T0) -> ResearchEvent:
    return ResearchEvent(
        event_id=event_id,
        instrument_key=KEY,
        symbol="INFY",
        company="Infosys",
        sector="IT",
        asset=AssetClass.EQUITY,
        source="nse_rss",
        source_url=None,
        published_at=known,
        ingested_at=known,
        event_time=None,
        known_at=known,
        event_type=AnnouncementType.ORDER_WIN,
        subject="Order win",
        text="New order",
        relevant=True,
        direction=EventDirection.POSITIVE,
        materiality=Materiality.MAJOR,
        source_confidence=0.95,
        classification_confidence=0.95,
        classification_basis="exchange_subject",
        calibrated=True,
    )


def prices() -> DailySeries:
    dates = get_calendar().trading_days(date(2026, 1, 2), date(2026, 10, 2))
    close = 100 * np.exp(np.arange(len(dates)) * 0.002)
    frame = pd.DataFrame(
        {c: close for c in ("open", "high", "low", "close")}, index=pd.to_datetime(dates)
    )
    frame["volume"], frame["factor"] = 1000, 1.0
    return DailySeries(KEY, frame)


def impact(event_id: str = "a1") -> EventImpact:
    return EventImpact(
        event_id,
        KEY,
        "INFY",
        AssetClass.EQUITY,
        "IT",
        ImpactScope.ISSUER,
        EventChannel.DEMAND,
        ImpactDirection.POSITIVE,
        Materiality.MAJOR,
        0.95,
        "rule",
        False,
        (Evidence("event_type", "order_win"),),
        "order won",
    )


def context() -> MarketContext:
    return MarketContext(
        KEY,
        date(2026, 10, 2),
        180,
        None,
        TrendState.UP,
        150,
        None,
        None,
        None,
        None,
        0.01,
        0.16,
        1,
        100000,
        {20: 0.1},
        "real history",
    )


def prediction() -> Forecast:
    return Forecast(
        KEY,
        5,
        ForecastStatus.OK,
        T0,
        date(2026, 10, 2),
        None,
        "regime_analogs",
        p_up=0.8,
        p_side=0.1,
        p_down=0.1,
        n_samples=100,
        n_unconditional=150,
    )


def test_classification_is_not_visible_before_it_exists(tmp_path: Path) -> None:
    raw = AnnouncementReceived(
        announcement_id="a1",
        instrument_key=KEY,
        company="Infosys",
        published_at=T0,
        received_at=T0,
        title="New order",
        source="nse_rss",
    )
    typed = TypedEvent(
        event_id="a1",
        instrument_key=KEY,
        published_at=T0,
        title="New order",
        source="nse_rss",
        relevant=True,
        announcement_type=AnnouncementType.ORDER_WIN,
        direction=EventDirection.POSITIVE,
        model="test",
        calibrated=True,
        classified_at=T0 + timedelta(minutes=10),
    )
    with EventStore(tmp_path / "events.db") as store:
        store.append(make_event(raw, ts=T0, source="test"))
        store.append(make_event(typed, ts=typed.classified_at, source="test"))
        early = research_events(store, as_of=T0 + timedelta(minutes=5))
        assert len(early) == 1 and early[0].event_type is None
        later = research_events(store, as_of=T0 + timedelta(minutes=11))
        assert later[0].known_at == typed.classified_at
        # A later reclassification must not erase the version available to an earlier replay.
        revised = typed.model_copy(
            update={"direction": EventDirection.NEGATIVE, "classified_at": T0 + timedelta(hours=1)}
        )
        store.append(make_event(revised, ts=revised.classified_at, source="test"))
        assert (
            research_events(store, as_of=T0 + timedelta(minutes=11))[0].direction
            is EventDirection.POSITIVE
        )


def test_regime_replay_excludes_future_computations(tmp_path: Path) -> None:
    with EventStore(tmp_path / "events.db") as store:
        for n, label in enumerate((Regime.TRENDING_UP, Regime.TRENDING_DOWN)):
            payload = RegimeComputed(
                session_date=T0.date(),
                bar_date=date(2026, 10, 2),
                label=label,
                raw=label,
                changed=False,
            )
            store.append(make_event(payload, ts=T0 + timedelta(hours=n), source="test"))
        assert _latest_regime(store, T0)[0] is Regime.TRENDING_UP
        assert _latest_regime(store, T0 - timedelta(seconds=1)) == (None, None)


def test_same_day_independent_events_are_not_corrections(tmp_path: Path) -> None:
    from src.research.events import _mark_superseded

    a = catalyst()
    b = catalyst("a2", T0 + timedelta(hours=1))
    assert all(e.is_current for e in _mark_superseded([a, b]))
    unrelated = replace(b, text="Revised separate order")
    assert all(e.is_current for e in _mark_superseded([a, unrelated]))
    linked = replace(b, text="Correction to a1")
    marked = _mark_superseded([a, linked])
    assert marked[0].superseded_by == "a2" and marked[1].updates == ("a1",)


@pytest.mark.parametrize("hour,minute,expected", [(9, 0, 0), (15, 29, 0), (15, 30, 1), (18, 0, 1)])
def test_session_anchor_respects_close_time(hour: int, minute: int, expected: int) -> None:
    dates = [date(2026, 10, 5), date(2026, 10, 6)]
    known = T0.replace(hour=hour, minute=minute)
    assert first_close_after(dates, known) == expected
    assert first_close_after(dates, known.astimezone(UTC)) == expected


def test_after_close_event_cannot_use_already_closed_final_bar() -> None:
    bars = prices()
    known = datetime.combine(bars.last_date, datetime.min.time(), IST).replace(hour=18)
    assert event_study([impact()], {KEY: bars}, horizons=[1], known_at={"a1": known}) == []


def test_benchmark_must_cover_identical_event_study_dates() -> None:
    bars = prices()
    known = datetime.combine(bars.frame.index[5].date(), datetime.min.time(), IST)
    missing = DailySeries("benchmark", bars.frame.drop(bars.frame.index[6]))
    assert (
        event_study(
            [impact()], {KEY: bars}, benchmark=missing, horizons=[1], known_at={"a1": known}
        )
        == []
    )


@pytest.mark.parametrize("missing_rs,uncertain", [(True, False), (False, True)])
def test_missing_or_uncertain_evidence_cannot_confirm(missing_rs: bool, uncertain: bool) -> None:
    ctx = replace(context(), relative_strength={20: None}) if missing_rs else context()
    decision = decide_status(
        impacts=[replace(impact(), uncertain=uncertain)],
        forecast=prediction(),
        context=ctx,
        confirmation=Confirmation.CONFIRMS,
    )
    assert decision.status is not CandidateStatus.CONFIRMED


def test_supported_evidence_confirms_and_conflicting_events_stay_watch() -> None:
    args = dict(forecast=prediction(), context=context(), confirmation=Confirmation.CONFIRMS)
    assert decide_status(impacts=[impact()], **args).status is CandidateStatus.CONFIRMED
    negative = replace(impact("a2"), direction=ImpactDirection.NEGATIVE)
    assert decide_status(impacts=[impact(), negative], **args).status is CandidateStatus.WATCH


def test_publications_are_immutable_and_timezone_normalized(tmp_path: Path) -> None:
    p = prediction()
    with ResearchStore(tmp_path / "events.db") as store:
        store.save_forecasts([p])
        store.save_forecasts([replace(p, p_up=0.2)])
        assert store.unsettled_forecasts()[0]["p_up"] == 0.8
        store.save_forecasts([replace(p, as_of=T0 + timedelta(seconds=1))])
        assert len(store.unsettled_forecasts()) == 2
    assert forecast_id(p) == forecast_id(replace(p, as_of=T0.astimezone(UTC)))
    assert _utc(T0).endswith("+00:00")
    with pytest.raises(ValueError, match="timezone-aware"):
        _utc(T0.replace(tzinfo=None))


def test_event_conditioning_falls_back_explicitly_when_history_is_thin() -> None:
    p = forecast(prices(), as_of=T0, catalyst=catalyst())
    assert p.baseline != "event_analogs"
    assert any("not an event-conditioned probability" in note for note in p.notes)


def test_matching_events_use_only_completed_nonoverlapping_outcomes() -> None:
    bars = prices()
    events = [
        catalyst(str(i), datetime.combine(bars.frame.index[i].date(), datetime.min.time(), IST))
        for i in range(5, 80, 10)
    ]
    events += [catalyst("late", T0 + timedelta(days=1)), catalyst()]
    p = forecast(
        bars, as_of=T0, catalyst=catalyst(), historical_events=events, min_analog_samples=2
    )
    assert p.baseline == "event_analogs" and p.n_samples == 8
    assert p.p_up + p.p_side + p.p_down == pytest.approx(1)
    duplicates = [replace(e, event_id=e.event_id + "-copy") for e in events]
    repeated = forecast(
        bars,
        as_of=T0,
        catalyst=catalyst(),
        historical_events=events + duplicates,
        min_analog_samples=2,
    )
    assert repeated.n_samples == p.n_samples


def test_future_price_bars_do_not_change_historical_forecast() -> None:
    bars = prices()
    earlier = datetime(2026, 8, 3, 10, tzinfo=IST)
    trimmed = DailySeries(KEY, bars.frame.loc[bars.frame.index.date < earlier.date()])
    assert forecast(bars, as_of=earlier) == forecast(trimmed, as_of=earlier)


def test_mature_event_study_and_published_forecast_settlement(tmp_path: Path) -> None:
    from src.research.evaluation import calibration, settle_forecasts

    bars = prices()
    dates = bars.frame.index
    impacts = [impact(f"historical-{i}") for i in range(8)]
    known = {
        item.event_id: datetime.combine(dates[5 + i * 10].date(), datetime.min.time(), IST)
        for i, item in enumerate(impacts)
    }
    benchmark_frame = bars.frame.copy()
    for column in ("open", "high", "low", "close"):
        benchmark_frame[column] = 100 * np.exp(np.arange(len(dates)) * 0.001)
    benchmark = DailySeries("benchmark", benchmark_frame)
    cells = event_study(
        impacts, {KEY: bars}, benchmark=benchmark, horizons=[5], known_at=known, cost_pct=0.001
    )
    assert len(cells) == 1 and cells[0].n == 8
    assert cells[0].mean_abnormal_return == pytest.approx(0.005)
    assert cells[0].mean_net_abnormal_return == pytest.approx(0.004)

    published = forecast(bars, as_of=datetime(2026, 8, 3, 10, tzinfo=IST))
    with ResearchStore(tmp_path / "research.db") as store:
        store.save_forecasts([published])
        assert settle_forecasts(store, {KEY: bars}, benchmark=benchmark, as_of=T0) == 1
        assert settle_forecasts(store, {KEY: bars}, benchmark=benchmark, as_of=T0) == 0
        report = calibration(store.settled_forecasts())
        assert report.n == 1 and report.brier is not None
        assert 0 <= report.brier <= 2
