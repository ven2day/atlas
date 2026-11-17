// 6. Experiment: the paired books side by side, their equity against NIFTY, the daily reports and
// the pre-registration they are judged against.

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { get } from "../api/client";
import { keys } from "../api/queries";
import { num, pct } from "../lib/format";
import { Icon } from "../shell/icons";
import { Button } from "../ui/Button";
import { Chart, type ChartLine } from "../ui/Chart";
import { Dialog } from "../ui/Dialog";
import { DateIST, Money, Pct } from "../ui/Num";
import { Page, PageHeader } from "../ui/PageHeader";
import { Empty, EmptyState, Panel } from "../ui/Panel";
import { Table } from "../ui/Table";

interface ReportBook {
  risk_adjusted_mtd?: { sharpe?: number | null; sharpe_ci95?: [number, number] | null; sortino?: number | null; max_drawdown_pct?: number | null };
  trades?: { month_to_date?: { count?: number; win_rate?: number; expectancy_inr?: number | null } };
  costs?: { month_to_date?: { total?: number | null } };
}

function Markdown({ text }: { text: string }) {
  return (
    <pre className="whitespace-pre-wrap rounded-lg border border-line bg-bg-2 p-4 font-mono text-xs leading-relaxed text-fg-0">
      {text}
    </pre>
  );
}

