# Research: event-aware candidates and probabilistic forecasts

A decision-support layer that answers *"which instruments deserve attention, and on what
evidence?"* It sits **beside** the trading path, not inside it: nothing in `src/research` imports
the OMS, the risk gate or a broker adapter, and no code path in it can produce an order.

```bash
uv run python scripts/run_research.py                      # today, 5-session horizon
uv run python scripts/run_research.py --horizon 10 --status CONFIRMED
uv run python scripts/run_research.py --symbol INFY        # one card, in full
uv run python scripts/run_research.py --report var/reports/research.md
```

The console shows the same output under **Research → `/research`**, reading the snapshot the
script publishes. The screen never triggers a run; a run is an explicit operator action.

## What it will not do

| It never | Instead it |
| --- | --- |
| Places, sizes, prices or routes an order | Reports a status and the evidence for it |
| Claims to know a future price | Reports a return *distribution* with its sample size |
| Treats an LLM's opinion as a signal | Uses models only to classify text, and requires cited evidence |
| Invents a probability from thin data | Returns `INSUFFICIENT_EVIDENCE` |
| Prices an asset it has no source for | Returns `AssetClass.UNSUPPORTED` and says so on the card |
| Truncates to a "top five" | Lists every candidate that matches the filters |

A **forecast is not a recommendation**, and a **positive event reading is not a forecast**. Those
are three separate blocks on every card, and they can disagree — when they do, the card says so
rather than averaging them into a single confident-looking number.

## The layers

```
AnnouncementReceived + TypedEvent   (existing pipeline; nothing is re-ingested)
        │  src/research/events.py    normalize, dedupe, supersede, point-in-time guard
        ▼
   ResearchEvent
        │  src/research/mapping.py   explicit rule table -> channels, scope, direction
        ▼
    EventImpact ─────────────┐
                             │  src/research/forecast.py   historical analogs -> distribution
   DailySeries ──────────────┤  src/research/context.py    features + regime + relative strength
   Features, Regime ─────────┤
                             ▼  src/research/status.py     WATCH / SETUP_FORMING / CONFIRMED /
                        CandidateCard                      INVALIDATED / NO_TRADE
                             │  src/research/cards.py      evidence, factors, conditions
                             ▼
              src/research/store.py + snapshot.py -> console, CLI, evaluation
```

### 1. Events (`events.py`)

Joins the two records the engine already writes: `AnnouncementReceived` (the exchange's
`published_at` and our `received_at`) and `TypedEvent` (its classification). Adds three things the
trading gate does not need but research does:

- **`known_at = max(published_at, received_at, classified_at)` for classified events.** A run timestamped `as_of` sees only events with
  `known_at <= as_of`. A late-published or future-dated story can never leak into an earlier
  decision. This single field is what makes the evaluation in §6 honest.
- **Supersession, not silent dedup.** An explicit correction must link to the earlier event ID
  or reuse its nonempty source URL. Same-day announcements remain independent. The original
  stays in the store; classification versions are filtered by their availability timestamps.
- **An explicit asset class**, including `UNSUPPORTED` for anything with no configured source.

`source_confidence` (do we trust NSE's feed?) is kept apart from `classification_confidence` (do
we trust this reading of this item?). Conflating them is how a system becomes overconfident.

### 2. Mapping (`mapping.py`)

One reviewable table, `_RULES`, maps each `AnnouncementType` to a channel (`demand`, `costs`,
`policy`, `currency`, `rates`, `risk_sentiment`, `capital_structure`, `governance`), a scope and
whether the direction is knowable from the type alone. Changing the system's view of the world
means editing that table, not a prompt.

The model's reading may *refine* a direction but never invent one:

| Rule says | Model says | Result |
| --- | --- | --- |
| a direction | the same | that direction, `uncertain=False` |
| a direction | unknown / absent | the rule's direction |
| a direction | the opposite | `MIXED`, `uncertain=True` |
| unknown | a direction, confidence ≥ 0.60 | the model's direction |
| unknown | a direction, confidence < 0.60 | the model's direction, `uncertain=True` |
| unknown | unknown | `UNKNOWN`, `uncertain=True` |

