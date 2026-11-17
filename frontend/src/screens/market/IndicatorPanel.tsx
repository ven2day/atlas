// The indicator picker: search, grouped by family, with presets, per-instance settings and a
// running list of what is on. Everything it offers is computed from the symbol's real daily
// OHLCV in the browser - no series is fetched from anywhere else, and none is invented.

import { useMemo, useState } from "react";

import { addBlocked, MAX_ACTIVE, MAX_PANES, paneCount, useIndicators } from "../../lib/indicatorStore";
import {
  BY_ID,
  CATEGORY_LABEL,
  INDICATORS,
  PRESETS,
  clampParams,
  instanceLabel,
  type Category,
  type IndicatorDef,
} from "../../lib/indicators";
import { Icon } from "../../shell/icons";
import { Badge } from "../../ui/Badge";
import { Button, LinkButton } from "../../ui/Button";
import { Dialog } from "../../ui/Dialog";
import { TextInput } from "../../ui/Input";
import { EmptyState } from "../../ui/Panel";
import { instanceColor } from "./series";

const CATEGORY_ORDER: Category[] = ["trend", "momentum", "volatility", "volume"];

const PLACEMENT_LABEL = {
  overlay: "on price",
  pane: "own pane",
  volume: "on volume",
} as const;

function ParamControl({
  def,
  paramKey,
  value,
  onChange,
}: {
  def: IndicatorDef;
  paramKey: string;
  value: number;
  onChange: (value: number) => void;
}) {
  const spec = def.params.find((p) => p.key === paramKey)!;
  const decimal = spec.step !== undefined && spec.step < 1;
  return (
    <label className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="rq-label truncate">{spec.label}</span>
      <span className="flex items-center gap-2">
        <input
          type="range"
          min={spec.min}
          max={spec.max}
          step={spec.step ?? 1}
          value={value}
          aria-label={`${def.abbr} ${spec.label}`}
          onChange={(e) => onChange(Number(e.target.value))}
          className="h-1.5 min-w-0 flex-1 cursor-pointer appearance-none rounded-full bg-bg-3"
        />
        <input
          type="number"
          min={spec.min}
          max={spec.max}
          step={spec.step ?? 1}
          value={decimal ? value : Math.round(value)}
          aria-label={`${def.abbr} ${spec.label}, exact`}
          onChange={(e) => {
            const next = Number(e.target.value);
            if (Number.isFinite(next)) onChange(Math.min(spec.max, Math.max(spec.min, next)));
          }}
          className="num h-7 w-16 shrink-0 rounded-md border border-line-strong bg-bg-0 px-2 text-xs text-fg-0 focus:border-accent focus:outline-none"
        />
      </span>
    </label>
  );
}

