// 11. News & events: the collected public news feeds, what the system read from each item, and
// the health of the feeds themselves. Research visibility only: nothing on this screen places an
// order. "Catalyst watch" is a monitoring state - a traded signal only exists once it has passed
// the AI review and the risk checks, and those live under Decisions. Every reading keeps its
// original technical terms (tooltips and the collapsed Classifier details).

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { cx } from "../lib/cx";
import {
  CONFIDENCE_BASIS,
  FEED_STATUS,
  NEWS_CATEGORIES,
  NEWS_DIRECTIONS,
  NEWS_HORIZONS,
  NEWS_IMPACTS,
  NEWS_SOURCE_TYPES,
  READING_STATUS,
  plain,
} from "../lib/explain";
import { num, relative } from "../lib/format";
import { Icon } from "../shell/icons";
import { Badge, type Tone } from "../ui/Badge";
import { Collapsible } from "../ui/Collapsible";
import { TimeIST } from "../ui/Num";
import { Page, PageHeader, Toolbar } from "../ui/PageHeader";
import { Panel } from "../ui/Panel";
import { QueryGate } from "../ui/QueryState";
import { DateFilter, Select, SymbolLink, TextFilter } from "./common";

type NewsRow = Schemas["NewsRow"];
type Reading = Schemas["NewsReadingRow"];
type FeedRow = Schemas["NewsFeedRow"];

const CATEGORIES = Object.keys(NEWS_CATEGORIES);
const DIRECTIONS = Object.keys(NEWS_DIRECTIONS);

const DIRECTION_TONE: Record<string, Tone> = {
  bullish: "up",
  bearish: "down",
  mixed: "warn",
  neutral: "neutral",
  unknown: "neutral",
};

const FEED_TONE: Record<string, Tone> = {
  active: "up",
  quiet: "neutral",
  failing: "warn",
  empty: "neutral",
  disabled: "neutral",
  unverified: "warn",
  owned: "accent",
  invalid: "crit",
};

type ReadingStatus = "catalyst_watch" | "unclear" | "not_relevant";

/** What the system decided to do about one reading - derived, never guessed. */
export function readingStatus(r: Reading): ReadingStatus {
  if (r.category === "unknown" || r.confidence_basis === "none") return "unclear";
  return r.relevant ? "catalyst_watch" : "not_relevant";
}

// -- one classification ---------------------------------------------------------------------------