So a fundraise stays `MIXED` (growth capital *versus* dilution) even when the classifier calls it
positive, and an insider filing stays `UNKNOWN` because the direction lives in the text, not the
category. Every impact carries the `Evidence` list it was derived from — source, text, exchange
subject, event type, classified direction — so a reader can audit the reading.

A **sector spillover** is emitted only where the rule table allows it (results, order wins,
litigation), always at half the issuer's confidence and always flagged uncertain. A **market-wide**
impact is never emitted: NSE's corporate RSS is issuer-level, and this repository has no
authorized macro source. `ImpactScope.MARKET` exists so one can be added later.

### 3. Forecasts (`forecast.py`)

Purely empirical. The event-conditioned cohort matches prior events for the same issuer, type,
direction and materiality. It requires at least 20 complete, non-overlapping outcomes and reports
`baseline="event_analogs"`. The anchor is the first session close after the classification became
known; this measures a close-to-close reaction, not an executable trade return. Event-conditioned
benchmark underperformance is currently unavailable and explicitly reported as such.

When this cohort is too small, the card explicitly falls back to a technical baseline.
For each historical bar, compute the forward log return over `h` sessions, and
keep the bars whose trailing state resembles today's:

- **trend** — close above/below its own 50-session mean;
- **volatility band** — 20-session realized vol, annualized, against *fixed* thresholds
  (low ≤ 20%, mid ≤ 40%, else high). Fixed rather than sample-relative on purpose: a percentile
  over the whole sample would let future bars decide which bucket a past bar sits in.

Matching bars are the analogs. Output: `p_up` / `p_side` / `p_down`, the 10th/50th/90th percentile
forward returns, `p_underperform_benchmark`, the sample count, the baseline used and the data
quality string. Horizons are 1, 5, 10 and 20 sessions.

"Sideways" is a **stated band**, `±0.5 · σ_daily · √h` — without it, `p_up` and `p_down` are not
comparable across horizons.

Fallbacks, in order, each recorded on the card:

1. fewer than 20 matching event outcomes → use the technical baseline and explicitly label it;
2. fewer than 20 analogs in the current state → use all forward returns (`baseline="unconditional"`) and say so;
3. fewer than 40 usable forward returns in total → `INSUFFICIENT_EVIDENCE`, no numbers at all;
4. a simulated or synthetic price source → `UNSUPPORTED_DATA`, refused outright.

No model is ever asked for a number. Laya and Jev classify text; the arithmetic is NumPy.

### 4. Context (`context.py`)

Reshapes the features and regime the engine already computes into trend, volatility, volume,
relative strength (20- and 60-session excess log return versus the benchmark) and regime. Then
answers one question: does the market **confirm** or **disagree** with the mapped direction?
`agreement()` returns a reason string alongside its verdict, so a card shows *why*.

### 5. Status (`status.py`)

A deterministic state machine; every threshold is a named constant (`CONFIRM_MIN_SAMPLES = 30`,
`CONFIRM_PROB_MARGIN = 0.05`, `CONFIRM_MIN_RS = 0.0`).

| Status | Means |
| --- | --- |
| `NO_TRADE` | No catalyst, or the asset has no configured source, or the forecast was refused |
| `WATCH` | A catalyst exists but the direction is unknown/mixed, or evidence is too thin |
| `SETUP_FORMING` | Catalyst, market and forecast point the same way but a confirmation condition is unmet |
| `CONFIRMED` | Direction agreed, ≥ 30 analogs, a ≥ 5% margin over the opposite outcome, relative strength not lagging, and the market confirms |
| `INVALIDATED` | A **positive** thesis the market contradicts |

Asymmetry worth knowing: a *negative* catalyst in a rising market is `WATCH`, not `INVALIDATED`.
Nothing was claimed about price, so nothing has been disproved — and in a long-only book a
bearish reading is a reason to avoid or exit, not a setup. Every card also carries `confirm_if`,
`weaken_if` and `invalidate_if` lists.

