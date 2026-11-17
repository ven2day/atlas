"""The research run's published snapshot: one JSON file the console reads.

The ``research_*`` tables are the durable, point-in-time record used to score forecasts later.
The snapshot is something different: the *latest run's* complete output, including the event
study and the data-gap list, which are computed live and would need their own tables to persist.
Writing it beside the daily reports follows the pattern the console already uses for reports, and
lets the web read side serve research without importing the pipeline or re-fetching prices.

Kept in its own module so ``src.web.queries`` can read a snapshot without pulling in the market
data stack.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.config.settings import Settings

SNAPSHOT_NAME = "research_latest.json"

DISCLAIMER = (
    "Research and decision support only. Nothing here is an order, a recommendation to trade, or "
    "a claim about the future: every probability is an empirical distribution over historical "
    "analogs, shown with its sample size, and a forecast is never a confirmation of a setup."
)


def snapshot_path(reports_dir: Path) -> Path:
    return reports_dir / SNAPSHOT_NAME


def reports_dir_for(settings: Settings) -> Path:
    """Where the console will look for this environment's snapshot.

    Mirrors ``src.web.queries``: the demo environment keeps its reports under its own state
    directory, so research run against the synthetic demo tape can never be read as real.
    """
    if settings.environment == "demo":
        return settings.state_dir / "reports"
    return settings.reports_dir


def snapshot_dict(run: Any, cards: Sequence[Any]) -> dict[str, Any]:
    """The run as a JSON-ready dict. ``run`` is a :class:`~src.research.pipeline.ResearchRun`."""
    calibration = run.calibration
    return {
        "as_of": run.as_of.isoformat(),
        "horizon": run.horizon,
        "counts": dict(run.status_counts),
        "candidates": [asdict(card) for card in cards],
        "evaluation": {
            "settled": run.settled,
            "brier": calibration.brier,
            "cells": [asdict(cell) for cell in calibration.cells],
            "by_horizon": {
                key: [asdict(cell) for cell in cells]
                for key, cells in calibration.by_horizon.items()
            },
            "by_regime": {
                key: [asdict(cell) for cell in cells]
                for key, cells in calibration.by_regime.items()
            },
            "study": [asdict(cell) for cell in run.study],
            "notes": list(calibration.notes),
        },
        "data_gaps": list(run.data_gaps),
        "disclaimer": DISCLAIMER,
    }


def write_snapshot(reports_dir: Path, run: Any, cards: Sequence[Any]) -> Path:
    """Atomically replace the snapshot, so a reader never sees a half-written file."""
    from src.research.store import dumps  # local: keeps this module's imports light

    reports_dir.mkdir(parents=True, exist_ok=True)
    path = snapshot_path(reports_dir)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(dumps(snapshot_dict(run, cards)), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(reports_dir: Path) -> Mapping[str, Any] | None:
    """The latest snapshot, or ``None`` when no research run has been published yet."""
    path = snapshot_path(reports_dir)
    try:
        body: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return body if isinstance(body, dict) else None
