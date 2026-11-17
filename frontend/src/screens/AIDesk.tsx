// 5. AI desk: which model plays which role, how healthy and fast it is, what it costs, the
// decision models and their calibration, and whether the advisors earn their keep.

import { useQuery } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { useMemo } from "react";

import { get, type Schemas } from "../api/client";
import { keys, useConfig } from "../api/queries";
import { num, pct } from "../lib/format";
import { Badge } from "../ui/Badge";
import { Chart } from "../ui/Chart";
import { DataTable, type Columns } from "../ui/DataTable";
import { Money, TimeIST } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Empty, EmptyState, Panel } from "../ui/Panel";
import { StatCard } from "../ui/Stat";
import { Table } from "../ui/Table";
import { useSessionDay } from "./common";

type Call = Schemas["LLMCallRow"];
type Spend = Schemas["SpendRow"];

const c = createColumnHelper<Call>();
const CALL_COLUMNS: Columns<Call> = [
  c.accessor("ts", { header: "Time (IST)", size: 104, cell: (x) => <TimeIST value={x.getValue()} /> }),
  c.accessor("role", { header: "Role", size: 92 }),
  c.accessor("book_id", { header: "Book", size: 68, cell: (x) => x.getValue() ?? "—" }),
  c.accessor((r) => `${r.provider}:${r.model}`, { id: "model", header: "Model", size: 240, cell: (x) => <span className="font-mono text-xs">{x.getValue()}</span> }),
  c.accessor("outcome", { header: "Outcome", size: 116, cell: (x) => <Badge tone={x.getValue() === "ok" ? "neutral" : "warn"}>{x.getValue()}</Badge> }),
  c.accessor("tokens_in", { header: "Tokens in", size: 100, meta: { align: "right" }, cell: (x) => num(x.getValue(), 0) }),
  c.accessor("tokens_out", { header: "Tokens out", size: 108, meta: { align: "right" }, cell: (x) => num(x.getValue(), 0) }),
  c.accessor("latency_ms", { header: "Latency", size: 96, meta: { align: "right" }, cell: (x) => `${num(x.getValue(), 0)} ms` }),
  c.accessor("cost_inr", { header: "Cost", size: 96, meta: { align: "right" }, cell: (x) => <Money value={x.getValue()} /> }),
  c.accessor("cache_hit", { header: "Cache", size: 80, cell: (x) => (x.getValue() ? <Badge tone="accent" size="sm">hit</Badge> : <span className="text-fg-2">—</span>) }),
  c.accessor("prompt_version", { header: "Prompt", size: 190, cell: (x) => <span className="font-mono text-xs text-fg-1">{x.getValue()}</span> }),
];

