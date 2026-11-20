"""The news classifier: rules only conclude what they support, the model leads when configured,
and everything unsure stays explicitly unknown. Hermetic - no model is loaded, no network."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import pytest

from src.decision_models.base import Answer
from src.decision_models.tasks.news import (
    NEWS_RULES_VERSION,
    NewsClassifier,
    NewsPipeline,
    rule_reading,
    unclassified_news,
)
from src.domain.clock import ReplayClock
from src.domain.events import NewsClassified, NewsItemReceived, NewsSymbolMatch
from src.domain.sink import RecordingSink
from src.domain.types import NewsCategory, NewsDirection, NewsHorizon, NewsImpact
from src.marketdata.news import NewsPollStats
from src.utils.market_time import IST

NOW = datetime(2026, 10, 5, 10, 0, tzinfo=IST)
INFY_KEY = "NSE:EQ:INFY"
TCS_KEY = "NSE:EQ:TCS"


def item(
    title: str,
    *,
    summary: str = "",
    news_id: str = "n1",
    symbols: tuple[NewsSymbolMatch, ...] = (
        NewsSymbolMatch(symbol="INFY", instrument_key=INFY_KEY, evidence="test"),
    ),
) -> NewsItemReceived:
    return NewsItemReceived(
        news_id=news_id, source_key="f1", source_name="Feed One", source_type="media_news",
        title=title, summary=summary, url="http://x/1",
        published_at=NOW - timedelta(minutes=10), received_at=NOW,
        symbols=symbols, content_hash="c1",
    )  # fmt: skip


def classifier(cascade: Any = None) -> tuple[NewsClassifier, RecordingSink]:
    clock = ReplayClock(NOW)
    sink = RecordingSink(clock)
    return NewsClassifier(sink=sink, clock=clock, cascade=cascade), sink


# -- the rule table --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("title", "category", "direction", "relevant"),
    [
        (
            "TCS wins a Rs 500 crore contract",
            NewsCategory.CONTRACT_ORDER,
            NewsDirection.BULLISH,
            True,
        ),
        (
            "SEBI imposes penalty on Infosys",
            NewsCategory.REGULATORY_LEGAL,
            NewsDirection.BEARISH,
            True,
        ),
        ("Infosys Q2 results announced", NewsCategory.RESULTS_GUIDANCE, None, True),
        (
            "Infosys declares interim dividend",
            NewsCategory.CORPORATE_ACTION,
            NewsDirection.BULLISH,
            True,
        ),
        (
            "TCS announces 2:1 stock split",
            NewsCategory.CORPORATE_ACTION,
            NewsDirection.NEUTRAL,
            True,
        ),
        ("TCS to acquire SmallCo", NewsCategory.ACQUISITION, NewsDirection.MIXED, True),
        (
            "Rating agency downgrades TCS credit rating",
            NewsCategory.CREDIT_RATING,
            NewsDirection.BEARISH,
            True,
        ),
        (
            "Promoter pledge rises at Infosys",
            NewsCategory.PROMOTER_INSIDER,
            NewsDirection.BEARISH,
            True,
        ),
        ("Board meeting to consider matters", NewsCategory.BOARD_MEETING, None, True),
        ("RBI raises repo rate by 25 bps", NewsCategory.MACRO_POLICY, None, False),
        ("Sensex ends flat; Nifty at record", NewsCategory.MARKET_WIDE, None, False),
        ("Rumor: Infosys in talks for a deal", NewsCategory.RUMOR, None, False),
        ("Infosys opens a new office", NewsCategory.UNKNOWN, None, False),
    ],
)
def test_rules_conclude_only_what_the_wording_supports(title, category, direction, relevant):
    reading = rule_reading(title, "")
    assert reading.category is category
    assert reading.direction is direction  # None means: the rules do NOT decide a direction
    assert reading.relevant is relevant
    if category is NewsCategory.UNKNOWN:
        assert reading.needs_review


def test_results_never_get_an_invented_direction():
    reading = rule_reading("Infosys posts record quarterly results", "")
    assert reading.category is NewsCategory.RESULTS_GUIDANCE
    assert reading.direction is None  # good-or-bad lives in the numbers, not the category
    assert reading.uncertainties  # and the reading says why it cannot tell


# -- the classifier --------------------------------------------------------------------------------


async def test_rule_only_classification_is_labelled_as_such():
    clf, sink = classifier()
    (reading,) = await clf.classify(item("TCS wins a major contract"))
    assert reading.category is NewsCategory.CONTRACT_ORDER
    assert reading.direction is NewsDirection.BULLISH
    assert reading.impact is NewsImpact.UNKNOWN and reading.horizon is NewsHorizon.UNKNOWN
    assert reading.confidence_basis == "rule" and reading.confidence == pytest.approx(0.35)
    assert reading.model == "rule_table" and reading.rules_version == NEWS_RULES_VERSION
    assert reading.classified_at == NOW and reading.relevant is True
    assert "contract" in reading.rationale.lower()
    assert any("headline" in e for e in reading.evidence)
    assert sink.payloads(NewsClassified) == []  # the pipeline emits, not the classifier


async def test_an_unreadable_item_stays_unclear_needs_review():
    clf, _ = classifier()
    (reading,) = await clf.classify(item("Infosys opens a new office"))
    assert reading.category is NewsCategory.UNKNOWN
    assert reading.direction is NewsDirection.UNKNOWN
    assert reading.impact is NewsImpact.UNKNOWN and reading.horizon is NewsHorizon.UNKNOWN
    assert reading.relevant is False and reading.confidence is None
    assert reading.confidence_basis == "none" and reading.model == "none"
    assert reading.rationale.startswith("Unclear - needs review")


async def test_unresolved_symbols_are_never_classified():
    clf, _ = classifier()
    ambiguous = item(
        "Twin Company results",
        symbols=(
            NewsSymbolMatch(
                symbol="twin", instrument_key=None, evidence="ambiguous", resolved=False
            ),
        ),
    )
    assert await clf.classify(ambiguous) == []
    no_symbol = item("Market wrap", symbols=())
    assert await clf.classify(no_symbol) == []


async def test_each_resolved_symbol_gets_its_own_reading():
    clf, _ = classifier()
    both = item(
        "Infosys and TCS both win orders",
        symbols=(
            NewsSymbolMatch(symbol="INFY", instrument_key=INFY_KEY, evidence="title"),
            NewsSymbolMatch(symbol="TCS", instrument_key=TCS_KEY, evidence="title"),
        ),
    )
    readings = await clf.classify(both)
    assert {r.instrument_key for r in readings} == {INFY_KEY, TCS_KEY}


@dataclass
class FakeResult:
    answers: dict[str, Answer] = field(default_factory=dict)
    abstained: tuple[str, ...] = ()
    escalated: dict[str, str] = field(default_factory=dict)


class FakeCascade:
    """Stands in for the Laya->Jev cascade; records the state it was asked about."""

    def __init__(self, answers: dict[str, Answer], abstained: tuple[str, ...] = (),
                 fail: bool = False) -> None:  # fmt: skip
        self.answers, self.abstained, self.fail = answers, abstained, fail
        self.calls: list[tuple[str, dict[str, str]]] = []

    async def decide(self, task: str, state, questions, *, decision_id=None, book_id=None):
        self.calls.append((task, dict(state)))
        if self.fail:
            raise RuntimeError("model down")
        return FakeResult(answers=self.answers, abstained=self.abstained)


def answer(name: str, value, probs=None, confidence=0.8, calibrated=True) -> Answer:
    question = {"relevant": "noul", "impact": "score"}.get(name, "choice")
    return Answer(question, value, probs or {}, confidence, model="laya:multilingual",
                  calibrated=calibrated)  # fmt: skip


async def test_the_model_leads_and_its_basis_is_recorded():
    cascade = FakeCascade(
        {
            "relevant": answer("relevant", True),
            "category": answer("category", "acquisition"),
            "direction": answer("direction", "mixed"),
            "impact": answer("impact", 1, probs={"low": 0.1, "medium": 0.6, "high": 0.3}),
            "horizon": answer("horizon", "swing"),
        }
    )
    clf, _ = classifier(cascade)
    # The rumor rule fires on this headline, but the model's reading leads where it answered.
    (reading,) = await clf.classify(item("Rumor: TCS may explore an acquisition"))
    assert cascade.calls[0][0] == "news"
    assert cascade.calls[0][1]["headline"] == "Rumor: TCS may explore an acquisition"
    assert reading.category is NewsCategory.ACQUISITION  # the model's read, not the rumor rule
    assert reading.direction is NewsDirection.MIXED
    assert reading.impact is NewsImpact.MEDIUM and reading.horizon is NewsHorizon.SWING
    assert reading.confidence_basis == "model_calibrated" and reading.model == "laya+rules"


async def test_an_uncalibrated_model_is_never_presented_as_calibrated():
    cascade = FakeCascade(
        {
            "relevant": answer("relevant", True, calibrated=False),
            "category": answer("category", "contract_order", calibrated=False),
        }
    )
    clf, _ = classifier(cascade)
    (reading,) = await clf.classify(item("TCS wins an order"))
    assert reading.confidence_basis == "model"  # not "model_calibrated"
    assert any("not calibrated" in u for u in reading.uncertainties)


async def test_model_abstention_and_failure_fall_back_to_rules():
    abstaining = FakeCascade({"category": answer("category", "other")}, abstained=("category",))
    clf, _ = classifier(abstaining)
    (reading,) = await clf.classify(item("TCS wins a major contract"))
    assert reading.category is NewsCategory.CONTRACT_ORDER  # the rule floor held
    assert reading.confidence_basis == "rule"

    broken = FakeCascade({}, fail=True)
    clf, _ = classifier(broken)
    (reading,) = await clf.classify(item("SEBI fines Infosys"))
    assert reading.category is NewsCategory.REGULATORY_LEGAL
    assert reading.direction is NewsDirection.BEARISH and reading.confidence_basis == "rule"


# -- the pipeline -----------------------------------------------------------------------------------


class FakeCollector:
    interval_s = 120.0

    def __init__(self, batches: list[list[NewsItemReceived]]) -> None:
        self._batches = batches
        self.forced: list[bool] = []

    async def poll(self, *, force: bool = False) -> NewsPollStats:
        self.forced.append(force)
        new = self._batches.pop(0) if self._batches else []
        return NewsPollStats(ok=True, new=new)


async def test_the_pipeline_classifies_new_items_and_its_backlog():
    collector = FakeCollector([[item("TCS wins an order")]])
    clf, sink = classifier()
    backlog = [item("SEBI fines Infosys", news_id="old")]
    pipeline = NewsPipeline(collector=collector, classifier=clf, backlog=backlog)
    assert pipeline.interval_s == 120.0

    _stats, classified = await pipeline.poll(force=True)
    assert collector.forced == [True]
    assert {c.news_id for c in classified} == {"old", "n1"}  # backlog first-batch included
    emitted = sink.payloads(NewsClassified)
    assert {c.news_id for c in emitted} == {"old", "n1"}  # the pipeline emits the readings

    _stats, classified = await pipeline.poll()
    assert classified == []  # nothing new, backlog consumed


async def test_the_pipeline_survives_a_classification_failure():
    class Exploding(NewsClassifier):
        async def classify(self, announcement):
            raise RuntimeError("bug")

    collector = FakeCollector([[item("TCS wins an order")]])
    clf = Exploding(sink=RecordingSink(ReplayClock(NOW)), clock=ReplayClock(NOW))
    pipeline = NewsPipeline(collector=collector, classifier=clf)
    _stats, classified = await pipeline.poll(force=True)
    assert classified == []  # logged, never raised, collection keeps running


async def test_a_pipeline_without_a_classifier_only_collects():
    collector = FakeCollector([[item("TCS wins an order")]])
    pipeline = NewsPipeline(collector=collector, classifier=None)
    _stats, classified = await pipeline.poll()
    assert classified == []


def test_unclassified_news_finds_the_leftovers():
    stored = [item("a", news_id="n1"), item("b", news_id="n2")]
    left = unclassified_news(stored, ["n1"])
    assert [i.news_id for i in left] == ["n2"]
