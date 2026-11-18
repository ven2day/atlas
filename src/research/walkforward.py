"""Walk-forward evaluation of three forecasters over the same observations.

The discipline this module exists to enforce:

* **An origin only sees its own past.** At each forecast origin, training outcomes are those whose
  window *fully completed* before the origin, minus an embargo. Because a completed window ended
  before the origin, it cannot overlap the window being forecast, which is the purge.
* **Forecasts are frozen.** A publication is written once, at its origin, and only ever gains an
  outcome later. Nothing recomputes a past probability with later knowledge.
* **Three approaches, one set of observations.** Unconditional, trend/volatility-technical and
  event-conditioned forecasts are all published for the same (event, origin, horizon), so their
  scores are paired and comparable. Comparing models on different subsets is how a worse model
  wins.
* **Issuers are not silently pooled.** The event-conditioned cohort is
  ``issuer|type|direction|materiality``. Pooling across issuers is a *separate, named* approach
  (``pooled_event``) that must be switched on explicitly, because it buys sample size by assuming
  issuers are interchangeable - an assumption, not a fact.
* **Overlapping windows are handled, not ignored.** Two events on one instrument whose outcome
  windows overlap are not independent observations; within a training pool the later one is
  dropped and the count is reported.
* **Tuning is chronological and separate from the holdout.** Origins are split in time; parameter
  selection sees only the earlier part, and the runner refuses to score a holdout fold with
  parameters chosen on it.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from typing import Any

import numpy as np

from src.research.outcomes import EventOutcome

QUANTILES = (0.10, 0.50, 0.90)


class Approach(StrEnum):
    UNCONDITIONAL = "unconditional"
    TECHNICAL = "technical"
    EVENT_CONDITIONED = "event_conditioned"
    # Opt-in and separately named: pools issuers, which is an assumption, not a measurement.
    POOLED_EVENT = "pooled_event"


# Compared in this order; the first with enough completed, non-overlapping training outcomes wins.
FALLBACK_ORDER: tuple[Approach, ...] = (
    Approach.EVENT_CONDITIONED,
    Approach.TECHNICAL,
    Approach.UNCONDITIONAL,
)


@dataclass(frozen=True, slots=True)
class WalkForwardConfig:
    """Frozen with every publication, so a result can be reproduced or dismissed later."""

    horizons: tuple[int, ...]
    min_train_samples: int = 20
    embargo_sessions: int = 1
    holdout_fraction: float = 0.25
    allow_pooling: bool = False
    classifier_version: str = ""
    rules_version: str = ""
    code_version: str = ""
    evaluation_mode: str = "reconstructed"

    def fingerprint(self) -> str:
        """A stable id for this exact configuration."""
        payload = (
            f"{','.join(str(h) for h in self.horizons)}|{self.min_train_samples}|"
            f"{self.embargo_sessions}|{self.holdout_fraction}|{self.allow_pooling}|"
            f"{self.classifier_version}|{self.rules_version}|{self.code_version}|"
            f"{self.evaluation_mode}"
        )
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizons": list(self.horizons),
            "min_train_samples": self.min_train_samples,
            "embargo_sessions": self.embargo_sessions,
            "holdout_fraction": self.holdout_fraction,
            "allow_pooling": self.allow_pooling,
            "classifier_version": self.classifier_version,
            "rules_version": self.rules_version,
            "code_version": self.code_version,
            "evaluation_mode": self.evaluation_mode,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True, slots=True)
class Fold:
    index: int
    origin: date
    train_cutoff: date  # training outcomes must complete strictly before this
    is_holdout: bool


@dataclass(frozen=True, slots=True)
class Publication:
    """One frozen forecast. Immutable after writing; only the outcome is added later."""

    publication_id: str
    run_id: str
    fold: int
    origin_date: date
    record_id: str
    instrument_key: str
    horizon_sessions: int
    approach: str  # the approach actually used, after any fallback
    requested_approach: str  # the approach asked for, so a fallback is visible
    cohort: str
    p_up: float | None
    p_side: float | None
    p_down: float | None
    q10: float | None
    q50: float | None
    q90: float | None
    n_train: int
    dropped_overlapping: int
    fallback_used: bool
    is_holdout: bool
    configuration: Mapping[str, Any]
    published_at: datetime
    notes: tuple[str, ...] = ()

    def as_row(self) -> Mapping[str, object]:
        return {
            "publication_id": self.publication_id,
            "run_id": self.run_id,
            "fold": self.fold,
            "origin_date": self.origin_date,
            "record_id": self.record_id,
            "instrument_key": self.instrument_key,
            "horizon_sessions": self.horizon_sessions,
            "approach": self.approach,
            "cohort": self.cohort,
            "p_up": self.p_up,
            "p_side": self.p_side,
            "p_down": self.p_down,
            "q10": self.q10,
            "q50": self.q50,
            "q90": self.q90,
            "n_train": self.n_train,
            "fallback_used": self.fallback_used,
            "configuration": dict(self.configuration),
            "published_at": self.published_at,
        }


def plan_folds(
    origins: Sequence[date],
    *,
    embargo_sessions: int = 1,
    holdout_fraction: float = 0.25,
    sessions_to_days: Mapping[date, int] | None = None,
) -> list[Fold]:
    """Chronological folds: the last ``holdout_fraction`` of origins are the holdout.

    ``train_cutoff`` is the origin pushed back by the embargo, so a training window that ends
    inside the embargo is excluded. Without a session map the embargo is applied in calendar days,
    which is conservative (it purges more, never less).
    """
    if not 0.0 <= holdout_fraction < 1.0:
        raise ValueError("holdout_fraction must be in [0, 1)")
    ordered = sorted(set(origins))
    if not ordered:
        return []
    holdout_from = len(ordered) - max(1, int(round(len(ordered) * holdout_fraction)))
    embargo = max(0, embargo_sessions)
    folds: list[Fold] = []
    for index, origin in enumerate(ordered):
        if sessions_to_days is not None:
            cutoff = origin - timedelta(days=sessions_to_days.get(origin, embargo))
        else:
            cutoff = origin - timedelta(days=embargo)
        folds.append(Fold(index, origin, cutoff, index >= holdout_from))
    return folds


def cohort_of(outcome: EventOutcome, fields: Mapping[str, str | None]) -> str:
    """The event-conditioned cohort: issuer plus the classification fields it conditions on."""
    parts = [
        outcome.instrument_key,
        str(fields.get("event_type") or "unclassified"),
        str(fields.get("direction") or "unknown"),
        str(fields.get("materiality") or "unrated"),
    ]
    return "|".join(parts)


def pooled_cohort_of(outcome: EventOutcome, fields: Mapping[str, str | None]) -> str:
    """The pooled cohort: the issuer is deliberately dropped. Only used by ``pooled_event``."""
    return "|".join(
        [
            "POOLED",
            str(fields.get("event_type") or "unclassified"),
            str(fields.get("direction") or "unknown"),
            str(fields.get("materiality") or "unrated"),
        ]
    )


def _non_overlapping(
    outcomes: Sequence[EventOutcome],
) -> tuple[list[EventOutcome], int]:
    """Greedily keep outcomes whose [anchor, outcome] windows do not overlap, per instrument.

    Overlapping windows on one instrument share price path, so counting them all would overstate
    the sample and understate the variance. The dropped count is reported, never hidden.
    """
    by_instrument: dict[str, list[EventOutcome]] = {}
    for outcome in outcomes:
        by_instrument.setdefault(outcome.instrument_key, []).append(outcome)
    kept: list[EventOutcome] = []
    dropped = 0
    for group in by_instrument.values():
        last_end: date | None = None
        for outcome in sorted(group, key=lambda o: (o.anchor_date or date.min, o.outcome_id)):
            if outcome.anchor_date is None or outcome.outcome_date is None:
                continue
            if last_end is not None and outcome.anchor_date <= last_end:
                dropped += 1
                continue
            kept.append(outcome)
            last_end = outcome.outcome_date
    return kept, dropped


def train_pool(
    outcomes: Iterable[EventOutcome],
    *,
    fold: Fold,
    horizon: int,
    cohort: str | None = None,
    cohort_of_outcome: Any = None,
    technical_state: Mapping[date, tuple[str, str]] | None = None,
    origin_state: tuple[str, str] | None = None,
) -> tuple[np.ndarray, int]:
    """Completed, purged, non-overlapping training abnormal returns for one fold.

    An outcome is eligible only if it settled, matches the horizon, and finished strictly before
    the fold's ``train_cutoff`` - which is the origin minus the embargo. That single condition is
    what keeps a training label from crossing into the period being forecast.
    """
    eligible = [
        o
        for o in outcomes
        if o.is_settled
        and o.horizon_sessions == horizon
        and o.abnormal_return is not None
        and o.outcome_date is not None
        and o.outcome_date < fold.train_cutoff
    ]
    if cohort is not None and cohort_of_outcome is not None:
        eligible = [o for o in eligible if cohort_of_outcome(o) == cohort]
    if technical_state is not None and origin_state is not None:
        eligible = [
            o
            for o in eligible
            if o.anchor_date is not None and technical_state.get(o.anchor_date) == origin_state
        ]
    kept, dropped = _non_overlapping(eligible)
    observed: list[float] = []
    for item in kept:
        if item.abnormal_return is not None:
            observed.append(float(item.abnormal_return))
    return np.asarray(observed, dtype=float), dropped


def summarize_pool(
    values: np.ndarray, sideways_band: float | None
) -> tuple[float | None, float | None, float | None, float | None, float | None, float | None]:
    """p(up)/p(side)/p(down) and q10/q50/q90 of a training pool. ``None``s when it is empty."""
    if values.size == 0:
        return (None,) * 6
    if sideways_band is None or sideways_band <= 0 or not math.isfinite(sideways_band):
        p_up = float(np.mean(values > 0))
        p_down = float(np.mean(values < 0))
    else:
        p_up = float(np.mean(values > sideways_band))
        p_down = float(np.mean(values < -sideways_band))
    p_side = max(0.0, 1.0 - p_up - p_down)
    q10, q50, q90 = (float(np.quantile(values, q)) for q in QUANTILES)
    return (
        round(p_up, 6),
        round(p_side, 6),
        round(p_down, 6),
        round(q10, 6),
        round(q50, 6),
        round(q90, 6),
    )


def publish(
    *,
    run_id: str,
    fold: Fold,
    outcome_ref: EventOutcome,
    requested: Approach,
    cohort: str,
    pools: Mapping[Approach, tuple[np.ndarray, int]],
    sideways_band: float | None,
    config: WalkForwardConfig,
    published_at: datetime,
    min_train_samples: int | None = None,
) -> Publication:
    """Freeze one forecast: walk the fallback order and record which level was actually used."""
    minimum = config.min_train_samples if min_train_samples is None else min_train_samples
    order = [requested, *(a for a in FALLBACK_ORDER if a is not requested)]
    if requested is Approach.POOLED_EVENT:
        order = [Approach.POOLED_EVENT, *(a for a in FALLBACK_ORDER)]
    used = order[0]
    values, dropped = pools.get(used, (np.asarray([], dtype=float), 0))
    notes: list[str] = []
    fallback = False
    for candidate in order:
        candidate_values, candidate_dropped = pools.get(candidate, (np.asarray([], dtype=float), 0))
        if candidate_values.size >= minimum:
            used, values, dropped = candidate, candidate_values, candidate_dropped
            fallback = candidate is not requested
            break
    else:
        notes.append(
            f"no approach reached {minimum} training observations; published on "
            f"{values.size} from {used.value} and labelled as such"
        )
        fallback = used is not requested
    if fallback:
        notes.append(
            f"{requested.value} had fewer than {minimum} completed, non-overlapping training "
            f"outcomes; fell back to {used.value}"
        )
    if dropped:
        notes.append(f"{dropped} training outcome(s) dropped for overlapping windows")
    p_up, p_side, p_down, q10, q50, q90 = summarize_pool(values, sideways_band)
    publication_id = hashlib.sha256(
        f"{run_id}|{fold.index}|{outcome_ref.record_id}|{outcome_ref.horizon_sessions}|"
        f"{requested.value}|{config.fingerprint()}".encode()
    ).hexdigest()[:24]
    return Publication(
        publication_id=publication_id,
        run_id=run_id,
        fold=fold.index,
        origin_date=fold.origin,
        record_id=outcome_ref.record_id,
        instrument_key=outcome_ref.instrument_key,
        horizon_sessions=outcome_ref.horizon_sessions,
        approach=used.value,
        requested_approach=requested.value,
        cohort=cohort,
        p_up=p_up,
        p_side=p_side,
        p_down=p_down,
        q10=q10,
        q50=q50,
        q90=q90,
        n_train=int(values.size),
        dropped_overlapping=dropped,
        fallback_used=fallback,
        is_holdout=fold.is_holdout,
        configuration=config.as_dict(),
        published_at=published_at,
        notes=tuple(notes),
    )
