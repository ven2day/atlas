"""
Event-aware market research: candidate cards with probabilistic forecasts and their evidence.

Reads the announcements the engine has already ingested and classified, maps them onto
instruments and sectors with explicit reviewable rules, forecasts the forward-return
distribution from historical analogs, and writes one explainable card per candidate. Scores
previously published forecasts against their outcomes, so calibration is measured rather than
asserted.

This is research and decision support only: nothing here sizes, prices, places or routes an
order, and no status is an instruction to trade.

    uv run python scripts/run_research.py                       # today, 5-session horizon
    uv run python scripts/run_research.py --horizon 10 --status CONFIRMED
    uv run python scripts/run_research.py --symbol INFY         # one card, in full
    uv run python scripts/run_research.py --report var/reports/research.md --json out.json

Every candidate matching the filters is listed; nothing is truncated to a "top N". Candidates
whose data is missing or whose asset has no configured source are reported as unsupported rather
than dropped, so an absent answer is visible.
"""

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from src.config import get_settings
from src.ops.exit_codes import ExitCode
from src.ops.process import run_entry_point
from src.research.cards import CandidateCard, CandidateFilter, filter_cards
from src.research.events import AssetClass
from src.research.forecast import HORIZONS
from src.research.pipeline import ResearchRun, run_research
from src.research.status import CandidateStatus
from src.research.store import dumps


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RakshaQuant research: event-aware candidates, forecasts and calibration."
    )
    parser.add_argument("--horizon", type=int, default=5, choices=sorted(HORIZONS),
                        help="Forecast horizon in trading sessions (default: 5).")  # fmt: skip
    parser.add_argument("--status", choices=[s.value for s in CandidateStatus], default=None,
                        help="Only this candidate status.")  # fmt: skip
    parser.add_argument("--asset", choices=[a.value for a in AssetClass], default=None,
                        help="Only this asset class.")  # fmt: skip
    parser.add_argument("--sector", default=None, help="Only this sector (exact match).")
    parser.add_argument("--event-type", default=None, help="Only candidates with this event type.")
    parser.add_argument("--symbol", default=None, help="Print one symbol's card in full.")
    parser.add_argument("--min-confidence", type=float, default=None,
                        help="Drop candidates whose weakest catalyst confidence is below this.")  # fmt: skip
    parser.add_argument("--exclude-uncertain", action="store_true",
                        help="Drop candidates whose catalyst mapping is flagged uncertain.")  # fmt: skip
    parser.add_argument("--report", type=Path, default=None,
                        help="Also write a Markdown report to this path.")  # fmt: skip
    parser.add_argument("--json", type=Path, default=None, help="Also write the cards as JSON.")
    parser.add_argument("--no-persist", action="store_true",
                        help="Do not write forecasts, cards or impacts to the database.")  # fmt: skip
    parser.add_argument("--no-settle", action="store_true",
                        help="Do not score previously published forecasts against outcomes.")  # fmt: skip
    return parser.parse_args(argv)


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def _print_row(card: CandidateCard) -> None:
    confidence = card.confidence
    prediction = card.forecast
    print(
        f"  {card.symbol:<12} {card.status.value:<14} "
        f"{(card.sector or '-')[:22]:<22} "
        f"up {_pct(prediction.p_up if prediction else None)} "
        f"side {_pct(prediction.p_side if prediction else None)} "
        f"down {_pct(prediction.p_down if prediction else None)} "
        f"n={prediction.n_samples if prediction else 0:<4} "
        f"conf={'  n/a' if confidence is None else f'{confidence:5.2f}'}"
    )


