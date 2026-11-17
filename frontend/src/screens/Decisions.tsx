// Decisions: what every book did with every signal, filterable; each row opens its lineage.

import { useQuery } from "@tanstack/react-query";
import { createColumnHelper } from "@tanstack/react-table";
import { useState } from "react";
import { useNavigate } from "react-router";

import { get, type Schemas } from "../api/client";
import { keys } from "../api/queries";
import { Icon } from "../shell/icons";
import { Badge } from "../ui/Badge";
import { Button } from "../ui/Button";
import { DataTable, downloadCsv, type Columns } from "../ui/DataTable";
import { TimeIST } from "../ui/Num";
import { Page, PageHeader, Toolbar } from "../ui/PageHeader";
import { Panel } from "../ui/Panel";
import { DateFilter, DecisionLink, DISPOSITION, Select, SymbolLink, TextFilter, useBookIds, useSessionDay } from "./common";

type Row = Schemas["DecisionRow"];

const col = createColumnHelper<Row>();
export const DECISION_COLUMNS: Columns<Row> = [
  col.accessor("ts", { header: "Time (IST)", size: 104, cell: (c) => <TimeIST value={c.getValue()} /> }),
  col.accessor("book_id", { header: "Book", size: 68 }),
  col.accessor("symbol", { header: "Symbol", size: 112, cell: (c) => <SymbolLink symbol={c.getValue()} /> }),
  col.accessor("strategy", { header: "Strategy", size: 144, cell: (c) => <span className="text-fg-1">{c.getValue()}</span> }),
  col.accessor("disposition", {
    header: "Outcome",
    size: 124,
    cell: (c) => <Badge tone={c.getValue() === "submitted" ? "neutral" : undefined}>{DISPOSITION[c.getValue()] ?? c.getValue()}</Badge>,
  }),
  col.accessor("detail", { header: "Detail", size: 420, cell: (c) => <span className="text-fg-1">{c.getValue() || "—"}</span> }),
  col.accessor("decision_id", { header: "Decision", size: 132, cell: (c) => <DecisionLink id={c.getValue()} /> }),
];

const OUTCOMES = ["", "submitted", "vetoed", "risk_rejected", "broker_rejected", "regime_gated", "policy_skipped", "shadow_strategy", "unknown"];

export default function Decisions() {
  const day = useSessionDay();
  const books = useBookIds();
  const navigate = useNavigate();
  const [book, setBook] = useState("");
  const [symbol, setSymbol] = useState("");
  const [strategy, setStrategy] = useState("");
  const [outcome, setOutcome] = useState("");
  const [date, setDate] = useState<string | null>(null);
  const query = {
    book: book || undefined,
    symbol: symbol.trim().toUpperCase() || undefined,
    strategy: strategy.trim() || undefined,
    outcome: outcome || undefined,
    date: date ?? day,
    limit: 1000,
  };
  const rows = useQuery({ queryKey: keys.decisions(query), queryFn: ({ signal }) => get("/api/decisions", { query, signal }) });
  const count = rows.data?.length ?? 0;

  return (
    <Page>
      <PageHeader
        title="Decisions"
        description="Every signal the engine produced, and what each book did with it: submitted, vetoed by an advisor, or blocked by a risk check. Open a row for the whole lineage."
        actions={
          <Button
            size="md"
            variant="secondary"
            disabled={count === 0}
            onClick={() => downloadCsv(`decisions-${query.date}`, rows.data ?? [], DECISION_COLUMNS)}
          >
            <Icon name="download" size={15} />
            Export CSV
          </Button>
        }
      />

      <Toolbar>
        <Select label="Book" value={book} onChange={setBook} options={[{ value: "", label: "All books" }, ...books.map((b) => ({ value: b, label: `Book ${b}` }))]} />
        <TextFilter label="Symbol" value={symbol} onChange={setSymbol} placeholder="e.g. INFY" width="w-32" />
        <TextFilter label="Strategy" value={strategy} onChange={setStrategy} placeholder="e.g. momentum" width="w-40" />
        <Select
          label="Outcome"
          value={outcome}
          onChange={setOutcome}
          options={OUTCOMES.map((o) => ({ value: o, label: o ? (DISPOSITION[o] ?? o) : "All outcomes" }))}
        />
        <DateFilter value={date ?? day} onChange={setDate} />
      </Toolbar>

      <Panel
        title={`Decisions · ${count}`}
        description={`Session ${date ?? day}, sorted by time; click any row to open its inspector`}
        bodyClassName="overflow-hidden"
        className="min-h-[420px]"
      >
        <DataTable
          className="h-[60vh] min-h-[320px]"
          label="Decisions"
          data={rows.data}
          columns={DECISION_COLUMNS}
          loading={rows.isLoading}
          error={rows.error}
          getRowId={(r) => `${r.seq}`}
          onRowClick={(r) => navigate(`/decisions/${r.decision_id}`)}
          rowLabel={(r) => `${r.book_id} ${r.symbol} ${r.strategy}: open the decision`}
          empty="No decisions match these filters."
          emptyHint="Decisions run once a session, inside the entry window. Try another date, or clear the filters."
        />
      </Panel>
    </Page>
  );
}
