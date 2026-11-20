# News & events: event-driven research from public RSS feeds

Paper/research feature. **Nothing on this path can place, size or route an order** - news reaches
trading only through the same research cards, event gate and decision lineage everything else
uses, and a "catalyst watch" is a monitoring state, never a signal that bypassed a veto or a
risk check.

## The flow, end to end

```
src/config/news_feeds.yaml          the operator-controlled registry (enabled + verified only)
        │
src/marketdata/news.py              NewsCollector: SSRF-guarded, bounded, conditional GET
        │                           emits NewsItemReceived (+ NewsCoverageGap on failure)
        ▼
src/decision_models/tasks/news.py   NewsClassifier: rule floor, configured cascade on top
        │                           emits NewsClassified per *resolved* symbol
        ▼
┌───────────────┬────────────────────┬──────────────────────────────┐
│ Console       │ Research archive   │ Research pipeline            │
│ GET /api/news │ sync-rss →         │ research_events() → cards,   │
│ /api/news/    │ research_source_   │ forecasts, calibration,      │
│ feeds         │ records + gaps     │ settled outcomes             │
└───────────────┴────────────────────┴──────────────────────────────┘
```

The engine runs the collector as its own periodic task (`news`, beside `announcements`), started
at **PRE_OPEN** - not at the market open: overnight headlines matter before the session trades,
and an evening or weekend run never leaves PRE_OPEN, so a task that started only at OPEN would
silently never poll. The pre-open hook also runs one forced backfill poll (like announcements),
and a backfill failure raises a `news_backfill_failed` WARNING without blocking the session.
Demo/replay sessions never fetch: no news events exist on a tape, and the screen says so.

The NSE announcements feed is **not** re-fetched here. It is owned by
`src/marketdata/announcements.py` (`AnnouncementIngestor`) with its own coverage-gap behaviour,
and its registry entry is marked `owned_by: announcements` so the news collector skips it.
Exchange filings and media news stay visibly distinct: every item and every UI badge carries its
source name and source type.

## The registry

`src/config/news_feeds.yaml` (override with `NEWS_FEEDS_FILE`):

```yaml
feeds:
  - key: moneycontrol_latest        # unique, [a-z0-9_]
    name: Moneycontrol - latest news
    type: media_news                # media_news | macro | exchange_filing
    url: https://www.moneycontrol.com/rss/latestnews.xml
    enabled: false
    verified: null                  # date set by scripts/verify_news_feeds.py
    min_interval_s: 300
```

A feed is fetched **only when `enabled: true` AND `verified` carries a date**. The shipped
registry keeps every media/macro feed disabled and unverified: a feed this installation has
never successfully fetched is never silently assumed to work. To add one:

```
uv run python scripts/verify_news_feeds.py --key <key> --include-disabled
```

The script runs the same SSRF validation and bounded fetch the collector uses, parses the feed,
reports item counts and timestamps, and prints the exact registry edit to make on success. A
feed that cannot be verified stays disabled - that is the documented state, not a failure to
notice.

## Safety rules on every fetch

- http/https only; no credentials in URLs; hosts resolved and refused when they point at
  private/loopback/link-local/reserved addresses; **every redirect hop re-validated** (max 3).
- Response bodies are streamed with a hard cap (`NEWS_MAX_RESPONSE_BYTES`, default 2 MB) and a
  timeout (`NEWS_FETCH_TIMEOUT_S`, default 15 s).
- Bounded concurrency across feeds (3), per-feed minimum intervals, and each feed's own `<ttl>`
  raises its interval - the feed's requested pace always wins.
- Conditional GET (`ETag` / `If-Modified-Since`): an unchanged feed costs a 304.
- Publisher terms: only the headline, a short summary (≤600 chars), timestamps and the article
  link are stored. Full article text is never copied, and attachments are never downloaded.

## Honesty rules (the part that matters for research)

- **Timestamps are three distinct facts**: the publisher's `published_at`, the collector's
  `received_at` (never backdated), and the archive's `imported_at` (the sync's wall clock).
- **A failed feed is unknown coverage, not "no news"**: the first failure of an episode raises a
  WARNING alert (`news_feed_down:<key>`) and records one `NewsCoverageGap` (from the last
  success, or an explicit *unknown* start when the feed never succeeded); recovery raises an
  INFO alert. `/api/news/feeds` shows `failing`, `quiet`, `empty`, `active`, `disabled`,
  `unverified` or `owned` - never a bare zero.