def _print_card(card: CandidateCard) -> None:
    prediction = card.forecast
    print(f"\n{card.symbol}  ({card.instrument_key})")
    print(f"  company   : {card.company or '-'}")
    print(f"  sector    : {card.sector or 'unknown - shares the risk engine Unknown bucket'}")
    print(f"  asset     : {card.asset.value}")
    print(f"  status    : {card.status.value}")
    print(f"  horizon   : {card.horizon_sessions} session(s)")
    print(f"  as of     : {card.as_of.isoformat()}")
    print("\n  catalysts")
    for event in card.events:
        print(f"    [{event.event_id}] {event.published_at.isoformat()} via {event.source}")
        print(f"      type={event.event_type or 'unclassified'} channel={event.channel} "
              f"direction={event.direction} materiality={event.materiality or '-'}")  # fmt: skip
        print(f"      confidence={event.confidence} basis={event.basis} "
              f"uncertain={event.uncertain}")  # fmt: skip
        if event.url:
            print(f"      source: {event.url}")
        print(f"      text: {event.excerpt}")
        print(f"      why: {event.reason}")
        for item in event.evidence:
            note = f" ({item.note})" if item.note else ""
            print(f"        - {item.field} = {item.value[:120]}{note}")
        if event.updates:
            print(f"      supersedes: {', '.join(event.updates)}")
    print("\n  market context")
    context = card.context
    print(
        f"    trend={context.trend.value} regime={context.regime.value if context.regime else '-'}"
    )
    print(f"    close={context.close} rsi14={context.rsi_14} adx14={context.adx_14}")
    print(
        f"    atr%={context.atr_pct} sigma={context.sigma_daily} vol_ratio={context.volume_ratio}"
    )
    strength = ", ".join(f"{k}={v}" for k, v in sorted(context.relative_strength.items()))
    print(f"    relative strength: {strength or '-'}")
    print(f"    agrees with the catalyst: {card.confirmation.value} - {card.confirmation_reason}")
    print(f"    data: {context.data_quality}")
    print("\n  forecast")
    if prediction is None:
        print("    none produced")
    else:
        print(f"    status={prediction.status.value} baseline={prediction.baseline} "
              f"samples={prediction.n_samples} (of {prediction.n_unconditional})")  # fmt: skip
        print(
            f"    p(up)={prediction.p_up} p(side)={prediction.p_side} p(down)={prediction.p_down}"
        )
        print(f"    q10/q50/q90 = {prediction.q10} / {prediction.q50} / {prediction.q90}")
        print(f"    p(underperform benchmark)={prediction.p_underperform_benchmark}")
        print(f"    sideways band=+/-{prediction.sideways_band} state=(trend={prediction.trend}, "
              f"vol={prediction.vol_band})")  # fmt: skip
        print(f"    data: {prediction.data_quality}")
        for note in prediction.notes:
            print(f"    caveat: {note}")
    print("\n  why this status")
    for reason in card.reasons:
        print(f"    - {reason}")
    print("  positive factors")
    for factor in card.positive_factors or ("(none)",):
        print(f"    + {factor}")
    print("  negative factors")
    for factor in card.negative_factors or ("(none)",):
        print(f"    - {factor}")
    print("  would confirm  :", "; ".join(card.confirm_if) or "-")
    print("  would weaken   :", "; ".join(card.weaken_if) or "-")
    print("  would invalidate:", "; ".join(card.invalidate_if) or "-")
    if card.unsupported:
        print("  unsupported / missing")
        for item in card.unsupported:
            print(f"    ! {item}")
    freshness = card.freshness
    print(f"  freshness: last bar {freshness.last_bar_date} "
          f"({freshness.bar_age_sessions} session(s) old), newest catalyst "
          f"{freshness.newest_event_at.isoformat() if freshness.newest_event_at else '-'}")  # fmt: skip
    for note in freshness.notes:
        print(f"    ! {note}")


def _print_evaluation(run: ResearchRun) -> None:
    report = run.calibration
    print("\nForecast calibration (published forecasts scored against realized outcomes)")
    print(f"  settled forecasts: {report.n}   multi-class Brier: {report.brier}")
    if report.brier is not None:
        print("  (lower is better; always answering the middle bucket scores about 0.667)")
    for note in report.notes:
        print(f"  ! {note}")
    if report.cells:
        print("  reliability, predicted p(up) vs observed:")
        for cell in report.cells:
            shown = (
                "insufficient samples"
                if cell.predicted is None
                else f"predicted {cell.predicted:.3f}  realized {cell.realized:.3f}"
            )
            print(f"    {cell.bucket}: n={cell.n:<4} {shown}")
    for label, groups in (("horizon", report.by_horizon), ("regime", report.by_regime)):
        for name, cells in groups.items():
            sized = [c for c in cells if c.predicted is not None]
            if not sized:
                continue
            print(
                f"  by {label} {name}: "
                + ", ".join(
                    f"{c.bucket} p={c.predicted:.2f}/r={c.realized:.2f}(n={c.n})" for c in sized
                )
            )
    if run.study:
        print(
            "\nEvent study (abnormal return vs the benchmark, from the first settled session "
            "after the event was known)"
        )
        print(
            f"  {'event type':<24} {'direction':<10} {'h':>3} {'regime':<14} "
            f"{'n':>4} {'mean':>9} {'median':>9} {'hit':>7} {'net':>9}"
        )
        for cell in run.study:
            mean = "-" if cell.mean_abnormal_return is None else f"{cell.mean_abnormal_return:+.4f}"
            median = (
                "-"
                if cell.median_abnormal_return is None
                else f"{cell.median_abnormal_return:+.4f}"
            )
            hit = "-" if cell.hit_rate is None else f"{cell.hit_rate:.2f}"
            net = (
                "-"
                if cell.mean_net_abnormal_return is None
                else f"{cell.mean_net_abnormal_return:+.4f}"
            )
            print(
                f"  {cell.event_type[:24]:<24} {cell.direction:<10} "
                f"{cell.horizon_sessions:>3} {(cell.regime or '-')[:14]:<14} "
                f"{cell.n:>4} {mean:>9} {median:>9} {hit:>7} {net:>9}"
            )
            for note in cell.notes:
                print(f"      ! {note}")
    if run.settled:
        print(f"\n  scored {run.settled} previously published forecast(s) in this run")