### 6. Evaluation (`evaluation.py`)

- **Event study.** Abnormal return versus the benchmark, measured from the first settled session
  that ends *after* `known_at`, grouped by event type, claimed direction, horizon and regime.
  Where a cell describes a trade setup, round-trip NSE charges — from the same
  `NSECostSchedule` the simulated broker uses — are deducted, because a gross abnormal return is
  not a return anyone keeps. Cells with fewer than 8 observations report their count and withhold
  the percentages.
- **Calibration.** Published forecasts are scored against outcomes they did not know: a
  multi-class Brier score over up/sideways/down, plus a reliability table of predicted `p_up`
  against the observed frequency of `up`, broken out by horizon and regime. Lower is better;
  always answering the middle bucket scores about 0.667.

Neither produces a profit claim. Whether the resulting *decisions* would have made money belongs
to `src/backtesting/edge.py` and its 200-trade / 95% CI gate.

## Persistence

Migration `0003_research.sql` adds three tables — `research_forecasts`, `research_cards`,
`research_event_impacts`. It is **additive**: no existing table or column changes, so a database
written by earlier code opens unchanged. These are not projections, so `rebuild_projections()`
never touches them (the same rule as `kv_state`).

`src/research/store.py` opens its **own** connection to the same SQLite file rather than adding a
write path to `EventStore`. WAL with the shared busy timeout allows it, and it means the research
layer cannot append, project or rebuild events — and a crash in it cannot take the engine's store
connection with it.

Published forecasts are immutable. The identity includes the full UTC publication timestamp;
an exact retry leaves the publication unchanged. A later publication gets a new identity.
Legacy forecast rows remain preserved. New timestamps use explicit UTC, and card recency
comparisons parse offsets so existing rows remain readable.

A `CONFIRMED` status requires known relative strength and non-uncertain catalyst evidence.
Opposing active issuer catalysts produce `WATCH`.

Each run also writes `var/reports/research_latest.json`, the snapshot the console serves. That
file carries the event study and the data-gap list, which are computed live and have no table.

## Historical research and walk-forward validation

Everything above operates on what the engine happened to observe live. That is a thin dataset for
months, so there is a second path: import historical announcements, reconstruct what *would* have
been knowable, and score the forecasters over history. It is driven by one CLI:

```bash
uv run python scripts/research_history.py import     --file <export.json> --from 2026-06-01 --to 2026-09-30
uv run python scripts/research_history.py coverage
uv run python scripts/research_history.py outcomes   --mode reconstructed
uv run python scripts/research_history.py walkforward --run-id my-run --mode reconstructed
uv run python scripts/research_history.py report     --run-id my-run --markdown var/reports/wf.md
```

Every stage supports `--dry-run`, and `import` supports `--symbol`, `--chunk-days`,
`--min-interval` and `--max-retries`. A worked example dataset, clearly labelled synthetic, is at
[`docs/examples/historical-events.example.json`](examples/historical-events.example.json). Run it
with `ENVIRONMENT=dev` so it lands in `var/dev/` and never mixes with real research in `var/paper/`.

### Where historical announcements can come from

**NSE offers no official free date-range API for historical corporate announcements.** This was
checked rather than assumed:

| Source | Historical date-range access | Used here? |
| --- | --- | --- |
| NSE RSS (`Online_announcements.xml`) | No — the newest ~165 items only | Already used for live ingest; **not** a historical source |
| `nsearchives.nseindia.com` | File-based only: direct URLs to individual PDFs/XML, no query API | No |
| `nseindia.com` corporate-filings page | Driven by undocumented internal endpoints | No — OD-10 forbids adding NSE scraping |
| NSE *Corporate Data* subscription (paid) | Yes, by license | You supply the export |
| Authorized third-party vendors | Yes, by license | You supply the export or configure `--url` |

