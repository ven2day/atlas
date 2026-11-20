"""Interpreting news items for trading research (categories, direction, impact, horizon).

Two layers, mirroring the announcement classifier's split:

* **A deterministic rule table** (:data:`RULES`) that only ever concludes what the rules actually
  support: a category from unambiguous wording, and a direction only where the repository's own
  event-mapping conventions back it (an order win is positive, a penalty is negative, a rumor is
  *unknown*). Impact and horizon are almost never rule-decidable and stay ``unknown`` rather than
  being guessed.
* **The configured decision-model cascade** (the same Laya→Jev infrastructure the announcements
  use, task ``"news"``) when one is available. The model leads; the rules are the floor for
  anything it abstains on. No new provider or credential is introduced.

Everything else stays honest: ``confidence_basis`` names where a confidence number came from (a
rule prior is not a calibrated probability), an item no rule or model can place is labelled
``"Unclear - needs review"`` with every field ``unknown``, and only items with a *resolved*
symbol match are classified per instrument - an ambiguous mention is never attributed.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol

from src.decision_models.base import Answer, Question
from src.domain.clock import Clock
from src.domain.events import NewsClassified, NewsItemReceived
from src.domain.sink import EventSink
from src.domain.types import NewsCategory, NewsDirection, NewsHorizon, NewsImpact
from src.marketdata.news import NewsPollStats

logger = logging.getLogger(__name__)

TASK = "news"
NEWS_RULES_VERSION = "news-rules-v1"
# A rule reading's standing confidence: a prior from wording, explicitly NOT calibrated.
RULE_CONFIDENCE = 0.35
UNCLEAR_RATIONALE = "Unclear - needs review: no rule or model reading supports a conclusion."

QUESTIONS: Mapping[str, Question] = {
    "relevant": Question(
        "noul",
        "Could this news item move the price of the matched Indian listed company's shares?",
        {"false": "routine coverage or not company-specific", "true": "a possible price catalyst"},
    ),
    "category": Question(
        "choice",
        "What kind of news event is this for the matched company?",
        {c.value: c.value.replace("_", " ") for c in NewsCategory},
    ),
    "direction": Question(
        "choice",
        "What is the likely effect on the company's share price?",
        {
            "bullish": "good news for shareholders",
            "bearish": "bad news for shareholders",
            "mixed": "carries both positive and negative effects",
            "neutral": "no material effect",
            "unknown": "cannot tell from the item",
        },
    ),
    "impact": Question(
        "score", "How large is the likely impact on the company?", ("low", "medium", "high")
    ),  # fmt: skip
    "horizon": Question(
        "choice",
        "Over what horizon would the effect most likely play out?",
        {
            "intraday": "within the session",
            "sessions_1_3": "one to three sessions",
            "swing": "more than three sessions",
            "unknown": "cannot tell",
        },
    ),
}

IMPACT_LEVELS = ("low", "medium", "high")


@dataclass(frozen=True)
class RuleReading:
    """What the rule table concluded - and, just as important, what it did not."""

    category: NewsCategory
    direction: NewsDirection | None = None  # None = the rules do not decide a direction
    relevant: bool = False
    matched: str = ""  # the wording that fired, quoted in the rationale
    note: str = ""  # why the rule reads it this way
    uncertainties: tuple[str, ...] = ()
    needs_review: bool = False


@dataclass(frozen=True)
class _Rule:
    pattern: re.Pattern[str]
    reading: RuleReading


# Order matters: the first match wins. Rumor is checked first so an unconfirmed report about an
# order win never inherits the order-win direction.
RULES: tuple[_Rule, ...] = (
    _Rule(
        re.compile(
            r"\brumou?rs?\b|\bspeculat\w*|\breportedly\b|\bin talks\b|\bsaid to\b|"
            r"\bmay explore\b|\bexploring options\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.RUMOR,
            None,
            False,
            "rumor/speculative wording",
            "unconfirmed reporting: the rules never assign a direction to a rumor",
            ("The item is not confirmed by the company or the exchange.",),
        ),
    ),
    _Rule(
        re.compile(
            r"\b(wins?|bags?|bagged|secures?|awarded|gets)\b[^.]{0,60}\b(order|contract|"
            r"deal|project)\b|\bnew contract\b|\border win\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.CONTRACT_ORDER,
            NewsDirection.BULLISH,
            True,
            "order/contract win",
            "the repository's event-mapping convention reads an order win as positive for "
            "the issuer; size is unknown from the headline, so impact stays unknown",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(sebi|enforcement directorate|\bed\b raid|\bcbi\b|penalt\w*|fined\b|"
            r"lawsuit|litigation|investigation|show-?cause|banned\b|fraud)\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.REGULATORY_LEGAL,
            NewsDirection.BEARISH,
            True,
            "regulatory/legal action wording",
            "the mapping convention reads regulatory or legal action as negative; "
            "severity is not judgeable from a headline",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(downgrade[ds]?|rating cut)\b[^.]{0,40}\b(rating|credit)?\b|"
            r"\bcredit rating\b[^.]{0,40}\b(downgrade|cut)\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.CREDIT_RATING,
            NewsDirection.BEARISH,
            True,
            "rating downgrade",
            "a downgrade is negative by the mapping convention",
        ),
    ),
    _Rule(
        re.compile(r"\b(upgrade[ds]?)\b[^.]{0,40}\brating\b|\brating\b[^.]{0,40}\b(upgrade)", re.I),
        RuleReading(
            NewsCategory.CREDIT_RATING,
            NewsDirection.BULLISH,
            True,
            "rating upgrade",
            "an upgrade is positive by the mapping convention",
        ),
    ),
    _Rule(
        re.compile(r"\bcredit rating\b|\braises? .*rating\b|\baffirms rating\b", re.I),
        RuleReading(
            NewsCategory.CREDIT_RATING,
            None,
            True,
            "credit rating wording",
            "a rating action whose direction the wording does not state",
        ),
    ),
    _Rule(
        re.compile(r"\b(dividend|record date|buyback|buy-back)\b", re.I),
        RuleReading(
            NewsCategory.CORPORATE_ACTION,
            NewsDirection.BULLISH,
            True,
            "dividend/buyback wording",
            "dividends and buybacks are positive by the mapping convention; "
            "splits and bonuses are treated as neutral and are NOT folded in here",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(bonus issue|stock split|sub-division of (equity )?shares|face value)\b", re.I
        ),
        RuleReading(
            NewsCategory.CORPORATE_ACTION,
            NewsDirection.NEUTRAL,
            True,
            "split/bonus wording",
            "splits and bonuses are liquidity events: neutral by the mapping convention",
        ),
    ),
    _Rule(
        re.compile(r"\bpledge[ds]?\b|\bencumbrance\b|\bmargin call\b", re.I),
        RuleReading(
            NewsCategory.PROMOTER_INSIDER,
            NewsDirection.BEARISH,
            True,
            "pledge wording",
            "pledging is negative by the mapping convention",
        ),
    ),
    _Rule(
        re.compile(r"\b(promoter|insider|sast|stake sale|stake buy|frees?ale)\b", re.I),
        RuleReading(
            NewsCategory.PROMOTER_INSIDER,
            None,
            True,
            "promoter/insider wording",
            "the direction lives in the text (buying vs selling), which the rules do "
            "not parse: it stays unknown rather than guessed",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(acquir\w*|acquisition|merger|amalgamation|takeover|scheme of arrangement)\b", re.I
        ),
        RuleReading(
            NewsCategory.ACQUISITION,
            NewsDirection.MIXED,
            True,
            "M&A wording",
            "acquisitions carry both effects (synergy vs dilution/integration risk), "
            "so the rules read them as mixed, never as one-sided",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(financial results|quarterly results|annual results|\bq[1-4]\b[^.]{0,40}"
            r"(results|pat|revenue|earnings)|earnings (call|release)|guidance|profit "
            r"(warning|alert)|pat\b|net profit)\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.RESULTS_GUIDANCE,
            None,
            True,
            "results/guidance wording",
            "whether results are good or bad is in the numbers, not the category: "
            "the rules never infer a direction for results",
            ("The result's direction needs the actual figures; this reading does not have them.",),
        ),
    ),
    _Rule(
        re.compile(r"\bboard (of directors )?(meeting|to consider)|\bmeeting of the board\b", re.I),
        RuleReading(
            NewsCategory.BOARD_MEETING,
            None,
            True,
            "board meeting wording",
            "a meeting's outcome is unknown until it happens",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(rbi|repo rate|reverse repo|cpi|inflation|gdp|budget|fiscal|monetary "
            r"policy|fomc|fed rate|sbi caps|government (announces|approves))\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.MACRO_POLICY,
            None,
            False,
            "macro/policy wording",
            "macro news is not attributable to one instrument by rules alone",
        ),
    ),
    _Rule(
        re.compile(
            r"\b(sensex|nifty|market wrap|markets? (open|close|end|slip|rally)|"
            r"benchmarks?|broad market)\b",
            re.I,
        ),
        RuleReading(
            NewsCategory.MARKET_WIDE,
            None,
            False,
            "market-wide wording",
            "market-wide coverage carries no issuer-specific conclusion",
        ),
    ),
)

_ACTIONABLE = frozenset(
    {
        NewsCategory.RESULTS_GUIDANCE,
        NewsCategory.CONTRACT_ORDER,
        NewsCategory.ACQUISITION,
        NewsCategory.CORPORATE_ACTION,
        NewsCategory.BOARD_MEETING,
        NewsCategory.REGULATORY_LEGAL,
        NewsCategory.CREDIT_RATING,
        NewsCategory.PROMOTER_INSIDER,
    }
)


def rule_reading(title: str, summary: str) -> RuleReading:
    """The first rule the wording supports, or an explicit needs-review reading."""
    text = f"{title} {summary}"
    for rule in RULES:
        if rule.pattern.search(text):
            return rule.reading
    return RuleReading(
        NewsCategory.UNKNOWN,
        None,
        False,
        "",
        "",
        ("No rule matched the wording.",),
        needs_review=True,
    )


def _direction_from_choice(value: object) -> NewsDirection | None:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("unclear", ""):
            return None
        try:
            return NewsDirection(lowered)
        except ValueError:
            return None
    return None


def _impact_from_answer(answer: Answer | None) -> NewsImpact | None:
    if answer is None:
        return None
    probs = {k: v for k, v in answer.probabilities.items() if k in IMPACT_LEVELS}
    if probs:
        return NewsImpact(max(probs, key=lambda k: probs[k]))
    if isinstance(answer.value, int | float) and not isinstance(answer.value, bool):
        index = min(len(IMPACT_LEVELS) - 1, max(0, round(float(answer.value))))
        return NewsImpact(IMPACT_LEVELS[index])
    if isinstance(answer.value, str) and answer.value in IMPACT_LEVELS:
        return NewsImpact(answer.value)
    return None


def _horizon_from_choice(value: object) -> NewsHorizon | None:
    if isinstance(value, str):
        try:
            return NewsHorizon(value.strip().lower())
        except ValueError:
            return None
    return None


def _category_from_choice(value: object) -> NewsCategory | None:
    if isinstance(value, str):
        try:
            return NewsCategory(value.strip().lower())
        except ValueError:
            return None
    return None


class NewsCascade(Protocol):
    async def decide(
        self,
        task: str,
        state: Mapping[str, str],
        questions: Mapping[str, Question],
        *,
        decision_id: str | None = None,
        book_id: str | None = None,
    ) -> object: ...


class NewsClassifier:
    """Classifies one news item per *resolved* symbol match. Unresolved mentions are skipped:
    an ambiguous company match must never become a signal for some instrument."""

    def __init__(
        self,
        *,
        sink: EventSink,
        clock: Clock,
        cascade: NewsCascade | None = None,
        max_per_item: int = 3,
    ) -> None:
        self._sink = sink
        self._clock = clock
        self._cascade = cascade
        self._max = max_per_item

    async def classify(self, item: NewsItemReceived) -> list[NewsClassified]:
        targets = [m for m in item.symbols if m.resolved and m.instrument_key]
        if not targets:
            return []  # unresolved or symbol-less: it stays visible as unclassified in the UI
        answers: Mapping[str, Answer] = {}
        abstained: tuple[str, ...] = ()
        if self._cascade is not None:
            try:
                result = await self._cascade.decide(
                    TASK,
                    {
                        "headline": item.title,
                        "summary": item.summary or "(none)",
                        "source_type": item.source_type.replace("_", " "),
                    },
                    QUESTIONS,
                )
                answers = getattr(result, "answers", {}) or {}
                abstained = tuple(getattr(result, "abstained", ()) or ())
            except Exception:
                logger.exception("the news decision model failed on %s; rules only", item.news_id)
        reading = rule_reading(item.title, item.summary)
        out: list[NewsClassified] = []
        for match in targets[: self._max]:
            assert match.instrument_key is not None
            out.append(self._build(item, match.symbol, match.instrument_key, reading,
                                   answers, abstained))  # fmt: skip
        return out

    def _build(
        self,
        item: NewsItemReceived,
        symbol: str,
        instrument_key: str,
        reading: RuleReading,
        answers: Mapping[str, Answer],
        abstained: tuple[str, ...],
    ) -> NewsClassified:
        # An abstained question has no answer to use: discarding it here means the rule floor
        # decides that field, and an abstention can never masquerade as a model reading.
        answers = {k: v for k, v in answers.items() if k not in set(abstained)}
        model_category = (
            _category_from_choice(answers["category"].value) if "category" in answers else None
        )
        model_direction = (
            _direction_from_choice(answers["direction"].value) if "direction" in answers else None
        )
        model_impact = _impact_from_answer(answers.get("impact"))
        model_horizon = (
            _horizon_from_choice(answers["horizon"].value) if "horizon" in answers else None
        )
        model_relevant = bool(answers["relevant"].value) if "relevant" in answers else None

        category = model_category or reading.category
        direction = model_direction or reading.direction or NewsDirection.UNKNOWN
        impact = model_impact or NewsImpact.UNKNOWN
        horizon = model_horizon or NewsHorizon.UNKNOWN

        used_model = bool(answers)
        if used_model:
            calibrated = all(a.calibrated for a in answers.values()) and bool(answers)
            basis = "model_calibrated" if calibrated else "model"
            confidences = [a.confidence for a in answers.values() if a.confidence is not None]
            confidence = min(confidences) if confidences else None
            models = sorted({a.model.split(":")[0] for a in answers.values() if a.model})
            model = (
                "+".join([*models, "rules"])
                if reading.category is not NewsCategory.UNKNOWN
                else "+".join(models) or "none"
            )
        elif reading.needs_review:
            basis, confidence, model = "none", None, "none"
        else:
            basis, confidence, model = "rule", RULE_CONFIDENCE, "rule_table"

        if reading.needs_review and not used_model:
            relevant = False
            rationale = UNCLEAR_RATIONALE
        else:
            relevant = (
                model_relevant
                if model_relevant is not None
                else (reading.relevant and category in _ACTIONABLE)
            )
            parts = []
            if reading.matched:
                parts.append(
                    f"Rules: the wording ({reading.matched}) places this in '{category.value}'."
                )
                if reading.note:
                    parts.append(reading.note + ".")
            if used_model:
                parts.append(
                    f"Model reading: category '{category.value}', direction "
                    f"'{direction.value}', impact '{impact.value}', horizon "
                    f"'{horizon.value}' (basis: {basis})."
                )
            if direction is NewsDirection.UNKNOWN:
                parts.append("No rule or model reading supports a direction, so it stays unknown.")
            rationale = " ".join(parts) or UNCLEAR_RATIONALE

        evidence = [f"headline: {item.title[:200]}"]
        if item.summary:
            evidence.append(f"summary: {item.summary[:300]}")
        evidence.append(f"source: {item.source_name} ({item.source_type.replace('_', ' ')})")
        uncertainties = list(reading.uncertainties)
        if abstained:
            uncertainties.append(f"the model abstained on: {', '.join(sorted(abstained))}")
        if basis in ("rule", "none") and self._cascade is not None:
            uncertainties.append("no model reading was available for this item")
        if basis == "model":
            uncertainties.append("the model's confidence is not calibrated for this task")
        return NewsClassified(
            news_id=item.news_id,
            instrument_key=instrument_key,
            symbol=symbol,
            category=category,
            direction=direction,
            impact=impact,
            horizon=horizon,
            confidence=confidence,
            confidence_basis=basis,
            rationale=rationale,
            evidence=tuple(evidence),
            uncertainties=tuple(uncertainties),
            relevant=relevant,
            model=model or "none",
            rules_version=NEWS_RULES_VERSION,
            classified_at=self._clock.now(),
        )


class NewsPipeline:
    """The engine's ``news`` task: poll the registry's feeds, then classify what resolved.

    Classification failures are logged, never raised, and never hold back collection - the item
    stays in the archive as unclassified rather than being dropped. A ``backlog`` of stored but
    unclassified items (e.g. the process stopped in between) is read on the first poll."""

    def __init__(
        self,
        *,
        collector: object,
        classifier: NewsClassifier | None,
        backlog: Iterable[NewsItemReceived] = (),
    ) -> None:
        self._collector = collector
        self._classifier = classifier
        self._backlog = list(backlog)

    @property
    def interval_s(self) -> float:
        return float(getattr(self._collector, "interval_s", 300.0))

    async def poll(self, *, force: bool = False) -> tuple[NewsPollStats, list[NewsClassified]]:
        poll: NewsPollStats = await self._collector.poll(force=force)  # type: ignore[attr-defined]
        pending, self._backlog = [*self._backlog, *poll.new], []
        return poll, await self.classify_all(pending)

    async def classify_all(self, items: Iterable[NewsItemReceived]) -> list[NewsClassified]:
        if self._classifier is None:
            return []
        out: list[NewsClassified] = []
        for item in items:
            try:
                classified = await self._classifier.classify(item)
            except Exception:
                logger.exception("classifying news %s failed", item.news_id)
                continue
            for reading in classified:
                self._classifier._sink.emit(reading, source="news")
            out.extend(classified)
        return out


def unclassified_news(
    stored: Iterable[NewsItemReceived], classified_ids: Iterable[str]
) -> list[NewsItemReceived]:
    """Items stored but never classified (e.g. the process stopped in between)."""
    done = set(classified_ids)
    return [item for item in stored if item.news_id not in done]
