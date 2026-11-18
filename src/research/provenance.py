"""Four timestamps, two evaluation modes, and versioned classifications.

The single most common way a backtest lies is by collapsing "when the exchange published it" into
"when we could have acted on it". This module keeps them apart, on purpose, and makes the
difference between the two evaluation modes impossible to lose:

* **``ACTUAL``** - what this installation really knew, and when. Every timestamp is a recorded
  fact. Used for forward-recorded calibration, which is the only kind that can claim to measure
  the deployed system.
* **``RECONSTRUCTED``** - publication time plus an *assumed* ingestion and classification lag.
  This is a modelling assumption, not a memory: the import that ran today still has today's
  ``received_at``, and nothing is backdated to pretend otherwise. Every result computed this way
  carries the label.

The two are never pooled. ``mixing_modes`` exists so a caller that tries gets an error rather than
a silently blended statistic.

Applying today's classifier to an announcement from two years ago is **retrospective
classification**: the model, prompt and rule versions are recorded on the classification, and the
flag travels with it into every report, because such a reading could not have been produced at the
time and may encode information the original reader did not have.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from src.utils.market_time import IST


class EvaluationMode(StrEnum):
    ACTUAL = "actual"
    RECONSTRUCTED = "reconstructed"


class MixedEvaluationModesError(ValueError):
    """Raised when actual and reconstructed observations would be pooled into one statistic."""


# Assumed delays used only by RECONSTRUCTED mode. The RSS feed's own ttl is five minutes, so a
# five-minute ingestion lag is the optimistic-but-defensible default for a polled source.
DEFAULT_INGESTION_LAG = timedelta(minutes=5)
DEFAULT_CLASSIFICATION_LAG = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class EventTimestamps:
    """The four distinct moments in an event's life. None of them implies another."""

    published_at: datetime  # the source's own publication time
    received_at: datetime  # when this system actually got it (never backdated)
    classified_at: datetime | None  # when a classification became available
    recorded_at: datetime  # when it was written to the event store

    def __post_init__(self) -> None:
        for name in ("published_at", "received_at", "recorded_at"):
            value = getattr(self, name)
            if value.tzinfo is None:
                raise ValueError(f"{name} must be timezone-aware")
        if self.classified_at is not None and self.classified_at.tzinfo is None:
            raise ValueError("classified_at must be timezone-aware")
        if self.recorded_at < self.received_at:
            raise ValueError("a record cannot be stored before it was received")

    @property
    def actual_availability(self) -> datetime:
        """Mode A: the first instant every input genuinely existed."""
        moments = [self.published_at, self.received_at, self.recorded_at]
        if self.classified_at is not None:
            moments.append(self.classified_at)
        return max(moments)

    def reconstructed_availability(
        self,
        *,
        ingestion_lag: timedelta = DEFAULT_INGESTION_LAG,
        classification_lag: timedelta = DEFAULT_CLASSIFICATION_LAG,
    ) -> datetime:
        """Mode B: publication plus assumed lags. An assumption, labelled as one."""
        if ingestion_lag < timedelta(0) or classification_lag < timedelta(0):
            raise ValueError("assumed lags cannot be negative")
        available = self.published_at + ingestion_lag
        if self.classified_at is not None:
            available = max(available, self.published_at + classification_lag)
        return available

    def availability(
        self,
        mode: EvaluationMode,
        *,
        ingestion_lag: timedelta = DEFAULT_INGESTION_LAG,
        classification_lag: timedelta = DEFAULT_CLASSIFICATION_LAG,
    ) -> datetime:
        if mode is EvaluationMode.ACTUAL:
            return self.actual_availability
        return self.reconstructed_availability(
            ingestion_lag=ingestion_lag, classification_lag=classification_lag
        )

    @property
    def import_delay(self) -> timedelta:
        """How late this system saw it. Large for historical imports, and that is the point."""
        return self.received_at - self.published_at


@dataclass(frozen=True, slots=True)
class ClassificationVersion:
    """Which reader produced a classification, and whether it read the event after the fact."""

    model: str
    prompt_version: str | None = None
    rules_version: str | None = None
    retrospective: bool = False

    def label(self) -> str:
        parts = [self.model]
        if self.prompt_version:
            parts.append(f"prompt={self.prompt_version}")
        if self.rules_version:
            parts.append(f"rules={self.rules_version}")
        if self.retrospective:
            parts.append("retrospective")
        return ";".join(parts)


def check_single_mode(modes: set[EvaluationMode] | frozenset[EvaluationMode]) -> EvaluationMode:
    """Refuse to pool observations produced under different availability assumptions."""
    if not modes:
        raise MixedEvaluationModesError("no observations to evaluate")
    if len(modes) > 1:
        raise MixedEvaluationModesError(
            f"cannot pool observations from {sorted(m.value for m in modes)}: actual and "
            "reconstructed availability are different assumptions and must be reported apart"
        )
    return next(iter(modes))


def is_after_close(moment: datetime, close_hour: int = 15, close_minute: int = 30) -> bool:
    """Whether an IST instant falls at or after the regular session close."""
    local = moment.astimezone(IST)
    close = local.replace(hour=close_hour, minute=close_minute, second=0, microsecond=0)
    return local >= close


def is_weekend(moment: datetime) -> bool:
    """Saturday or Sunday in IST. Holidays need the calendar; this is only the coarse check."""
    return moment.astimezone(IST).weekday() >= 5
