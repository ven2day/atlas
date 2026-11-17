// The status footer: loop lag, the freshest quote, events, the stream connection, build, env,
// and the operator preferences (single-key shortcuts, table density). It wraps rather than
// hides, so connection health is visible on a phone too.

import { useSummary, useSystem } from "../api/queries";
import { cx } from "../lib/cx";
import { duration, num } from "../lib/format";
import { useSettings } from "../lib/settings";
import { useStream } from "../lib/stream";
import { Segmented } from "../ui/Segmented";

import type { ReactNode } from "react";

const WS_TEXT = {
  open: "live",
  connecting: "connecting",
  reconnecting: "reconnecting",
  unauthorized: "unauthorised",
} as const;

function Item({ label, children }: { label: string; children: ReactNode }) {
  return (
    <span className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
      <span className="text-fg-2">{label}</span>
      {children}
    </span>
  );
}

export function StatusBar() {
  const { data: system } = useSystem();
  const { data: summary } = useSummary();
  const status = useStream((s) => s.status);
  const { shortcuts, toggleShortcuts, density, setDensity } = useSettings();
  const ages = Object.values(system?.quote_age_s ?? {});
  const freshest = ages.length ? Math.min(...ages) : null;
  const lag = system?.loop_lag_ms;
  const connected = status === "open";

  return (
    <footer className="z-20 flex shrink-0 flex-wrap items-center gap-x-4 gap-y-1 border-t border-line bg-bg-1 px-4 py-1.5 text-2xs sm:px-6">
      <Item label="stream">
        <span className={cx("flex items-center gap-1.5", connected ? "text-accent-fg" : "text-warn")}>
          <span aria-hidden="true" className={cx("h-1.5 w-1.5 rounded-full", connected ? "bg-accent" : "bg-warn")} />
          {WS_TEXT[status]}
        </span>
      </Item>
      <Item label="loop lag">
        <span className={cx("num", lag !== null && lag !== undefined && lag > 500 ? "text-warn" : "text-fg-1")}>
          {lag === null || lag === undefined ? "—" : `${num(lag, 0)} ms`}
        </span>
      </Item>
      <Item label="last quote">
        <span className="num text-fg-1">{freshest === null ? "—" : `${duration(freshest)} old`}</span>
      </Item>
      <Item label="events">
        <span className="num text-fg-1">{num(summary?.last_seq ?? null, 0)}</span>
      </Item>
      <Item label="build">
        <span className="font-mono text-fg-1">{system?.versions.build ?? system?.versions.app ?? "—"}</span>
      </Item>
      <Item label="env">
        <span className="text-fg-1">{system?.environment ?? "—"}</span>
      </Item>

      <div className="ml-auto flex items-center gap-3">
        <Segmented
          label="Table density"
          size="xs"
          value={density}
          onChange={setDensity}
          options={[
            { value: "comfortable", label: "Roomy", title: "40 px table rows" },
            { value: "compact", label: "Compact", title: "32 px table rows" },
          ]}
        />
        <button
          type="button"
          onClick={toggleShortcuts}
          aria-pressed={shortcuts}
          className="rounded-sm text-fg-2 transition-colors duration-150 hover:text-fg-0"
          title="Single-key shortcuts (1-8 switch screens). Ctrl/Cmd+K always opens the palette."
        >
          keys: <span className={cx(shortcuts ? "text-accent-fg" : "text-fg-1")}>{shortcuts ? "on" : "off"}</span>
        </button>
        <span className="hidden sm:inline">
          <kbd className="num rounded-sm border border-line-strong px-1 py-0.5 font-mono text-fg-2">Ctrl K</kbd>{" "}
          <span className="text-fg-2">palette</span>
        </span>
      </div>
    </footer>
  );
}
