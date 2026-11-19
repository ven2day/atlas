// The app header: product and mode, session state, the session clock (IST), data freshness,
// broker and reconciliation, the books' kill switches, today's AI spend against its cap, and
// HALT. Nothing here is hidden on a small screen - the status strip wraps and scrolls instead.

import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { get } from "../api/client";
import { keys, useConfig, useSummary, useSystem } from "../api/queries";
import { cx } from "../lib/cx";
import { SESSION_STATES, dataFreshness } from "../lib/explain";
import { duration } from "../lib/format";
import { useUi } from "../lib/ui";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { Money, TimeIST } from "../ui/Num";
import { HaltDialog } from "./HaltDialog";
import { Icon } from "./icons";

/** Today's IST date of the session clock (the demo's clock is its tape's day). */
export function sessionDay(now: string | null | undefined): string {
  const d = now ? new Date(now) : new Date();
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Kolkata" }).format(d);
}

/** One labelled reading in the status strip. */
function Reading({
  icon,
  label,
  children,
  tone = "plain",
  title,
}: {
  icon?: string;
  label: string;
  children: ReactNode;
  tone?: "plain" | "warn" | "crit";
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "flex shrink-0 items-center gap-1.5 text-xs",
        tone === "warn" && "text-warn",
        tone === "crit" && "text-down",
      )}
    >
      {icon && <Icon name={icon} size={14} className="text-fg-2" />}
      <span className="text-fg-2">{label}</span>
      <span className={cx("num whitespace-nowrap", tone === "plain" && "text-fg-0")}>{children}</span>
    </span>
  );
}

function BrandMark() {
  return (
    <span
      aria-hidden="true"
      className="flex h-8 w-8 items-center justify-center rounded-md border border-accent-line bg-accent-soft text-accent-fg"
    >
      <Icon name="market" size={18} strokeWidth={1.7} />
    </span>
  );
}

export function TopBar() {
  const { data: summary } = useSummary();
  const { data: system } = useSystem();
  const { data: config } = useConfig();
  const { halt, setHalt, setNavOpen } = useUi();
  const day = sessionDay(summary?.now);
  const spend = useQuery({
    queryKey: keys.aiSpend({ group_by: "day", since: day, until: day }),
    queryFn: ({ signal }) =>
      get("/api/ai/spend", { query: { group_by: "day", since: day, until: day }, signal }),
    refetchInterval: 30_000,
  });

  const ages = Object.entries(system?.quote_age_s ?? {}).sort((a, b) => a[1] - b[1]);
  const freshest = ages[0];
  const freshness = dataFreshness(freshest ? freshest[1] : null);
  const reconciles = system?.last_reconcile ?? [];
  const drift = reconciles.some((r) => !r.in_sync);
  const open = summary?.market_open ?? false;
  const product = (config?.experiment as { product?: string } | undefined)?.product ?? "CNC";
  const budget = config ? Number(config.llm_budget_daily_inr) : null;

  return (
    <header className="z-30 shrink-0 border-b border-line bg-bg-1">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2.5 sm:px-5">
        <button
          type="button"
          onClick={() => setNavOpen(true)}
          aria-label="Open navigation"
          className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md border border-line bg-bg-2 text-fg-1 transition-colors duration-150 hover:text-fg-0 lg:hidden"
        >
          <Icon name="menu" size={18} />
        </button>
        <Link to="/" className="flex shrink-0 items-center gap-2.5 rounded-md" aria-label="RakshaQuant home">
          <BrandMark />
          <span className="text-base font-semibold tracking-tight text-fg-0">RakshaQuant</span>
        </Link>

        {summary?.demo ? (
          <Badge tone="warn" title="The bundled fixture tape replayed through the real engine">
            DEMO
          </Badge>
        ) : (
          <Badge tone="accent" title={`Paper trading, ${product}`}>
            {`PAPER · ${product}`}
          </Badge>
        )}

        {/* The status strip: one instance, wrapping and scrolling rather than hiding. */}
        <div
          className={cx(
            "order-last flex w-full items-center gap-4 overflow-x-auto py-1",
            "lg:order-none lg:ml-2 lg:w-auto lg:flex-1 lg:py-0",
          )}
        >
          <Reading icon="clock" label="Session" title={summary?.session?.state ? SESSION_STATES[summary.session.state]?.plain : undefined}>
            <span className="flex items-center gap-1.5">
              <span
                aria-hidden="true"
                className={cx("h-1.5 w-1.5 rounded-full", open ? "bg-accent" : "bg-fg-2")}
              />
              <span className="text-fg-0">{summary?.session?.state ?? "NO SESSION"}</span>
              <span className="text-fg-2">{open ? "open" : "closed"}</span>
            </span>
          </Reading>
          <TimeIST value={summary?.now} label className="shrink-0 text-xs text-fg-1" />
          <Reading icon="database" label="Data" title={freshness.detail}>
            <span
              className={cx(
                freshness.state === "stale" && "text-warn",
                freshness.state === "none" && "text-fg-2",
                (freshness.state === "live" || freshness.state === "delayed") && "text-fg-0",
              )}
            >
              {freshest ? freshest[0].toUpperCase() : ""} {freshness.label}
              {freshest && (freshness.state === "delayed" || freshness.state === "stale") &&
                ` · ${duration(freshest[1])} old`}
            </span>
          </Reading>
          <Reading icon="wallet" label="Broker" tone={drift ? "warn" : "plain"}>
            <span className="text-fg-0">SIM</span>
            <span className={cx("ml-1", drift ? "text-warn" : "text-fg-2")}>
              {reconciles.length === 0 ? "not reconciled" : drift ? "drift" : "in sync"}
            </span>
          </Reading>
          <span className="flex shrink-0 items-center gap-1.5 text-xs" title="Kill switches by book">
            <Icon name="book" size={14} className="text-fg-2" />
            <span className="text-fg-2">Books</span>
            {summary?.books.map((b) => (
              <Badge
                key={b.book_id}
                size="sm"
                tone={b.kill_switch === "ARMED" ? "neutral" : b.kill_switch === "FLATTEN" ? "crit" : "warn"}
                title={`Book ${b.book_id}: ${b.advisor}, kill switch ${b.kill_switch}`}
              >
                {b.kill_switch === "ARMED" ? b.book_id : `${b.book_id} ${b.kill_switch === "HALT_NEW" ? "HALT" : b.kill_switch}`}
              </Badge>
            ))}
          </span>
        </div>

        <div className="ml-auto flex shrink-0 items-center gap-3">
          <span className="hidden items-center gap-1.5 text-xs text-fg-2 sm:flex" title="Today's AI spend against the daily cap">
            <Icon name="spark" size={14} />
            AI today <Money value={spend.data?.total_inr} className="text-fg-0" /> /{" "}
            {budget === null ? "—" : budget > 0 ? <Money value={budget} /> : "no cap"}
          </span>
          <Button
            variant="danger"
            size="sm"
            onClick={() => setHalt(true)}
            title="Block new entries in every book"
          >
            <Icon name="stop" size={14} strokeWidth={1.8} />
            HALT
          </Button>
        </div>
      </div>
      <HaltDialog open={halt} onOpenChange={setHalt} />
    </header>
  );
}