So the importer ships two adapters and **no invented endpoint**:

- `LocalFileSource` — the supported path. You provide a JSON or CSV export you are licensed to
  hold. The schema is documented in the example file: `symbol` and `published_at` are required;
  `event_type`, `source_id`, `document_url`, `subject`, `title`, `body`, `direction` and
  `materiality` are optional. `direction`/`materiality` are honoured **only** if your source
  actually states them; otherwise the rule table decides the direction and materiality stays
  unrated rather than being guessed.
- `HttpArchiveSource` — a generic adapter for an archive *you* configure, with a field map,
  cursor path and your own credentials. With no `--url` it raises `SourceNotConfiguredError` and
  the import is recorded as `failed`. It will not fall back to guessing an NSE endpoint.

Import guarantees: stable dedup identity (the source's own id, else a content hash), resumable
cursors persisted per run, bounded retries with exponential backoff on retryable failures only,
pacing between requests, and a per-window coverage record. **A window whose fetch failed is
recorded as `failed` and the import finishes `partial`** — a missing response is never converted
into "there were no events".

### Four timestamps, and two evaluation modes

Publication, receipt, classification availability and event-store recording are separate columns
that nothing may collapse. In particular **an import never backdates**: a 2024 announcement
imported today stores today as its receipt and recording time.

| Mode | Availability used | Claims |
| --- | --- | --- |
| `actual` (A) | `max(published, received, classified, recorded)` — all recorded facts | What this installation really knew. The only mode that measures the deployed system. |
| `reconstructed` (B) | `published + assumed ingestion lag` (default 5 min) and classification lag (2 min) | A stated assumption. Lags are configurable and the label is printed on every row and report. |

Mode B is what makes historical import usable at all — under mode A, everything imported today
becomes available today, so no historical origin can be evaluated. But mode B is a model of
availability, not a memory of it, and the two are never pooled: `check_single_mode` raises
`MixedEvaluationModesError` if a statistic would mix them, and every report carries the mode.

Applying today's rule table to an old announcement is **retrospective classification**. Each
classification row records `model`, `rules_version`, `classified_at` and `retrospective`, and a
re-read under a new rules version writes a *new* row that supersedes the old one rather than
overwriting it. Reports repeat the limitation.

### Outcomes

`outcomes` fetches daily bars through the existing provider abstractions
(`build_history_source`, widened via `--lookback-days` / `--period`) and records provenance per
instrument: provider, retrieval time, requested versus available range, missing sessions against
the exchange calendar, adjustment method, and benchmark coverage. For each event it computes
1/5/10/20-session outcomes — instrument return, benchmark return over **identical session
endpoints**, abnormal return, and an outcome bucket using the same sideways definition as the
forecaster.

- The anchor is the first session that **closes after** the information was available
  (`timing.first_close_after`), so an after-close, weekend or holiday event anchors to the next
  session and never borrows a close that had already happened.
- A horizon that has not fully elapsed stays **`pending`**. It is never zero and never dropped
  silently.
- If the benchmark lacks either endpoint the observation becomes `no_benchmark`. The raw
  instrument return is kept; the abnormal return is refused rather than assumed to be zero.
- These are **close-to-close reaction measurements, not trade returns.** No entry policy, fills,
  slippage or latency is modelled. Executable performance stays with `src/backtesting/edge.py`.
- The evaluated instrument set comes from today's scrip master and universe, so delisted issuers
  are absent. Every affected dataset is annotated `SURVIVORSHIP` rather than presented as
  unbiased.

### Walk-forward

Origins are the events' own anchor dates. At each origin, training outcomes are those that
**completed strictly before the origin minus an embargo** — which is also the purge, since a
window that ended before the origin cannot overlap the window being forecast. Overlapping windows
on one instrument are dropped greedily and the drop count is reported.

Three approaches are published for every observation, so their scores are paired:

| Approach | Trains on |
| --- | --- |
| `unconditional` | all of that issuer's event-anchored outcomes at the horizon |
| `technical` | the subset whose anchor bar shares today's trend and volatility band |
| `event_conditioned` | the cohort `issuer \| event_type \| direction \| materiality` |

`pooled_event` (which drops the issuer from the cohort) exists but is **off by default** and
separately named, because pooling issuers buys sample size by assuming they are interchangeable.
Enable it explicitly with `--allow-pooling`.

When the requested cohort is too thin, the publication falls back down that order and sets
`fallback_used`, recording both the requested and the actually-used approach. Publications are
immutable: `save_walkforward` is `ON CONFLICT DO NOTHING`, so a later run can never rewrite a
frozen forecast. Origins are split chronologically and the last `--holdout` fraction is flagged,
keeping parameter selection off the final period. Each row persists its run id, fold, cohort,
sample size and the full configuration fingerprint.

### Metrics and uncertainty

Reports cover: total and usable events, coverage exclusions, pending horizons, cohort sample
sizes, fallback frequency, multiclass Brier, reliability tables, q10–q90 interval coverage and
width, directional and benchmark-relative outcomes, and paired loss differences against both
baselines.

Uncertainty uses a **moving-block bootstrap** over observations ordered by origin, with a block
length at least the longest horizon. Overlapping event windows are not independent, so an i.i.d.
interval would be too narrow and would manufacture significance. Below `MIN_REPORT_SAMPLES` (20)
no interval is reported; below `MIN_PROMOTION_SAMPLES` (50) a paired comparison cannot name a
winner at all and returns `insufficient evidence to prefer either`.

No after-cost strategy performance is reported anywhere in this path, because no entry/exit
execution policy with fills and slippage has been implemented for research setups. The report
says so explicitly instead of implying it.

### Storage

Migration `0004_research_history.sql` adds seven tables — `research_source_records`,
`research_imports`, `research_classifications`, `research_price_datasets`,
`research_event_outcomes`, `research_walkforward`, `research_evaluations`. Like 0003 it is purely
additive: no existing table or column changes, existing research publications and all trading data
are preserved, and none of them are projections.

## Configuration

No new settings. The research run uses what is already configured:

| Setting | Effect on research |
| --- | --- |
| `MARKET_DATA_PROVIDER` | Where daily bars come from (`dhan` or `yfinance`) |
| `UNIVERSE_FILE` / `UNIVERSE_SECTORS` | The candidate set, and whether sectors are known |
| `ANNOUNCEMENTS_ENABLED` | Whether the engine ingests the catalysts research reads |
| `LLM_ROLE_*`, `DECISION_LAYA_ENABLED`, `TYPESAFE_API_KEY` | Whether announcements get classified; unclassified ones still appear, as `WATCH` with no direction |
| `DHAN_SECURITY_IDS` | Index mapping, for the benchmark series |

## Known limits

- **No catalysts, no candidates.** Research reads the engine's announcement pipeline; before at
  least one session with `ANNOUNCEMENTS_ENABLED=true` (or a replayed day), the run reports zero
  candidates and names that as a data gap.
- **Issuer-level events only.** The one authorized feed is NSE's corporate RSS. Macro, sector and
  commodity catalysts need a configured, authorized source that does not exist here yet.
- **Gold and other non-equity assets are unsupported.** There is no configured source or
  instrument mapping, so such a request returns `AssetClass.UNSUPPORTED`. It is never answered
  with a placeholder price.
- **A custom universe has no sectors** unless you supply `UNIVERSE_SECTORS`. Unsectored symbols
  share the risk engine's single `Unknown` bucket, where `RISK_MAX_SECTOR_PCT` caps them all
  together. Research warns about this; it does not change the risk engine.
- **Settlement covers the current run's instruments.** A forecast for a symbol that has since left
  the universe cannot be settled, and is reported as a gap rather than silently dropped.
- **Analog conditioning is coarse.** Trend and a fixed volatility band, not the engine's full
  ADX-based regime, because the regime is computed once daily for the index only. Cards report
  which baseline was used so the difference is visible.