function ReadingCard({ reading: r }: { reading: Reading }) {
  const status = readingStatus(r);
  const statusExplain = READING_STATUS[status]!;
  const category = plain(NEWS_CATEGORIES, r.category);
  const direction = plain(NEWS_DIRECTIONS, r.direction);
  const impact = plain(NEWS_IMPACTS, r.impact);
  const horizon = plain(NEWS_HORIZONS, r.horizon);
  const basis = plain(CONFIDENCE_BASIS, r.confidence_basis);
  return (
    <div className="rounded-md border border-line bg-bg-1 px-3 py-2.5">
      <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1.5">
        <Badge
          size="sm"
          tone={status === "catalyst_watch" ? "accent" : status === "unclear" ? "warn" : "neutral"}
          title={statusExplain.plain}
        >
          {statusExplain.label}
        </Badge>
        <SymbolLink symbol={r.symbol} />
        <Badge size="sm" tone="neutral" title={`${category.plain} (category: ${r.category})`}>
          {category.label}
        </Badge>
        <Badge
          size="sm"
          tone={DIRECTION_TONE[r.direction] ?? "neutral"}
          title={`${direction.plain} (direction: ${r.direction})`}
        >
          {direction.label}
        </Badge>
        <Badge size="sm" tone="neutral" title={`${impact.plain} (impact: ${r.impact})`}>
          {impact.label}
        </Badge>
        <Badge size="sm" tone="neutral" title={`${horizon.plain} (horizon: ${r.horizon})`}>
          {horizon.label}
        </Badge>
        {r.confidence !== null && (
          <span className="num text-2xs text-fg-2" title={basis.plain}>
            confidence {num(r.confidence, 2)} · {basis.label}
          </span>
        )}
      </div>
      <p className="mt-2 text-xs leading-relaxed text-fg-1">{r.rationale}</p>
      <Collapsible
        className="mt-2 border-line bg-bg-2"
        title="Classifier details"
        summary={`${r.model} · ${r.rules_version} · classified ${relative(r.classified_at)}`}
      >
        <div className="flex flex-col gap-2 p-3 text-xs">
          {r.uncertainties.length > 0 && (
            <div>
              <p className="rq-label">Uncertainties</p>
              <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4 text-fg-1">
                {r.uncertainties.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </div>
          )}
          {r.evidence.length > 0 && (
            <div>
              <p className="rq-label">Evidence</p>
              <ul className="mt-1 flex list-disc flex-col gap-0.5 pl-4 text-fg-1">
                {r.evidence.map((e, i) => (
                  <li key={i} className="break-words">{e}</li>
                ))}
              </ul>
            </div>
          )}
          <p className="font-mono text-2xs text-fg-2">
            news_id {r.news_id} · instrument {r.instrument_key}
          </p>
        </div>
      </Collapsible>
    </div>
  );
}

// -- one item --------------------------------------------------------------------------------------

function NewsCard({ row }: { row: NewsRow }) {
  const sourceType = plain(NEWS_SOURCE_TYPES, row.source_type);
  return (
    <li className="rq-card flex flex-col gap-3 px-4 py-3.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
        <Badge
          size="sm"
          tone={row.source_type === "exchange_filing" ? "accent" : "neutral"}
          title={`${sourceType.plain} (source: ${row.source_key})`}
        >
          {row.source_name}
        </Badge>
        <span className="text-2xs text-fg-2">{sourceType.label}</span>
        <span className="num text-2xs text-fg-2">
          published <TimeIST value={row.published_at} label /> · received {relative(row.received_at)}
        </span>
        {row.duplicate_of && (
          <Badge
            size="sm"
            tone="neutral"
            title={`The same story was first collected as ${row.duplicate_of}; both records are kept with their own provenance`}
          >
            also seen in another feed
          </Badge>
        )}
      </div>

      <div>
        {row.url ? (
          <a
            href={row.url}
            target="_blank"
            rel="noreferrer noopener"
            className="text-sm font-medium text-fg-0 hover:text-accent-fg hover:underline"
          >
            {row.title} <Icon name="external" size={12} className="inline align-baseline" />
          </a>
        ) : (
          <span className="text-sm font-medium text-fg-0">{row.title}</span>
        )}
        {row.summary && <p className="mt-1 text-xs leading-relaxed text-fg-1">{row.summary}</p>}
      </div>

      <div className="flex flex-wrap items-center gap-1.5">
        {row.symbols.length === 0 && (
          <span className="text-2xs text-fg-2">
            No universe symbol matched - kept for the record, attributed to no stock.
          </span>
        )}
        {row.symbols.map((m) =>
          m.resolved && m.instrument_key ? (
            <span key={`${m.symbol}|${m.instrument_key}`} title={m.evidence}>
              <SymbolLink symbol={m.symbol} />
            </span>
          ) : (
            <Badge key={`${m.symbol}|unresolved`} size="sm" tone="warn" title={m.evidence}>
              {m.symbol} · unresolved
            </Badge>
          ),
        )}
      </div>

      {row.readings.length === 0 ? (
        <p className="text-xs text-fg-2">
          {row.symbols.some((s) => !s.resolved)
            ? "Not attributed: the company mention is ambiguous, so nothing is guessed. It stays visible for review."
            : "No classification recorded yet - readings appear once the news task classifies the item during a session."}
        </p>
      ) : (
        <div className="flex flex-col gap-2">
          {row.readings.map((r) => (
            <ReadingCard key={`${r.news_id}|${r.instrument_key}`} reading={r} />
          ))}
        </div>
      )}
    </li>
  );
}

// -- feed health ------------------------------------------------------------------------------------

function useFeeds() {
  return useQuery({
    queryKey: keys.newsFeeds,
    queryFn: ({ signal }) => get("/api/news/feeds", { signal }),
    refetchInterval: 30_000,
  });
}

function FeedHealth() {
  const feeds = useFeeds();
  const rows: FeedRow[] = feeds.data ?? [];
  const failing = rows.filter((f) => f.status === "failing").length;
  return (
    <Collapsible
      title="Feed health"
      summary="Every configured feed with its state, item count and coverage gaps - a failing feed is UNKNOWN coverage, never 'no news'"
      defaultOpen={failing > 0}
      badge={
        failing > 0 ? (
          <Badge tone="warn">{failing} failing</Badge>
        ) : (
          <Badge tone="neutral">{rows.length} feeds</Badge>
        )
      }
      bodyClassName="max-h-[360px] overflow-auto"
    >
      <QueryGate
        isLoading={feeds.isLoading}
        isError={feeds.isError}
        error={feeds.error}
        data={rows}
        isEmpty={(r) => r.length === 0}
        emptyTitle="No feeds configured"
        emptyHint="Feeds live in src/config/news_feeds.yaml. A feed is fetched only when enabled AND verified by scripts/verify_news_feeds.py."
        className="px-4 py-4"
        skeletonRows={3}
      >
        {(items) => (
          <ul className="divide-y divide-line">
            {items.map((f) => {
              const status = plain(FEED_STATUS, f.status);
              const type = plain(NEWS_SOURCE_TYPES, f.type);
              return (
                <li key={f.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2.5">
                  <Badge size="sm" tone={FEED_TONE[f.status] ?? "neutral"} title={status.plain}>
                    {status.label}
                  </Badge>
                  <span className="text-sm text-fg-0">{f.name}</span>
                  <span className="text-2xs text-fg-2" title={type.plain}>
                    {type.label}
                  </span>
                  <span className="num ml-auto text-2xs text-fg-2">
                    {f.items_total} item(s)
                    {f.last_item_at ? ` · last ${relative(f.last_item_at)}` : ""}
                    {f.gaps > 0 ? ` · ${f.gaps} coverage gap(s)` : ""}
                  </span>
                  {f.last_error && <span className="w-full text-2xs text-warn">{f.last_error}</span>}
                  {f.status === "unverified" && (
                    <span className="w-full font-mono text-2xs text-fg-2">
                      enable with: scripts/verify_news_feeds.py --key {f.key}
                    </span>
                  )}
                </li>
              );
            })}
          </ul>
        )}
      </QueryGate>
    </Collapsible>
  );
}

// -- the screen --------------------------------------------------------------------------------------

export default function News() {
  const [symbol, setSymbol] = useState("");
  const [category, setCategory] = useState("");
  const [direction, setDirection] = useState("");
  const [source, setSource] = useState("");
  const [date, setDate] = useState<string | null>(null);
  const feeds = useFeeds();
  const query = {
    symbol: symbol.trim().toUpperCase() || undefined,
    category: category || undefined,
    direction: direction || undefined,
    source: source || undefined,
    date: date ?? undefined,
    limit: 200,
  };
  const news = useQuery({
    queryKey: keys.news(query),
    queryFn: ({ signal }) => get("/api/news", { query, signal }),
    refetchInterval: 15_000,
  });
  const rows = news.data ?? [];
  const catalysts = rows.filter((r) => r.readings.some((x) => readingStatus(x) === "catalyst_watch")).length;

  return (
    <Page>
      <PageHeader
        title="News & events"
        description="Collected public news feeds and what the system read from each item. A catalyst watch is research visibility, not an order: traded signals appear under Decisions after AI review and risk checks."
      />

      <FeedHealth />

      <Toolbar>
        <TextFilter label="Symbol" value={symbol} onChange={setSymbol} placeholder="e.g. INFY" width="w-32" />
        <Select
          label="Category"
          value={category}
          onChange={setCategory}
          options={[
            { value: "", label: "All categories" },
            ...CATEGORIES.map((c) => ({ value: c, label: NEWS_CATEGORIES[c]!.label })),
          ]}
        />
        <Select
          label="Direction"
          value={direction}
          onChange={setDirection}
          options={[
            { value: "", label: "All directions" },
            ...DIRECTIONS.map((d) => ({ value: d, label: NEWS_DIRECTIONS[d]!.label })),
          ]}
        />
        <Select
          label="Source"
          value={source}
          onChange={setSource}
          options={[
            { value: "", label: "All sources" },
            ...(feeds.data ?? []).map((f) => ({ value: f.key, label: f.name })),
          ]}
        />
        <DateFilter value={date ?? ""} onChange={setDate} />
      </Toolbar>

      <Panel
        title={`Items · ${rows.length}`}
        description={
          catalysts > 0
            ? `${catalysts} item(s) carry a catalyst watch. Newest publication first; every reading keeps its evidence.`
            : "Newest publication first; every reading keeps its evidence."
        }
        bodyClassName="max-h-[68vh] overflow-auto"
        scrollable
      >
        <QueryGate
          isLoading={news.isLoading}
          isError={news.isError}
          error={news.error}
          data={rows}
          isEmpty={(r) => r.length === 0}
          emptyTitle="No news items match"
          emptyHint="The collector polls enabled+verified feeds while a session runs. The shipped registry keeps media feeds disabled until verified (scripts/verify_news_feeds.py); Feed health above shows what is configured. Widen the filters or the date to see older items."
          className="px-4 py-6"
          skeletonRows={4}
        >
          {(items) => (
            <ul className={cx("flex flex-col gap-3 p-4")}>
              {items.map((row: NewsRow) => (
                <NewsCard key={row.news_id} row={row} />
              ))}
            </ul>
          )}
        </QueryGate>
      </Panel>
    </Page>
  );
}