def _markdown(run: ResearchRun, cards: list[CandidateCard]) -> str:
    lines = [
        "# Research candidates",
        "",
        f"- as of: `{run.as_of.isoformat()}`",
        f"- horizon: {run.horizon} session(s)",
        f"- candidates: {len(cards)}",
        f"- statuses: {', '.join(f'{k}={v}' for k, v in sorted(run.status_counts.items())) or 'none'}",
        f"- settled forecasts scored: {run.settled}",
        f"- calibration: n={run.calibration.n}, Brier={run.calibration.brier}",
        "",
        "Nothing on this page is an order or an instruction to trade. Every figure is an "
        "empirical distribution over historical analogs, with its sample size beside it.",
        "",
    ]
    for card in cards:
        prediction = card.forecast
        lines += [
            f"## {card.symbol} - {card.status.value}",
            "",
            f"- instrument: `{card.instrument_key}`  sector: {card.sector or 'unknown'}"
            f"  asset: {card.asset.value}",
            f"- horizon: {card.horizon_sessions} session(s), as of {card.as_of.isoformat()}",
        ]
        if prediction is not None:
            lines += [
                f"- forecast ({prediction.baseline}, n={prediction.n_samples}): "
                f"p(up)={prediction.p_up}, p(side)={prediction.p_side}, p(down)={prediction.p_down}",
                f"- q10/q50/q90 = {prediction.q10} / {prediction.q50} / {prediction.q90}"
                f"; p(underperform benchmark)={prediction.p_underperform_benchmark}",
            ]
        lines += ["", "### Catalysts", ""]
        for event in card.events:
            link = f" ([source]({event.url}))" if event.url else ""
            lines += [
                f"- `{event.event_id}` {event.published_at.isoformat()} - "
                f"{event.event_type or 'unclassified'} / {event.channel} / {event.direction}"
                f"{link}",
                f"  - text: {event.excerpt}",
                f"  - why: {event.reason} (confidence {event.confidence}, basis {event.basis}"
                f"{', uncertain' if event.uncertain else ''})",
            ]
        lines += ["", "### Why this status", ""]
        lines += [f"- {reason}" for reason in card.reasons] or ["- (none recorded)"]
        lines += ["", "### Factors", ""]
        lines += [f"- (+) {f}" for f in card.positive_factors]
        lines += [f"- (-) {f}" for f in card.negative_factors]
        lines += ["", "### Conditions", ""]
        lines += [f"- confirm: {c}" for c in card.confirm_if]
        lines += [f"- weaken: {c}" for c in card.weaken_if]
        lines += [f"- invalidate: {c}" for c in card.invalidate_if]
        if card.unsupported:
            lines += ["", "### Unsupported or missing", ""]
            lines += [f"- {item}" for item in card.unsupported]
        lines += [
            "",
            f"Freshness: last bar {card.freshness.last_bar_date} "
            f"({card.freshness.bar_age_sessions} session(s) old).",
            "",
        ]
    if run.data_gaps:
        lines += ["## Data gaps", ""] + [f"- {gap}" for gap in run.data_gaps] + [""]
    return "\n".join(lines)


def main() -> int:
    args = _parse_args()
    settings = get_settings()
    run = asyncio.run(
        run_research(
            settings,
            horizon=args.horizon,
            persist=not args.no_persist,
            settle=not args.no_settle,
        )
    )
    wanted = CandidateFilter(
        asset=AssetClass(args.asset) if args.asset else None,
        sector=args.sector,
        event_type=args.event_type,
        horizon=args.horizon,
        status=CandidateStatus(args.status) if args.status else None,
        min_confidence=args.min_confidence,
        include_uncertain=not args.exclude_uncertain,
    )
    cards = filter_cards(run.cards, wanted)
    if args.symbol:
        cards = [c for c in cards if c.symbol.upper() == args.symbol.upper()]

    print("=" * 78)
    print("RakshaQuant research - event-aware candidates (decision support, not orders)")
    print("=" * 78)
    print(f"as of {run.as_of.isoformat()}   horizon {run.horizon} session(s)")
    print(f"provider {settings.market_data_provider}   environment {settings.environment}")
    if settings.environment == "demo":
        print("!! ENVIRONMENT=demo: synthetic tape, cards are labelled and isolated, never real")
    print(f"\n{run.summary()}")

    if args.symbol:
        if not cards:
            print(f"\nno card for {args.symbol.upper()} among this run's candidates")
        for card in cards:
            _print_card(card)
    else:
        print(
            f"\n  {'symbol':<12} {'status':<14} {'sector':<22} "
            f"{'p(up)':>6} {'p(side)':>8} {'p(down)':>8} {'n':>5} {'conf':>6}"
        )
        for card in cards:
            _print_row(card)
        if not cards:
            print("  (no candidates matched; see the data gaps below)")

    _print_evaluation(run)

    if run.data_gaps:
        print("\nData gaps and unsupported inputs")
        for gap in run.data_gaps:
            print(f"  ! {gap}")

    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(_markdown(run, cards), encoding="utf-8")
        print(f"\nwrote {args.report}")
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps([json.loads(dumps(asdict(card))) for card in cards], indent=2),
            encoding="utf-8",
        )
        print(f"wrote {args.json}")
    return ExitCode.OK


if __name__ == "__main__":
    run_entry_point("run_research", main)
