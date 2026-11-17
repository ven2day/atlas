import { useSummary } from "../api/queries";
import { dateIST } from "../lib/format";
import { useStream } from "../lib/stream";
import { Icon } from "./icons";

/** A persistent banner whenever the console shows the demo: nothing here is a real market. */
export function DemoBanner() {
  const { data: summary } = useSummary();
  if (!summary?.demo) return null;
  return (
    <div
      role="note"
      className="flex shrink-0 items-start gap-2.5 border-b border-warn bg-warn-soft px-4 py-2 text-xs text-warn sm:px-6"
    >
      <Icon name="alert" size={15} className="mt-px shrink-0" strokeWidth={1.8} />
      <p className="leading-relaxed">
        <span className="font-semibold uppercase tracking-wide">DEMO</span> — the bundled fixture
        tape ({dateIST(summary.session?.date)}, synthetic prices) replayed through the real engine.
        Nothing here is a real market or a real order.
      </p>
    </div>
  );
}

/** A wrong or expired token: say what to do, nothing else (no data is shown). */
export function Unauthorized() {
  return (
    <div className="flex h-full items-center justify-center bg-bg-0 p-6">
      <div className="rq-card w-full max-w-md p-6">
        <span className="flex h-10 w-10 items-center justify-center rounded-md border border-accent-line bg-accent-soft text-accent-fg">
          <Icon name="market" size={20} />
        </span>
        <h1 className="mt-4 text-xl font-semibold tracking-tight text-fg-0">Not authorised</h1>
        <p className="mt-2 text-sm leading-relaxed text-fg-1">
          This console needs the access token of the running server. Open the link the server
          printed when it started (it ends in <code className="rounded-sm bg-bg-2 px-1.5 py-0.5 font-mono text-xs text-fg-0">#token=…</code>).
          A restart issues a new token.
        </p>
      </div>
    </div>
  );
}

export function useUnauthorized(): boolean {
  return useStream((s) => s.status === "unauthorized");
}