export default function Experiment() {
  const books = useQuery({ queryKey: keys.books, queryFn: ({ signal }) => get("/api/books", { signal }), refetchInterval: 30_000 });
  const equity = useQuery({ queryKey: ["equity"], queryFn: ({ signal }) => get("/api/equity", { signal }) });
  const reports = useQuery({ queryKey: ["reports"], queryFn: ({ signal }) => get("/api/reports", { query: { limit: 90 }, signal }) });
  const latest = reports.data?.[0]?.date;
  const report = useQuery({
    queryKey: keys.report(latest ?? ""),
    queryFn: ({ signal }) => get("/api/reports/{day}", { params: { day: latest! }, signal }),
    enabled: !!latest,
  });
  const [open, setOpen] = useState<string | null>(null); // a report date, or "prereg"
  const doc = useQuery({
    queryKey: ["doc", open],
    queryFn: ({ signal }) =>
      open === "prereg"
        ? get("/api/docs/preregistration", { signal })
        : get("/api/reports/{day}/markdown", { params: { day: open! }, signal }),
    enabled: open !== null,
  });

  const reportBooks = (report.data?.books ?? {}) as Record<string, ReportBook>;
  const lines = useMemo(() => {
    const tones = { A: "neutral", B: "accent", C: "warn" } as const;
    const out: ChartLine[] = (equity.data?.books ?? []).map((b) => ({
      name: `Book ${b.book_id}`,
      tone: tones[b.book_id as keyof typeof tones] ?? "neutral",
      points: b.points.map((p) => ({ time: p.date, value: p.return_pct })),
    }));
    if (equity.data?.benchmark_points.length) {
      out.push({ name: "NIFTY 50", tone: "neutral", dashed: true, points: equity.data.benchmark_points.map((p) => ({ time: p.date, value: p.return_pct })) });
    }
    return out;
  }, [equity.data]);

  return (
    <Page>
      <PageHeader
        title="Experiment"
        description="Three books trade the same signals; only the advisor differs. This is the comparison the whole platform exists to make, against the rules it was pre-registered with."
        actions={
          <Button size="md" variant="secondary" onClick={() => setOpen("prereg")}>
            <Icon name="book" size={15} />
            Pre-registration
          </Button>
        }
      />

      <Panel
        title={`Paired books · ${books.data?.experiment ?? ""}`}
        description={latest ? `Return and net value are live; the risk statistics come from the ${latest} report` : "Return and net value are live; no daily report yet"}
        footer="Net AI value = (book equity − book A equity) − that book's AI spend. Sharpe, Sortino, drawdown, expectancy and costs are month-to-date from the latest daily report."
        bodyClassName="overflow-x-auto"
      >
        {(books.data?.books ?? []).length === 0 ? (
          <Empty>{books.isLoading ? "Loading…" : "No books configured."}</Empty>
        ) : (
          <Table
            minWidth={1180}
            head={["Book", "Advisor", "Return", "Sharpe (95% CI)", "Sortino", "Max DD", "Trades", "Win %", "Expectancy", "Costs MTD", "AI spend", "Net AI value"]}
            align={["l", "l", "r", "r", "r", "r", "r", "r", "r", "r", "r", "r"]}
            rows={(books.data?.books ?? []).map((b) => {
              const r = reportBooks[b.book_id];
              const ra = r?.risk_adjusted_mtd;
              const mtd = r?.trades?.month_to_date;
              return [
                <span key="b" className="font-semibold">{b.book_id}</span>,
                <span key="a" className="text-fg-1">{b.advisor}</span>,
                <Pct key="r" value={b.return_pct} pnl arrow />,
                <span key="s">
                  {ra?.sharpe == null ? "—" : num(ra.sharpe, 2)}
                  {ra?.sharpe_ci95 && <span className="text-fg-2"> ({num(ra.sharpe_ci95[0], 2)}, {num(ra.sharpe_ci95[1], 2)})</span>}
                </span>,
                ra?.sortino == null ? "—" : num(ra.sortino, 2),
                ra?.max_drawdown_pct == null ? "—" : pct(ra.max_drawdown_pct),
                num(b.closed_trades, 0),
                b.win_rate == null ? "—" : pct(b.win_rate * 100),
                <Money key="e" value={mtd?.expectancy_inr} pnl />,
                <Money key="c" value={r?.costs?.month_to_date?.total} />,
                <Money key="ai" value={b.ai_spend_inr_to_date} />,
                b.vs ? <Money key="n" value={b.net_ai_value_inr} pnl arrow /> : <span key="n" className="text-fg-2">baseline</span>,
              ];
            })}
          />
        )}
      </Panel>

      <Panel title="Return on capital" description="Each book's cumulative return in percent; NIFTY 50 dashed">
        {lines.some((l) => l.points.length) ? (
          <Chart label="Each book's return on its capital and NIFTY 50's, percent, by session" lines={lines} height={320} axis="pct" legend />
        ) : (
          <EmptyState title="No marks yet" hint="The first mark is recorded when a session ends." />
        )}
      </Panel>

      <Panel title="Daily reports" description="Written at 15:45 IST each session; open one for the full markdown">
        {!reports.data?.length ? (
          <EmptyState title="No daily report yet" hint="Reports are written at 15:45 IST on each session day." />
        ) : (
          <Table
            minWidth={720}
            head={["Session", ...Object.keys(reports.data[0]!.books).map((b) => `${b} day / net AI`), ""]}
            align={["l", ...Object.keys(reports.data[0]!.books).map(() => "r" as const), "r"]}
            rows={reports.data.map((r) => [
              <DateIST key="d" value={r.date} />,
              ...Object.entries(r.books).map(([b, x]) => (
                <span key={b}>
                  <Pct value={x.day_return_pct} pnl />
                  {x.net_ai_value_inr != null && <> / <Money value={x.net_ai_value_inr} pnl /></>}
                </span>
              )),
              <Button key="o" size="xs" variant="ghost" onClick={() => setOpen(r.date)}>
                Open
                <Icon name="chevronRight" size={12} />
              </Button>,
            ])}
          />
        )}
      </Panel>

      <Dialog open={open !== null} onOpenChange={(o) => !o && setOpen(null)} title={doc.data?.title ?? "Loading…"} wide>
        {doc.data ? <Markdown text={doc.data.markdown} /> : <Empty>{doc.error ? "Could not load the document." : "Loading…"}</Empty>}
      </Dialog>
    </Page>
  );
}
