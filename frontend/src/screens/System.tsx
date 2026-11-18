// 8. System: the engine's tasks and health, feed freshness, the store, reconciliation, the
// read-only configuration, a filterable log tail, and every version that shapes a decision.

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { get } from "../api/client";
import { useConfig, useSystem } from "../api/queries";
import { cx } from "../lib/cx";
import { duration, num } from "../lib/format";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { SelectInput, TextInput } from "../ui/Input";
import { TimeIST } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Empty, Panel } from "../ui/Panel";
import { QueryGate } from "../ui/QueryState";
import { StatCard } from "../ui/Stat";
import { Table } from "../ui/Table";

function bytes(n: number): string {
  if (n >= 1 << 30) return `${num(n / (1 << 30), 2)} GB`;
  if (n >= 1 << 20) return `${num(n / (1 << 20), 1)} MB`;
  return `${num(n / 1024, 0)} KB`;
}

const LEVEL_TONE: Record<string, string> = {
  WARNING: "text-warn",
  ERROR: "text-down",
  CRITICAL: "text-down",
};

export default function System() {
  const { data: system } = useSystem();
  const { data: config } = useConfig();
  const [level, setLevel] = useState("");
  const [contains, setContains] = useState("");
  const query = { level: level || undefined, contains: contains.trim() || undefined, limit: 300 };
  const logs = useQuery({ queryKey: ["logs", query], queryFn: ({ signal }) => get("/api/logs", { query, signal }), refetchInterval: 10_000 });
  const ages = Object.entries(system?.quote_age_s ?? {});

  return (
    <Page>
      <PageHeader
        title="System health"
        description="Whether the engine is running, how fresh its data is, whether the OMS and the broker agree, and exactly which versions produced today's decisions."
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard
          label="Engine"
          size="sm"
          value={
            <span className={cx("flex items-center gap-2", system?.running ? "text-up" : "text-fg-1")}>
              <span aria-hidden="true" className={cx("h-2 w-2 rounded-full", system?.running ? "bg-up" : "bg-fg-2")} />
              {system?.running ? "running" : "idle"}
            </span>
          }
          hint={
            system?.last_heartbeat ? (
              <>
                heartbeat <TimeIST value={system.last_heartbeat} label />
              </>
            ) : (
              "no heartbeat yet"
            )
          }
        />
        <StatCard label="Uptime" size="sm" value={system?.uptime_s == null ? "—" : duration(system.uptime_s)} hint="since the process started" />
        <StatCard
          label="Loop lag"
          size="sm"
          value={system?.loop_lag_ms == null ? "—" : `${num(system.loop_lag_ms, 0)} ms`}
          hint={system?.loop_lag_ms != null && system.loop_lag_ms > 500 ? "above 500 ms: the loop is falling behind" : "the engine's own tick delay"}
        />
        <StatCard
          label="Event store"
          size="sm"
          value={system ? bytes(system.store_bytes) : "—"}
          hint={system ? `seq ${num(system.last_seq, 0)} · schema v${system.schema_version}` : "—"}
        />
      </div>

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <Panel title="Tasks and feeds" description="What the engine is running, and how old its newest quote is">
          <div className="flex flex-col gap-4 p-4">
            <div className="flex flex-col gap-2">
              <span className="rq-label">Engine tasks</span>
              <div className="flex flex-wrap gap-1.5">
                {system?.tasks.length ? (
                  system.tasks.map((t) => <Badge key={t} mono size="sm">{t}</Badge>)
                ) : (
                  <span className="text-sm text-fg-2">No engine tasks (no session running).</span>
                )}
              </div>
            </div>
            <div className="flex flex-col gap-2">
              <span className="rq-label">Feed freshness (age of the newest quote, by source)</span>
              {ages.length ? (
                <ul className="flex flex-wrap gap-2">
                  {ages.map(([source, age]) => (
                    <li key={source} className={cx("flex items-center gap-2 rounded-md border border-line bg-bg-2 px-3 py-1.5 text-xs", age > 1800 && "border-warn")}>
                      <Icon name="database" size={14} className={age > 1800 ? "text-warn" : "text-fg-2"} />
                      <span className="font-mono text-fg-1">{source}</span>
                      <span className={cx("num", age > 1800 ? "text-warn" : "text-fg-0")}>{duration(age)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <span className="text-sm text-fg-2">No quotes in memory.</span>
              )}
            </div>
            <p className="font-mono text-2xs break-all text-fg-2">{system?.store_path}</p>
          </div>
        </Panel>

        <Panel title="Reconciliation (OMS vs broker)" description="Run pre-open and every 15 minutes during a session">
          {system?.last_reconcile.length ? (
            <Table
              minWidth={720}
              head={["Book", "Scope", "State", "At (IST)", "Differences"]}
              rows={system.last_reconcile.map((r) => [
                <span key="b" className="font-medium">{r.book_id}</span>,
                <span key="s" className="font-mono text-xs text-fg-1">{r.scope}</span>,
                r.in_sync ? <Badge key="st" tone="up">in sync</Badge> : <Badge key="st" tone="warn">drift</Badge>,
                <TimeIST key="t" value={r.ts} label />,
                <span key="d" className="text-fg-1">{r.diffs.join("; ") || "—"}</span>,
              ])}
            />
          ) : (
            <Empty>Not reconciled yet (each session reconciles pre-open and every 15 minutes).</Empty>
          )}
        </Panel>

        <Panel title="Versions" description="Every version that could change a decision">
          <dl className="grid grid-cols-[minmax(120px,180px)_minmax(0,1fr)] gap-x-4 gap-y-2 p-4 text-sm">
            {Object.entries(system?.versions ?? {}).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-fg-2">{k}</dt>
                <dd className="truncate font-mono text-xs text-fg-0" title={v}>{v}</dd>
              </div>
            ))}
            <dt className="text-fg-2">risk limits</dt>
            <dd className="truncate font-mono text-xs text-fg-0">{config?.limits_hash ?? "—"}</dd>
            {Object.entries(system?.prompts ?? {}).map(([k, v]) => (
              <div key={k} className="contents">
                <dt className="text-fg-2">prompt {k}</dt>
                <dd className="truncate font-mono text-xs text-fg-0" title={v}>{v}</dd>
              </div>
            ))}
            {Object.entries(config?.llm_roles ?? {}).map(([role, chain]) => (
              <div key={role} className="contents">
                <dt className="text-fg-2">role {role}</dt>
                <dd className="truncate font-mono text-xs text-fg-0">
                  {chain.length ? chain.join(" → ") : <span className="text-fg-2">not configured</span>}
                </dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel title="Configuration" description="Read-only and redacted: secrets never reach the browser">
          <pre
            tabIndex={0}
            role="region"
            aria-label="Configuration, read-only and redacted"
            className="max-h-[420px] overflow-auto whitespace-pre-wrap p-4 font-mono text-xs leading-relaxed text-fg-1"
          >
            {config ? JSON.stringify(config, null, 2) : "—"}
          </pre>
        </Panel>
      </div>

      <Panel
        title="Logs"
        description="Newest first, from the engine's own log stream"
        className="min-h-[420px]"
        bodyClassName="overflow-auto max-h-[560px]"
        scrollable
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <label className="flex items-center gap-2">
              <span className="rq-label">Level</span>
              <SelectInput
                aria-label="Log level"
                value={level}
                onChange={(e) => setLevel(e.target.value)}
                className="h-8 w-32 text-xs"
              >
                {["", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"].map((l) => (
                  <option key={l} value={l}>{l || "All"}</option>
                ))}
              </SelectInput>
            </label>
            <label className="flex items-center gap-2">
              <span className="rq-label">Contains</span>
              <TextInput
                aria-label="Filter logs by text"
                value={contains}
                placeholder="e.g. reconcile"
                onChange={(e) => setContains(e.target.value)}
                className="h-8 w-44 text-xs"
              />
            </label>
          </div>
        }
      >
        <QueryGate
          isLoading={logs.isLoading}
          isError={logs.isError}
          error={logs.error}
          data={logs.data}
          isEmpty={(rows) => rows.length === 0}
          emptyTitle="No log lines match these filters"
          emptyHint="Widen the level filter or clear the text box. These are the engine's own JSON lines from var/logs/, newest last."
          className="p-4"
          skeletonRows={6}
        >
          {(rows) => (
            <ul className="divide-y divide-line font-mono text-2xs leading-relaxed">
              {rows.map((l, i) => (
                <li key={i} className="flex flex-wrap gap-x-3 gap-y-0.5 px-4 py-1.5">
                  <TimeIST value={l.ts} className="shrink-0 text-fg-2" />
                  <span className={cx("w-16 shrink-0", LEVEL_TONE[l.level] ?? "text-fg-2")}>{l.level}</span>
                  <span className="w-52 shrink-0 truncate text-fg-2" title={l.logger}>{l.logger}</span>
                  <span className="min-w-0 flex-1 break-words text-fg-0">{l.message}</span>
                </li>
              ))}
            </ul>
          )}
        </QueryGate>
      </Panel>
    </Page>
  );
}