- **Duplicates keep provenance**: the same story from two feeds stores both records; the later
  one carries `duplicate_of` pointing at the first-seen id. Nothing is overwritten.
- **Symbol matching never guesses**: matches carry evidence ("company name 'infosys' in the
  title"), word-boundary matching prevents fragment false positives, and an ambiguous mention
  stays `resolved: false` - it is shown in the UI as *unresolved* and is never classified or
  synced into research as some instrument.
- **Classification stays inside what the evidence supports.** The deterministic rule table
  (`NEWS_RULES_VERSION`) assigns a category from unambiguous wording and a direction only where
  the repository's own event-mapping conventions back it (order win → bullish, penalty →
  bearish, acquisition → mixed, rumor → **unknown**, results → **unknown** - the numbers, not
  the category, decide). Everything else is "Unclear - needs review". When a decision model is
  configured (the existing Laya→Jev cascade, task `news`; no new provider or credential), the
  model leads and the rules are the floor for whatever it abstains on - an abstention is never
  an approval. `confidence_basis` labels every number: `rule` is a fixed prior (0.35), `model`
  is uncalibrated, `model_calibrated` is the only calibrated reading.

## Trading connection

News **cannot** bypass stale-data protections, strategy rules, advisor vetoes, position sizing,
risk limits or kill switches, because it never enters the order path directly:

- Classified news with a resolved instrument joins `research_events()` with the same
  point-in-time guard as announcements (`known_at` = max of published, received and classified
  timestamps), so it flows into the existing research cards - the console's *catalyst watch*
  vocabulary (WATCH / SETUP_FORMING / CONFIRMED) - forecasts, calibration and settled outcomes.
- Traded signals remain the price strategies' output inside the entry window. The UI keeps the
  distinction explicit: **Catalyst watch** (research visibility) vs a **decision** that passed
  AI review and risk checks (Decisions screen, full lineage).
- Outcome measurement (before trusting any of it): synced news records are evaluated by the
  existing research machinery at its configured horizons, reported per event type and direction
  with sample counts and uncertainty; `outcomes --mode actual` reads only forward-captured
  records. A tiny sample is reported as a tiny sample.

## Console

The **News** screen (Research group) shows: feed health with coverage gaps first, then items -
plain-language reading, category/direction/impact/horizon badges, confidence *with its basis*,
rationale, symbol chips with evidence, source and both timestamps, a link to the original
article, and the status chip (Catalyst watch / Unclear - needs review / Not relevant).
Classifier internals (model, rules version, evidence, uncertainties, ids) live in a collapsed
"Classifier details" section. Filters: symbol, category, direction, source, date.

## Settings

| Setting | Default | Effect |
| --- | --- | --- |
| `NEWS_ENABLED` | `true` | Poll the registry's enabled+verified feeds during a session (a registry with none is a no-op) |
| `NEWS_FEEDS_FILE` | `src/config/news_feeds.yaml` | The registry |
| `NEWS_MAX_RESPONSE_BYTES` | `2000000` | Hard cap per feed response |
| `NEWS_FETCH_TIMEOUT_S` | `15` | Per-feed fetch timeout |

## Observability

Every poll logs its shape so "no items" is never mysterious:

- at startup: `news collector configured: feeds=... store=... environment=...` (the exact store
  the console reads via `GET /api/news`), plus an INFO alert `news_feeds_active` listing feeds,
  and `news_feeds_unverified` naming enabled-but-unverified feeds that stay disabled;
- per poll: `news poll starting: N of M configured feed(s) due (...)`, then per feed either
  `fetched X item(s), Y new`, `unchanged (304), 0 new item(s)`, or `FAILED (<reason>)`;
- per step: `news poll: N feed(s) polled, X item(s) collected, Y reading(s) classified`.

Feed failures additionally emit one `news_feed_down:<key>` WARNING alert and one
`NewsCoverageGap` per failure episode (recovery emits an INFO alert), and
`GET /api/news/feeds` reports the resulting status.

## Known limitations

- The shipped registry verifies nothing by itself: until the operator runs
  `scripts/verify_news_feeds.py` and enables feeds, the News screen is an honest empty state.
- Rule-based direction exists only where the mapping conventions back it; impact and horizon
  are almost never rule-decidable and stay `unknown` without a model.
- Media feeds repeat and reword stories; cross-feed dedupe is by normalised title, so near
  duplicates with reworded headlines remain separate items (both visible, neither merged).
- No paywalled, scraped or undocumented source is used, and none should be added: registry URLs
  must be public RSS/Atom feeds whose terms permit this polling.