function ActiveList() {
  const active = useIndicators((s) => s.active);
  const remove = useIndicators((s) => s.remove);
  const setParams = useIndicators((s) => s.setParams);
  const clear = useIndicators((s) => s.clear);
  const [expanded, setExpanded] = useState<string | null>(null);

  if (!active.length) {
    return (
      <EmptyState
        title="No indicators on"
        hint="Pick one from the list, or start from a preset. Indicators are drawn over the real daily bars of the selected symbol."
        className="py-8"
      />
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-xs font-medium uppercase tracking-wide text-fg-2">
          Active · {active.length}/{MAX_ACTIVE}
        </h3>
        <LinkButton tone="danger" onClick={clear}>
          Clear all
        </LinkButton>
      </div>
      <ul className="flex flex-col gap-2">
        {active.map((item, index) => {
          const def = BY_ID.get(item.id);
          if (!def) return null;
          const open = expanded === item.key;
          return (
            <li key={item.key} className="rounded-md border border-line bg-bg-2 p-2.5">
              <div className="flex items-center gap-2">
                <span
                  aria-hidden="true"
                  className="h-2.5 w-2.5 shrink-0 rounded-full"
                  style={{ background: instanceColor(index) }}
                />
                <button
                  type="button"
                  onClick={() => setExpanded(open ? null : item.key)}
                  aria-expanded={open}
                  className="min-w-0 flex-1 rounded-sm text-left text-sm font-medium text-fg-0 hover:text-accent-fg"
                >
                  {instanceLabel(def, item.params)}
                </button>
                {def.params.length > 0 && (
                  <LinkButton onClick={() => setExpanded(open ? null : item.key)} aria-label={`Settings for ${instanceLabel(def, item.params)}`}>
                    <Icon name="sliders" size={15} />
                  </LinkButton>
                )}
                <LinkButton tone="danger" onClick={() => remove(item.key)} aria-label={`Remove ${instanceLabel(def, item.params)}`}>
                  <Icon name="close" size={15} />
                </LinkButton>
              </div>
              {open && def.params.length > 0 && (
                <div className="mt-3 flex flex-col gap-3 border-t border-line pt-3">
                  {def.params.map((p) => (
                    <ParamControl
                      key={p.key}
                      def={def}
                      paramKey={p.key}
                      value={clampParams(def, item.params)[p.key] ?? p.default}
                      onChange={(value) => setParams(item.key, { [p.key]: value })}
                    />
                  ))}
                </div>
              )}
            </li>
          );
        })}
      </ul>
      <p className="text-2xs leading-relaxed text-fg-2">
        Overlays draw on the price chart; oscillators get their own pane below it (at most {MAX_PANES}, so the price
        keeps the space it needs).
      </p>
    </div>
  );
}

function IndicatorRow({ def }: { def: IndicatorDef }) {
  const active = useIndicators((s) => s.active);
  const add = useIndicators((s) => s.add);
  const count = active.filter((a) => a.id === def.id).length;
  const blocked = addBlocked(active, def);

  return (
    <li className="flex items-start gap-3 rounded-md px-2.5 py-2 transition-colors duration-150 hover:bg-bg-2">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-2xs text-accent-fg">{def.abbr}</span>
          <span className="text-sm font-medium text-fg-0">{def.name}</span>
          <span className="rounded-full border border-line bg-bg-2 px-1.5 py-px text-[10px] uppercase tracking-wide text-fg-2">
            {PLACEMENT_LABEL[def.placement]}
          </span>
          {count > 0 && <Badge tone="accent" size="sm">{count} on</Badge>}
        </div>
        <p className="mt-0.5 text-xs leading-relaxed text-fg-2">{def.description}</p>
      </div>
      <Button
        size="xs"
        variant={count > 0 ? "soft" : "secondary"}
        disabled={blocked !== null}
        title={blocked ?? `Add ${def.name}`}
        onClick={() => add(def.id)}
        aria-label={blocked ?? `Add ${def.name}`}
      >
        <Icon name="plus" size={13} strokeWidth={2} />
        Add
      </Button>
    </li>
  );
}

export function IndicatorPanel({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [query, setQuery] = useState("");
  const replace = useIndicators((s) => s.replace);
  const panes = useIndicators((s) => paneCount(s.active));
  const text = query.trim().toLowerCase();

  const grouped = useMemo(() => {
    const matches = INDICATORS.filter(
      (d) =>
        !text ||
        d.name.toLowerCase().includes(text) ||
        d.abbr.toLowerCase().includes(text) ||
        d.description.toLowerCase().includes(text) ||
        d.category.includes(text),
    );
    return CATEGORY_ORDER.map((category) => ({
      category,
      items: matches.filter((d) => d.category === category),
    })).filter((g) => g.items.length > 0);
  }, [text]);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      wide
      title="Indicators"
      description="Computed in the browser from this symbol's real daily bars. A series with no history yet draws nothing rather than guessing."
      footer={
        <>
          <span className="mr-auto text-2xs text-fg-2">
            {INDICATORS.length} indicators · {panes}/{MAX_PANES} panes in use
          </span>
          <Button size="md" variant="primary" onClick={() => onOpenChange(false)}>
            Done
          </Button>
        </>
      }
    >
      <div className="grid gap-5 md:grid-cols-[minmax(0,1fr)_300px]">
        <div className="flex min-w-0 flex-col gap-4">
          <div className="relative">
            <Icon name="search" size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-fg-2" />
            <TextInput
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search indicators, e.g. rsi, bollinger, volume"
              className="pl-9"
              aria-label="Search indicators"
            />
          </div>

          <div className="flex flex-col gap-2">
            <span className="rq-label">Presets</span>
            <div className="flex flex-wrap gap-2">
              {PRESETS.map((preset) => (
                <button
                  key={preset.id}
                  type="button"
                  title={preset.description}
                  onClick={() => replace(preset.items)}
                  className="rounded-full border border-line-strong bg-bg-2 px-3 py-1.5 text-xs font-medium text-fg-1 transition-colors duration-150 hover:border-accent hover:text-accent-fg"
                >
                  {preset.name}
                </button>
              ))}
            </div>
          </div>

          {grouped.length === 0 ? (
            <EmptyState title="No indicator matches" hint={`Nothing in the catalogue matches “${query}”. Try RSI, MACD, ATR or volume.`} />
          ) : (
            <div className="flex flex-col gap-4">
              {grouped.map((group) => (
                <section key={group.category}>
                  <h3 className="mb-1 flex items-center gap-2 px-2.5 text-xs font-semibold uppercase tracking-wide text-fg-2">
                    {CATEGORY_LABEL[group.category]}
                    <span className="num text-2xs font-normal normal-case">{group.items.length}</span>
                  </h3>
                  <ul className="flex flex-col">
                    {group.items.map((def) => (
                      <IndicatorRow key={def.id} def={def} />
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </div>

        <aside className="min-w-0 border-t border-line pt-4 md:border-l md:border-t-0 md:pl-5 md:pt-0">
          <ActiveList />
        </aside>
      </div>
    </Dialog>
  );
}