function SpendBars({ rows, empty }: { rows: Spend[]; empty: string }) {
  if (!rows.length) return <EmptyState title={empty} hint="Spend appears the first time a role is actually called." className="py-8" />;
  const max = Math.max(...rows.map((r) => Number(r.cost_inr)), 0.0001);
  return (
    <ul className="flex flex-col gap-3 p-4 text-sm">
      {rows.map((r) => (
        <li key={r.key} className="grid grid-cols-[minmax(90px,190px)_1fr_auto] items-center gap-3">
          <span className="truncate font-mono text-xs text-fg-1" title={r.key}>{r.key}</span>
          <span className="h-2 overflow-hidden rounded-full bg-bg-3" aria-hidden="true">
            <span className="block h-full rounded-full bg-accent" style={{ width: `${(Number(r.cost_inr) / max) * 100}%` }} />
          </span>
          <span className="num flex shrink-0 items-baseline gap-2 text-right">
            <span className="font-medium text-fg-0"><Money value={r.cost_inr} /></span>
            <span className="text-2xs text-fg-2">{num(r.calls, 0)} calls</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function AIDesk() {
  const day = useSessionDay();
  const monthStart = `${day.slice(0, 8)}01`;
  const { data: config } = useConfig();
  const models = useQuery({ queryKey: keys.aiModels, queryFn: ({ signal }) => get("/api/ai/models", { signal }), refetchInterval: 30_000 });
  const calls = useQuery({ queryKey: keys.aiCalls({ limit: 500 }), queryFn: ({ signal }) => get("/api/ai/calls", { query: { limit: 500 }, signal }) });
  const today = useQuery({ queryKey: keys.aiSpend({ group_by: "role", since: day }), queryFn: ({ signal }) => get("/api/ai/spend", { query: { group_by: "role", since: day, until: day }, signal }) });
  const month = useQuery({ queryKey: keys.aiSpend({ group_by: "model", since: monthStart }), queryFn: ({ signal }) => get("/api/ai/spend", { query: { group_by: "model", since: monthStart }, signal }) });
  const dms = useQuery({ queryKey: keys.decisionModels({}), queryFn: ({ signal }) => get("/api/ai/decision-models", { signal }) });
  const calibration = useQuery({ queryKey: ["ai", "calibration"], queryFn: ({ signal }) => get("/api/ai/calibration", { signal }) });
  const books = useQuery({ queryKey: keys.books, queryFn: ({ signal }) => get("/api/books", { signal }) });
  const reports = useQuery({ queryKey: ["reports"], queryFn: ({ signal }) => get("/api/reports", { query: { limit: 90 }, signal }) });

  const netLines = useMemo(() => {
    const days = [...(reports.data ?? [])].reverse();
    const ids = [...new Set(days.flatMap((d) => Object.keys(d.books)))].filter((b) => days.some((d) => d.books[b]?.net_ai_value_inr != null));
    return ids.slice(0, 3).map((b, i) => ({
      name: `${b} − baseline`,
      tone: (["accent", "warn", "neutral"] as const)[i]!,
      points: days.filter((d) => d.books[b]?.net_ai_value_inr != null).map((d) => ({ time: d.date, value: d.books[b]!.net_ai_value_inr! })),
    }));
  }, [reports.data]);
  const dm = config?.decision_models as { laya_enabled?: boolean; laya_checkpoint?: string; jev_configured?: boolean } | undefined;
  const budget = config ? Number(config.llm_budget_daily_inr) : null;
  const enabledRoles = (models.data ?? []).filter((r) => r.enabled).length;

  return (
    <Page>
      <PageHeader
        title="AI desk"
        description="Every model call the engine made, what it cost against the budget, how the local decision models are calibrated, and whether each advisor is still worth keeping."
      />

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        <StatCard label="Spend today" size="sm" value={<Money value={today.data?.total_inr} />} hint={`by role, ${day}`} />
        <StatCard label="Spend this month" size="sm" value={<Money value={month.data?.total_inr} />} hint="by provider and model" />
        <StatCard
          label="Daily cap"
          size="sm"
          value={budget === null ? "—" : budget > 0 ? <Money value={budget} /> : "no cap"}
          hint={budget === null ? "configuration not loaded" : budget > 0 ? "paid calls stop at the cap" : "no budget is set"}
        />
        <StatCard label="Roles configured" size="sm" value={`${num(enabledRoles, 0)} / ${num((models.data ?? []).length, 0)}`} hint="with a model chain" />
      </div>

      <Panel title="Roles and models" description="Primary first, then the fallbacks each role degrades to">
        {!models.data?.length ? (
          <Empty>{models.isLoading ? "Loading…" : "No LLM roles."}</Empty>
        ) : (
          <Table
            minWidth={980}
            head={["Role", "Model (primary first)", "Calls today", "Errors", "p50", "p95", "Last outcome", "Last call (IST)"]}
            align={["l", "l", "r", "r", "r", "r", "l", "l"]}
            rows={models.data.flatMap((role) =>
              role.enabled
                ? role.models.map((m, i) => [
                    <span key="r" className={i === 0 ? "font-medium text-fg-0" : "text-fg-2"}>{i === 0 ? role.role : ""}</span>,
                    <span key="m" className={i === 0 ? "font-mono text-xs" : "font-mono text-xs text-fg-2"}>
                      {i === 0 ? m.model : `fallback · ${m.model}`}
                    </span>,
                    num(m.calls_today, 0),
                    <span key="e" className={m.errors_today > 0 ? "text-warn" : undefined}>{num(m.errors_today, 0)}</span>,
                    m.p50_latency_ms == null ? "—" : `${num(m.p50_latency_ms, 0)} ms`,
                    m.p95_latency_ms == null ? "—" : `${num(m.p95_latency_ms, 0)} ms`,
                    m.last_outcome ? <Badge key="o" tone={m.last_outcome === "ok" ? "neutral" : "warn"}>{m.last_outcome}</Badge> : "—",
                    m.last_call ? <TimeIST key="t" value={m.last_call} /> : "—",
                  ])
                : [
                    [
                      <span key="r" className="font-medium text-fg-0">{role.role}</span>,
                      <span key="m" className="text-fg-2">not configured (LLM_ROLE_{role.role.toUpperCase()})</span>,
                      "—",
                      "—",
                      "—",
                      "—",
                      "—",
                      "—",
                    ],
                  ],
            )}
          />
        )}
      </Panel>

      <div className="grid min-w-0 gap-5 xl:grid-cols-2">
        <Panel title="Spend today, by role" description={`Since ${day} (IST)`}>
          <SpendBars rows={today.data?.rows ?? []} empty="No AI spend today" />
        </Panel>
        <Panel title="Spend this month, by provider and model" description={`Since ${monthStart}`}>
          <SpendBars rows={month.data?.rows ?? []} empty="No AI spend this month" />
        </Panel>

        <Panel title="Decision models" description="The typed local models, their latency and how often they escalate">
          <div className="flex flex-wrap gap-x-6 gap-y-2 border-b border-line px-4 py-3 text-sm">
            <span className="text-fg-1">
              Laya: <span className="font-medium text-fg-0">{dm?.laya_enabled ? `on (${dm.laya_checkpoint ?? "—"})` : "off"}</span>
            </span>
            <span className="text-fg-1">
              Jev: <span className="font-medium text-fg-0">{dm?.jev_configured ? "configured" : "not configured"}</span>
            </span>
          </div>
          {dms.data?.length ? (
            <Table
              minWidth={760}
              head={["Task", "Model", "Calls", "p50", "p95", "Escalated", "Calibrated", "Shadow"]}
              align={["l", "l", "r", "r", "r", "r", "r", "r"]}
              rows={dms.data.map((d) => [
                d.task,
                <span key="m" className="font-mono text-xs">{d.model}:{d.checkpoint}</span>,
                num(d.calls, 0),
                d.p50_latency_ms == null ? "—" : `${num(d.p50_latency_ms, 0)} ms`,
                d.p95_latency_ms == null ? "—" : `${num(d.p95_latency_ms, 0)} ms`,
                d.escalation_rate == null ? "—" : pct(d.escalation_rate * 100),
                d.calibrated_rate == null ? "—" : pct(d.calibrated_rate * 100),
                num(d.shadow_calls, 0),
              ])}
            />
          ) : (
            <Empty>No decision-model calls recorded.</Empty>
          )}
        </Panel>

        <Panel title="Calibration" description="Temperatures fitted against a labelled set">
          {calibration.data?.fitted ? (
            <Table
              head={["Question", "Temperature"]}
              align={["l", "r"]}
              rows={Object.entries(calibration.data.temperatures).map(([k, t]) => [
                <span key="k" className="font-mono text-xs text-fg-1">{k}</span>,
                `T = ${num(t, 3)}`,
              ])}
            />
          ) : (
            <Empty>
              Uncalibrated: the reliability diagram and Brier scores need a labelled set, which the owner skipped for now
              (decision OD-11). Questions Laya is unsure of go to Jev when its key is set; otherwise the typed veto abstains.
            </Empty>
          )}
        </Panel>
      </div>

      <Panel
        title="Veto precision"
        description="Of the signals an advisor vetoed, how many would have lost money had they been taken"
        footer="Pre-registered rule: keep an advisor only if its net AI value is positive and the confidence interval's lower bound is above 50% with at least 30 settled vetoes."
      >
        <Table
          minWidth={980}
          head={["Book", "Advisor", "Vetoes", "Settled", "Correct", "Precision", "95% CI", "Loss avoided", "Gain forgone"]}
          align={["l", "l", "r", "r", "r", "r", "r", "r", "r"]}
          rows={(books.data?.books ?? [])
            .filter((b) => b.advisor !== "none")
            .map((b) => {
              const v = b.veto_precision;
              return [
                <span key="b" className="font-medium">{b.book_id}</span>,
                <span key="a" className="text-fg-1">{b.advisor}</span>,
                num(v.vetoes, 0),
                num(v.settled, 0),
                num(v.correct, 0),
                v.precision == null ? "—" : pct(v.precision * 100),
                v.ci95 ? `${pct(v.ci95[0] * 100)} – ${pct(v.ci95[1] * 100)}` : "—",
                <Money key="la" value={v.loss_avoided_inr} />,
                <Money key="gf" value={v.gain_forgone_inr} />,
              ];
            })}
        />
      </Panel>

      <Panel title="Net AI value vs book A" description="After AI spend, by session, from the daily reports">
        {netLines.some((l) => l.points.length) ? (
          <Chart label="Net AI value of each advised book against the baseline, rupees, by session" lines={netLines} height={280} axis="inr" legend />
        ) : (
          <Empty>
            Daily reports carry this series (written at 15:45 IST). Now:{" "}
            {(books.data?.books ?? []).filter((b) => b.vs).map((b) => (
              <span key={b.book_id} className="mr-4">
                {b.book_id} <Money value={b.net_ai_value_inr} pnl />
              </span>
            ))}
          </Empty>
        )}
      </Panel>

      <Panel title="Calls" description="The most recent 500 model calls, newest first" className="min-h-[420px]" bodyClassName="overflow-x-auto">
        <DataTable
          label="LLM calls"
          data={calls.data}
          columns={CALL_COLUMNS}
          loading={calls.isLoading}
          error={calls.error}
          getRowId={(r) => `${r.seq}`}
          empty="No LLM calls recorded."
          emptyHint="Either no role is configured, or none was needed today."
        />
      </Panel>
    </Page>
  );
}
