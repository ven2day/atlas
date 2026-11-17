"""Candidate status: WATCH, SETUP_FORMING, CONFIRMED, INVALIDATED, NO_TRADE.

A small, explicit, deterministic state machine. Every threshold is a named constant here so a
reviewer can read the whole policy in one screen, and every outcome carries the reasons it was
reached plus the three condition lists a card has to show: what would **confirm**, **weaken** or
**invalidate** the thesis.

What this deliberately is not:

* **Not an order.** Nothing here sizes, prices or routes anything, and no state implies that a
  trade should be placed. ``CONFIRMED`` means "the catalyst, the market and the historical
  distribution all point the same way", not "buy".
* **Not a score.** The states are ordered by evidence, not by expected return. A ``CONFIRMED``
  candidate with a small sample is flagged as such rather than quietly promoted.
* **Not forgiving about missing data.** An unsupported asset, a forecast built on synthetic
  prices, or too few historical analogs cannot reach ``CONFIRMED``. The system says so.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from src.research.context import Confirmation, MarketContext, TrendState
from src.research.forecast import Forecast, ForecastStatus
from src.research.mapping import EventImpact, ImpactDirection, ImpactScope

# A CONFIRMED candidate needs at least this many historical analogs behind its distribution...
CONFIRM_MIN_SAMPLES = 30
# ...and the leading outcome must beat the opposite one by at least this much, so a 50.5/49.5
# split is never dressed up as a directional edge.
CONFIRM_PROB_MARGIN = 0.05
# Relative strength that counts as the instrument actually leading its benchmark.
CONFIRM_MIN_RS = 0.0


class CandidateStatus(StrEnum):
    WATCH = "WATCH"
    SETUP_FORMING = "SETUP_FORMING"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True, slots=True)
class StatusDecision:
    status: CandidateStatus
    reasons: tuple[str, ...]
    confirm_if: tuple[str, ...]
    weaken_if: tuple[str, ...]
    invalidate_if: tuple[str, ...]


def _issuer_impacts(impacts: Sequence[EventImpact]) -> list[EventImpact]:
    """The issuer-level impacts, best first: a definite direction beats an uncertain one."""
    issuer = [i for i in impacts if i.scope is ImpactScope.ISSUER]
    return sorted(
        issuer,
        key=lambda i: (i.direction is ImpactDirection.UNKNOWN, i.uncertain, -(i.confidence or 0.0)),
    )


def decide_status(
    *,
    impacts: Sequence[EventImpact],
    forecast: Forecast | None,
    context: MarketContext,
    confirmation: Confirmation,
) -> StatusDecision:
    """The candidate's status from its catalyst, its forecast and its market context."""
    reasons: list[str] = []
    issuer = _issuer_impacts(impacts)

    if not issuer:
        sector_only = [i for i in impacts if i.scope is ImpactScope.SECTOR]
        if sector_only:
            return StatusDecision(
                status=CandidateStatus.WATCH,
                reasons=(
                    "only a sector-level spillover applies to this instrument; no issuer event",
                ),
                confirm_if=("an issuer-specific announcement appears",),
                weaken_if=("the sector spillover is itself downgraded or corrected",),
                invalidate_if=("a contradicting issuer announcement is published",),
            )
        return StatusDecision(
            status=CandidateStatus.NO_TRADE,
            reasons=("no event maps to this instrument",),
            confirm_if=(),
            weaken_if=(),
            invalidate_if=(),
        )

    best = issuer[0]
    if best.asset.value == "unsupported":
        return StatusDecision(
            status=CandidateStatus.NO_TRADE,
            reasons=(
                "this asset has no configured data source or instrument mapping in the "
                "repository, so nothing here can be priced or forecast",
            ),
            confirm_if=(),
            weaken_if=(),
            invalidate_if=(),
        )

    direction = best.direction
    reasons.append(
        f"catalyst: {best.event_id} acts through {best.channel.value}, direction "
        f"{direction.value}, basis {best.basis}" + (", flagged uncertain" if best.uncertain else "")
    )

    invalidate_if = [
        "a later announcement for this issuer supersedes or contradicts this one",
        f"the close moves against the thesis ({direction.value}) with the market agreeing",
    ]
    weaken_if = [
        "the classification confidence drops below the floor on re-reading",
        "market context turns neutral while the catalyst is unchanged",
    ]

    if direction is ImpactDirection.UNKNOWN:
        reasons.append("the direction is not knowable from the event type or its reading")
        return StatusDecision(
            status=CandidateStatus.WATCH,
            reasons=tuple(reasons),
            confirm_if=(
                "the announcement text is classified with a direction above the confidence floor",
                "a follow-up announcement makes the direction explicit",
            ),
            weaken_if=tuple(weaken_if),
            invalidate_if=tuple(invalidate_if),
        )
    if direction is ImpactDirection.MIXED:
        reasons.append("the rule and the classification disagree, so the net direction is mixed")
        return StatusDecision(
            status=CandidateStatus.WATCH,
            reasons=tuple(reasons),
            confirm_if=("evidence resolves the conflict in one direction",),
            weaken_if=tuple(weaken_if),
            invalidate_if=tuple(invalidate_if),
        )

    if confirmation is Confirmation.DISAGREES:
        reasons.append("the market context points against the catalyst")
        if direction is ImpactDirection.POSITIVE:
            # A long thesis the market contradicts is invalidated: the book is long-only, so there
            # is no setup to hold and nothing left to wait for.
            invalidate_if.append("already met: the market disagrees with a positive thesis")
            return StatusDecision(
                status=CandidateStatus.INVALIDATED,
                reasons=tuple(reasons),
                confirm_if=("the trend and relative strength turn to agree with the catalyst",),
                weaken_if=tuple(weaken_if),
                invalidate_if=tuple(invalidate_if),
            )
        # A negative catalyst the market has not acted on is *not* an invalidated thesis - nothing
        # was claimed about price, so nothing has been disproved. In a long-only book it simply
        # offers no entry, and the honest state is WATCH with the disagreement stated.
        reasons.append(
            "the catalyst is negative and the market has not acted on it; with no short book "
            "there is no setup here, only something to watch"
        )
        return StatusDecision(
            status=CandidateStatus.WATCH,
            reasons=tuple(reasons),
            confirm_if=(
                "price action turns to agree with the negative catalyst",
                "the reading is used as a reason to avoid or exit a long, not to open one",
            ),
            weaken_if=tuple(weaken_if),
            invalidate_if=tuple(invalidate_if),
        )

    if forecast is None or forecast.status is ForecastStatus.UNSUPPORTED_DATA:
        why = (
            "no forecast: the price data is simulated or synthetic"
            if forecast is not None
            else "no forecast was produced for this instrument"
        )
        reasons.append(why)
        return StatusDecision(
            status=CandidateStatus.NO_TRADE,
            reasons=tuple(reasons),
            confirm_if=(),
            weaken_if=(),
            invalidate_if=tuple(invalidate_if),
        )

    if forecast.status is ForecastStatus.INSUFFICIENT_EVIDENCE:
        reasons.append(
            f"the forecast has insufficient evidence ({'; '.join(forecast.notes) or 'too few bars'})"
        )
        return StatusDecision(
            status=CandidateStatus.WATCH,
            reasons=tuple(reasons),
            confirm_if=(
                f"at least {forecast.n_unconditional} usable forward returns accumulate, or the "
                "history is extended",
            ),
            weaken_if=tuple(weaken_if),
            invalidate_if=tuple(invalidate_if),
        )

    positive = direction is ImpactDirection.POSITIVE
    p_for = forecast.p_up if positive else forecast.p_down
    p_against = forecast.p_down if positive else forecast.p_up
    reasons.append(
        f"forecast over {forecast.horizon_sessions} session(s) on {forecast.n_samples} "
        f"{forecast.baseline} analogs: q10/q50/q90 = {forecast.q10}/{forecast.q50}/{forecast.q90}"
    )

    confirm_if = [
        f"the leading outcome keeps at least a {CONFIRM_PROB_MARGIN:.0%} margin over the "
        "opposite one as new sessions settle",
        f"relative strength stays above {CONFIRM_MIN_RS:+.4f} against the benchmark",
    ]
    weaken_if.append(
        f"the {forecast.horizon_sessions}-session analog margin narrows below "
        f"{CONFIRM_PROB_MARGIN:.0%}"
    )
    invalidate_if.append(
        "the realized return breaks the forecast's 10th-percentile bound on the thesis side"
    )

    aligned = (
        p_for is not None and p_against is not None and (p_for - p_against) >= CONFIRM_PROB_MARGIN
    )
    enough_samples = forecast.n_samples >= CONFIRM_MIN_SAMPLES
    strong_rs = context.relative_strength.get(20)
    rs_ok = strong_rs is None or strong_rs >= CONFIRM_MIN_RS
    if context.trend is TrendState.UNKNOWN:
        rs_ok = False
        reasons.append("the trend is unknown, so the setup is not confirmed")

    if aligned and enough_samples and rs_ok and confirmation is Confirmation.CONFIRMS:
        reasons.append("the catalyst, the market context and the forecast distribution all agree")
        return StatusDecision(
            status=CandidateStatus.CONFIRMED,
            reasons=tuple(reasons),
            confirm_if=tuple(confirm_if),
            weaken_if=tuple(weaken_if),
            invalidate_if=tuple(invalidate_if),
        )

    if not aligned:
        reasons.append(
            "the historical distribution does not favour the thesis direction by "
            f"{CONFIRM_PROB_MARGIN:.0%}"
        )
    if not enough_samples:
        reasons.append(
            f"only {forecast.n_samples} analogs, below the {CONFIRM_MIN_SAMPLES} needed to confirm"
        )
    if not rs_ok:
        reasons.append("relative strength or trend is not supporting the thesis yet")
    if confirmation is not Confirmation.CONFIRMS:
        reasons.append(f"market context is {confirmation.value}, not confirming")
    return StatusDecision(
        status=CandidateStatus.SETUP_FORMING,
        reasons=tuple(reasons),
        confirm_if=tuple(confirm_if),
        weaken_if=tuple(weaken_if),
        invalidate_if=tuple(invalidate_if),
    )
