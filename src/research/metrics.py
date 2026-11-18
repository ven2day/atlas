"""Metrics and uncertainty for walk-forward research evaluation.

Two choices here are deliberate and easy to get wrong:

**Observations are not independent.** Event outcomes measured over 5, 10 or 20 sessions on
overlapping dates share price path, so an i.i.d. bootstrap understates the interval - often by
enough to turn noise into a "significant" result. Uncertainty is therefore quantified with a
*moving-block* bootstrap over observations ordered by origin, with a block length at least the
longest horizon, and the assumption is printed in every report rather than buried.

**A small sample is not a result.** Below :data:`MIN_REPORT_SAMPLES` a metric is reported with its
count and withheld as a percentage; below :data:`MIN_PROMOTION_SAMPLES` a paired comparison cannot
declare a winner at all. Nothing is promoted because three observations looked good.

**No after-cost performance is reported.** That would require an explicit entry/exit execution
policy with fills, slippage and latency. This module measures *forecast* quality (calibration,
interval coverage, abnormal-return reaction) and says so. Executable strategy performance belongs
to :mod:`src.backtesting.edge` and its 200-trade / 95% CI gate.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np

from src.research.evaluation import CalibrationCell
from src.research.walkforward import Approach, Publication

MIN_CELL_SAMPLES = 8  # below this, counts are shown but percentages are withheld
MIN_REPORT_SAMPLES = 20  # below this, no interval or claim of difference is reported
MIN_PROMOTION_SAMPLES = 50  # below this, a paired comparison cannot name a winner
DEFAULT_BOOTSTRAP_REPLICATES = 2000
DEFAULT_CI = 0.95
BUCKETS = ("up", "side", "down")
_PROBABILITY_KEYS = {"up": "p_up", "side": "p_side", "down": "p_down"}


@dataclass(frozen=True, slots=True)
class Metrics:
    approach: str
    n: int
    brier: float | None
    brier_ci: tuple[float, float] | None
    reliability: tuple[CalibrationCell, ...]
    interval_coverage: float | None  # share of outcomes landing inside [q10, q90]
    interval_width: float | None  # mean q90 - q10, in log-return units
    share_up: float | None
    share_side: float | None
    share_down: float | None
    mean_abnormal_return: float | None
    mean_abnormal_ci: tuple[float, float] | None
    median_abnormal_return: float | None
    fallback_count: int
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "approach": self.approach,
            "n": self.n,
            "brier": self.brier,
            "brier_ci": list(self.brier_ci) if self.brier_ci else None,
            "reliability": [
                {"bucket": c.bucket, "n": c.n, "predicted": c.predicted, "realized": c.realized}
                for c in self.reliability
            ],
            "interval_coverage": self.interval_coverage,
            "interval_width": self.interval_width,
            "share_up": self.share_up,
            "share_side": self.share_side,
            "share_down": self.share_down,
            "mean_abnormal_return": self.mean_abnormal_return,
            "mean_abnormal_ci": list(self.mean_abnormal_ci) if self.mean_abnormal_ci else None,
            "median_abnormal_return": self.median_abnormal_return,
            "fallback_count": self.fallback_count,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class PairedComparison:
    """Difference in per-observation Brier loss against a baseline, over shared observations."""

    approach: str
    baseline: str
    n_paired: int
    mean_loss_difference: float | None  # negative = the approach loses less, i.e. is better
    ci: tuple[float, float] | None
    wins: float | None  # share of bootstrap replicates where the approach is better
    verdict: str
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "approach": self.approach,
            "baseline": self.baseline,
            "n_paired": self.n_paired,
            "mean_loss_difference": self.mean_loss_difference,
            "ci": list(self.ci) if self.ci else None,
            "wins": self.wins,
            "verdict": self.verdict,
            "notes": list(self.notes),
        }


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    run_id: str
    evaluation_mode: str
    created_at: datetime
    configuration: Mapping[str, Any]
    totals: Mapping[str, int]
    cohort_sizes: Mapping[str, int]
    by_approach: Mapping[str, Metrics]
    comparisons: tuple[PairedComparison, ...]
    fallback_rate: float | None
    notes: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "evaluation_mode": self.evaluation_mode,
            "created_at": self.created_at.isoformat(),
            "configuration": dict(self.configuration),
            "totals": dict(self.totals),
            "cohort_sizes": dict(self.cohort_sizes),
            "by_approach": {k: v.as_dict() for k, v in self.by_approach.items()},
            "comparisons": [c.as_dict() for c in self.comparisons],
            "fallback_rate": self.fallback_rate,
            "notes": list(self.notes),
        }


def moving_block_bootstrap(
    values: Sequence[float],
    statistic: Callable[[np.ndarray], float],
    *,
    block_size: int,
    replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    ci: float = DEFAULT_CI,
    seed: int = 20261118,
) -> tuple[float, float] | None:
    """A percentile interval from resampled contiguous blocks, preserving serial dependence.

    ``values`` must already be ordered by origin. Returns ``None`` when there is not enough data
    for the interval to mean anything.
    """
    array = np.asarray(values, dtype=float)
    if array.size < MIN_REPORT_SAMPLES or block_size < 1:
        return None
    rng = np.random.default_rng(seed)
    n = array.size
    blocks_needed = int(math.ceil(n / block_size))
    starts = rng.integers(0, n - block_size + 1, size=(replicates, blocks_needed))
    gathered = np.concatenate(
        [array[start : start + block_size] for start in starts.ravel()]
    ).reshape(replicates, blocks_needed * block_size)[:, :n]
    stats = np.asarray([statistic(row) for row in gathered], dtype=float)
    stats = stats[np.isfinite(stats)]
    if stats.size < max(20, replicates // 20):
        return None
    low, high = (1.0 - ci) / 2.0, 1.0 - (1.0 - ci) / 2.0
    return (round(float(np.quantile(stats, low)), 8), round(float(np.quantile(stats, high)), 8))


def _brier_loss(probabilities: Mapping[str, float | None], bucket: str) -> float | None:
    total = 0.0
    for name in BUCKETS:
        value = probabilities.get(_PROBABILITY_KEYS[name])
        if not isinstance(value, int | float):
            return None
        total += (float(value) - (1.0 if name == bucket else 0.0)) ** 2
    return total


def reliability(
    rows: Sequence[Mapping[str, Any]], *, bins: int = 10
) -> tuple[CalibrationCell, ...]:
    """Predicted ``p_up`` against the observed frequency of ``up``, in equal-width bins."""
    pairs: list[tuple[float, float]] = []
    for row in rows:
        predicted = row.get("p_up")
        if not isinstance(predicted, int | float):
            continue
        pairs.append((float(predicted), 1.0 if row.get("outcome_bucket") == "up" else 0.0))
    if not pairs:
        return ()
    pairs.sort()
    cells: list[CalibrationCell] = []
    for index in range(bins):
        low, high = index / bins, (index + 1) / bins
        selected = [(p, r) for p, r in pairs if low <= p < high or (index == bins - 1 and p == 1.0)]
        if not selected:
            continue
        enough = len(selected) >= MIN_CELL_SAMPLES
        cells.append(
            CalibrationCell(
                bucket=f"{low:.1f}-{high:.1f}",
                n=len(selected),
                predicted=round(float(np.mean([p for p, _ in selected])), 4) if enough else None,
                realized=round(float(np.mean([r for _, r in selected])), 4) if enough else None,
            )
        )
    return tuple(cells)


def _score(
    approach: str,
    rows: Sequence[Mapping[str, Any]],
    *,
    block_size: int,
    seed: int,
) -> Metrics:
    notes: list[str] = []
    n = len(rows)
    losses = [float(r["_loss"]) for r in rows if r.get("_loss") is not None]
    brier = round(float(np.mean(losses)), 6) if losses else None
    brier_ci = moving_block_bootstrap(
        losses, lambda a: float(np.mean(a)), block_size=block_size, seed=seed
    )
    if n < MIN_REPORT_SAMPLES:
        notes.append(
            f"only {n} observation(s), below {MIN_REPORT_SAMPLES}: no interval is reported and "
            "this must not be read as a result"
        )
        brier_ci = None

    covered: list[float] = []
    widths: list[float] = []
    for row in rows:
        q10, q90, realized = row.get("q10"), row.get("q90"), row.get("abnormal_return")
        if not isinstance(q10, int | float) or not isinstance(q90, int | float):
            continue
        if not isinstance(realized, int | float):
            continue
        low, high, value = float(q10), float(q90), float(realized)
        covered.append(1.0 if low <= value <= high else 0.0)
        widths.append(high - low)
    interval_coverage = (
        round(float(np.mean(covered)), 6) if len(covered) >= MIN_CELL_SAMPLES else None
    )
    if interval_coverage is not None and len(covered) < MIN_REPORT_SAMPLES:
        notes.append(f"interval coverage rests on {len(covered)} observation(s)")

    abnormal = [float(r["abnormal_return"]) for r in rows
                if isinstance(r.get("abnormal_return"), int | float)]  # fmt: skip
    mean_abnormal = round(float(np.mean(abnormal)), 8) if abnormal else None
    mean_ci = moving_block_bootstrap(
        abnormal, lambda a: float(np.mean(a)), block_size=block_size, seed=seed
    )
    if len(abnormal) < MIN_REPORT_SAMPLES:
        mean_ci = None

    def share(bucket: str) -> float | None:
        hits = [r for r in rows if r.get("outcome_bucket") in BUCKETS]
        if len(hits) < MIN_CELL_SAMPLES:
            return None
        return round(
            float(np.mean([1.0 if r["outcome_bucket"] == bucket else 0.0 for r in hits])), 6
        )

    fallbacks = sum(1 for r in rows if r.get("fallback_used"))
    if fallbacks:
        notes.append(
            f"{fallbacks} of {n} publication(s) used a labelled baseline fallback rather than the "
            "requested event-conditioned pool"
        )
    return Metrics(
        approach=approach,
        n=n,
        brier=brier,
        brier_ci=brier_ci,
        reliability=reliability(rows),
        interval_coverage=interval_coverage,
        interval_width=round(float(np.mean(widths)), 6) if widths else None,
        share_up=share("up"),
        share_side=share("side"),
        share_down=share("down"),
        mean_abnormal_return=mean_abnormal,
        mean_abnormal_ci=mean_ci,
        median_abnormal_return=round(float(np.median(abnormal)), 8) if abnormal else None,
        fallback_count=fallbacks,
        notes=tuple(notes),
    )


def _paired(
    approach: str,
    baseline: str,
    losses: Mapping[str, float],
    baseline_losses: Mapping[str, float],
    order: Sequence[str],
    *,
    block_size: int,
    seed: int,
) -> PairedComparison:
    keys = [k for k in order if k in losses and k in baseline_losses]
    notes: list[str] = []
    n = len(keys)
    if n == 0:
        return PairedComparison(approach, baseline, 0, None, None, None,
                                "no shared observations", ("the two approaches were never scored "
                                                           "on the same event",))  # fmt: skip
    differences = [losses[k] - baseline_losses[k] for k in keys]
    mean_difference = round(float(np.mean(differences)), 8)
    if n < MIN_REPORT_SAMPLES:
        notes.append(
            f"only {n} paired observation(s), below {MIN_REPORT_SAMPLES}: no interval, no verdict"
        )
        return PairedComparison(approach, baseline, n, mean_difference, None, None,
                                "insufficient paired evidence", tuple(notes))  # fmt: skip
    interval = moving_block_bootstrap(
        differences, lambda a: float(np.mean(a)), block_size=block_size, seed=seed
    )
    wins = None
    if interval is not None:
        rng = np.random.default_rng(seed)
        array = np.asarray(differences, dtype=float)
        blocks = int(math.ceil(array.size / block_size))
        starts = rng.integers(
            0, array.size - block_size + 1, size=(DEFAULT_BOOTSTRAP_REPLICATES, blocks)
        )
        gathered = np.concatenate([array[s : s + block_size] for s in starts.ravel()]).reshape(
            DEFAULT_BOOTSTRAP_REPLICATES, blocks * block_size
        )[:, : array.size]
        means = gathered.mean(axis=1)
        wins = round(float(np.mean(means < 0)), 4)
    verdict = "indistinguishable"
    if n < MIN_PROMOTION_SAMPLES:
        verdict = "insufficient evidence to prefer either"
        notes.append(
            f"{n} paired observations is below the {MIN_PROMOTION_SAMPLES} needed to prefer a model"
        )
    elif interval is not None:
        if interval[1] < 0:
            verdict = f"{approach} scores better than {baseline}"
        elif interval[0] > 0:
            verdict = f"{baseline} scores better than {approach}"
    return PairedComparison(
        approach=approach,
        baseline=baseline,
        n_paired=n,
        mean_loss_difference=mean_difference,
        ci=interval,
        wins=wins,
        verdict=verdict,
        notes=tuple(notes),
    )


def evaluate(
    publications: Iterable[Publication],
    outcomes: Mapping[str, Mapping[str, Any]],
    *,
    run_id: str,
    evaluation_mode: str,
    created_at: datetime,
    configuration: Mapping[str, Any],
    totals: Mapping[str, int],
    horizons: Sequence[int] = (),
    bootstrap_replicates: int = DEFAULT_BOOTSTRAP_REPLICATES,
    seed: int = 20261118,
) -> EvaluationReport:
    """Score frozen publications against settled outcomes, paired across approaches.

    ``outcomes`` maps ``(record_id, horizon)`` to a row carrying ``abnormal_return`` and
    ``outcome_bucket``. Publications whose horizon has not settled are excluded from scoring but
    still counted in ``totals``, so an incomplete horizon is visible rather than silently absent.
    """
    block_size = max(horizons) if horizons else 5
    by_approach: dict[str, list[dict[str, Any]]] = defaultdict(list)
    losses: dict[str, dict[str, float]] = defaultdict(dict)
    cohort_sizes: dict[str, int] = defaultdict(int)
    unscored = 0
    scored = 0
    ordered_keys: list[str] = []

    for publication in sorted(publications, key=lambda p: (p.origin_date, p.publication_id)):
        key = f"{publication.record_id}|{publication.horizon_sessions}"
        cohort_sizes[publication.cohort] += 1
        outcome = outcomes.get(key)
        if outcome is None or outcome.get("outcome_bucket") not in BUCKETS:
            unscored += 1
            continue
        probabilities = {
            "p_up": publication.p_up, "p_side": publication.p_side, "p_down": publication.p_down,
        }  # fmt: skip
        loss = _brier_loss(probabilities, str(outcome["outcome_bucket"]))
        row: dict[str, Any] = {
            "key": key,
            "p_up": publication.p_up,
            "q10": publication.q10,
            "q90": publication.q90,
            "abnormal_return": outcome.get("abnormal_return"),
            "outcome_bucket": outcome.get("outcome_bucket"),
            "fallback_used": publication.fallback_used,
            "_loss": loss,
        }
        requested = publication.requested_approach or publication.approach
        by_approach[requested].append(row)
        if loss is not None:
            losses[requested][key] = loss
        if key not in ordered_keys:
            ordered_keys.append(key)
        scored += 1

    metrics = {
        approach: _score(approach, rows, block_size=block_size, seed=seed)
        for approach, rows in sorted(by_approach.items())
    }

    comparisons: list[PairedComparison] = []
    for baseline in (Approach.UNCONDITIONAL.value, Approach.TECHNICAL.value):
        baseline_losses = losses.get(baseline, {})
        if not baseline_losses:
            continue
        for approach in sorted(losses):
            if approach == baseline:
                continue
            comparisons.append(
                _paired(
                    approach,
                    baseline,
                    losses[approach],
                    baseline_losses,
                    ordered_keys,
                    block_size=block_size,
                    seed=seed,
                )  # fmt: skip
            )

    notes = [
        f"uncertainty uses a moving-block bootstrap with block length {block_size} session(s) over "
        f"{bootstrap_replicates} replicates, because overlapping event windows are not independent",
        "no after-cost strategy performance is reported: this evaluates forecast quality only, and "
        "no entry/exit execution policy with fills and slippage is implemented for research setups",
    ]
    if unscored:
        notes.append(
            f"{unscored} publication(s) were excluded from scoring because their horizon has not "
            "settled; they remain pending and are not counted as outcomes"
        )
    if evaluation_mode == "reconstructed":
        notes.append(
            "RECONSTRUCTED mode: availability is publication time plus assumed ingestion and "
            "classification lags, an explicit assumption - not a record of what this system "
            "actually knew. Do not compare these figures with forward-recorded calibration."
        )
    fallback_total = sum(m.fallback_count for m in metrics.values())
    return EvaluationReport(
        run_id=run_id,
        evaluation_mode=evaluation_mode,
        created_at=created_at,
        configuration=configuration,
        totals={
            **dict(totals),
            "publications_scored": scored,
            "publications_unscored": unscored,
        },
        cohort_sizes=dict(sorted(cohort_sizes.items())),
        by_approach=metrics,
        comparisons=tuple(comparisons),
        fallback_rate=round(fallback_total / scored, 6) if scored else None,
        notes=tuple(notes),
    )
