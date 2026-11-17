// lightweight-charts behind a small declarative API: candles, a close line or a filled area for
// the price pane, optional volume, overlay and oscillator series (each oscillator pane gets its
// own scale and reference levels), and entry/exit/event markers. Lazy (./Chart).

import {
  AreaSeries,
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  LineSeries,
  LineStyle,
  type ISeriesApi,
  type MouseEventParams,
  type SeriesMarker,
  type SeriesType,
  type Time,
} from "lightweight-charts";
import { useEffect, useMemo, useRef, useState } from "react";

import { inrCompact, num, pct, price, qty } from "../lib/format";

export interface Candle {
  time: string; // YYYY-MM-DD
  open: number;
  high: number;
  low: number;
  close: number;
  volume?: number;
}

export type AxisKind = "inr" | "pct" | "price" | "volume" | "number";

export interface ChartLine {
  name: string;
  points: { time: string; value: number }[];
  /** An explicit colour from the categorical palette; otherwise `tone` picks a semantic one. */
  color?: string;
  tone?: "accent" | "neutral" | "warn" | "up" | "down";
  dashed?: boolean; // a benchmark: same palette, different stroke
  kind?: "line" | "histogram";
  /** 0 (or absent) draws on the price pane; 1, 2, … get their own pane below it. */
  pane?: number;
  lineWidth?: 1 | 2 | 3 | 4;
  /** Draw on another scale of the same pane, e.g. "volume" for a volume moving average. */
  priceScaleId?: string;
}

export interface ChartMarker {
  time: string;
  kind: "entry" | "exit" | "event";
  text: string;
}

/** Per-oscillator-pane options: `panes[0]` configures chart pane 1, and so on. */
export interface PaneSpec {
  levels?: number[];
  axis?: AxisKind;
  /** Relative height against the price pane. */
  stretch?: number;
}

export interface ChartProps {
  /** How the price axis reads. */
  axis?: AxisKind;
  /** The base price series: candles, a close line, a filled area, or nothing (indicators only). */
  kind?: "candles" | "line" | "area" | "none";
  candles?: Candle[];
  lines?: ChartLine[];
  markers?: ChartMarker[];
  panes?: PaneSpec[];
  /** Draw each candle's volume as a histogram at the foot of the price pane. */
  volume?: boolean;
  /** A live read-out of every series under the crosshair. */
  legend?: boolean;
  height?: number;
  label: string; // the chart's accessible name, e.g. "INFY daily candles, ₹"
}

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || "grey";
}

/** Callers hand over `var(--chart-3)`, so no literal colour ever lives in a component. */
function resolveColor(value: string): string {
  const match = /^var\((--[\w-]+)\)$/.exec(value.trim());
  return match ? cssVar(match[1]!) : value;
}

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Session dates arrive as business days; label them like the rest of the UI (05 Oct). */
function dayLabel(time: Time): string {
  if (typeof time === "object") return `${String(time.day).padStart(2, "0")} ${MONTHS[time.month - 1]}`;
  if (typeof time === "string") return dayLabel({ year: +time.slice(0, 4), month: +time.slice(5, 7), day: +time.slice(8, 10) });
  return new Date(time * 1000).toISOString().slice(0, 10);
}

function formatterFor(axis: AxisKind): (v: number) => string {
  switch (axis) {
    case "pct":
      return (v) => pct(v);
    case "inr":
      return (v) => inrCompact(v);
    case "volume":
      return (v) => qty(v);
    case "number":
      return (v) => num(v, Math.abs(v) < 10 ? 2 : 0);
    default:
      return (v) => price(v);
  }
}

const PRICE_FORMAT = { type: "price", precision: 2, minMove: 0.01 } as const;
const VOLUME_FORMAT = { type: "volume" } as const;

type AnySeries = ISeriesApi<SeriesType>;
// The chart hands back series typed to their own kind; the legend only needs them as map keys.
const anySeries = (series: unknown): AnySeries => series as AnySeries;

interface LegendEntry {
  name: string;
  color: string;
  value: string | null;
}

interface Meta {
  name: string;
  color: string;
  fmt: (v: number) => string;
  last: string | null;
}

/** The value of a data item: a line/histogram carries `value`, a candle carries `close`. */
function valueOf(raw: unknown): number {
  if (typeof raw === "number") return raw;
  const record = (raw ?? {}) as { value?: number; close?: number };
  return record.value ?? record.close ?? Number.NaN;
}

export default function ChartImpl({
  candles,
  lines = [],
  markers = [],
  panes = [],
  height = 320,
  label,
  axis = "price",
  kind = "candles",
  volume = false,
  legend = false,
}: ChartProps) {
  const host = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<LegendEntry[] | null>(null);
  // `panes` is usually a fresh array literal, so depend on its shape, not its identity.
  const panesKey = JSON.stringify(panes);

  // The legend at rest: each series' last value, worked out from the props so it needs no state
  // and no second render once the chart exists.
  const resting = useMemo<LegendEntry[]>(() => {
    if (!legend) return [];
    const paneSpecs: PaneSpec[] = panesKey ? (JSON.parse(panesKey) as PaneSpec[]) : [];
    const out: LegendEntry[] = [];
    const last = (points: { value: number }[], fmt: (v: number) => string) => {
      for (let i = points.length - 1; i >= 0; i--) {
        const v = points[i]?.value;
        if (v !== undefined && Number.isFinite(v)) return fmt(v);
      }
      return null;
    };
    const data = candles ?? [];
    if (data.length && kind !== "none") {
      const close = data[data.length - 1]!.close;
      out.push({
        name: kind === "candles" ? "OHLC" : "Close",
        color: kind === "candles" ? "var(--up)" : "var(--accent)",
        value: formatterFor(axis)(close),
      });
    }
    if (volume && data.some((c) => c.volume !== undefined)) {
      out.push({ name: "Volume", color: "var(--text-2)", value: formatterFor("volume")(data[data.length - 1]!.volume ?? 0) });
    }
    for (const line of lines) {
      const pane = line.pane ?? 0;
      const fmt = line.priceScaleId === "volume" ? formatterFor("volume") : pane === 0 ? formatterFor(axis) : formatterFor(paneSpecs[pane - 1]?.axis ?? "number");
      out.push({ name: line.name, color: line.color ?? "var(--accent)", value: last(line.points, fmt) });
    }
    return out;
  }, [legend, panesKey, candles, kind, volume, lines, axis]);

  useEffect(() => {
    const paneSpecs: PaneSpec[] = panesKey ? (JSON.parse(panesKey) as PaneSpec[]) : [];
    if (!host.current) return;
    const text = cssVar("--text-2");
    const grid = cssVar("--border");
    const border = cssVar("--border-strong");
    const api = createChart(host.current, {
      height,
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: text,
        fontFamily: cssVar("--font-ui"),
        fontSize: 11,
        attributionLogo: false,
      },
      grid: { vertLines: { color: grid }, horzLines: { color: grid } },
      rightPriceScale: { borderColor: border },
      timeScale: { borderColor: border, tickMarkFormatter: (t: Time) => dayLabel(t), rightOffset: 2, barSpacing: 8 },
      crosshair: {
        mode: 0,
        vertLine: { color: border, labelBackgroundColor: cssVar("--bg-3") },
        horzLine: { color: border, labelBackgroundColor: cssVar("--bg-3") },
      },
      localization: { locale: "en-IN", priceFormatter: formatterFor(axis), timeFormatter: (t: Time) => dayLabel(t) },
    });

    const palette = {
      accent: cssVar("--accent"),
      neutral: cssVar("--text-1"),
      warn: cssVar("--warn"),
      up: cssVar("--up"),
      down: cssVar("--down"),
    };

    const meta = new Map<AnySeries, Meta>();
    const track = (series: unknown, entry: Meta) => meta.set(anySeries(series), entry);
    const fmtFor = (pane: number) => (pane === 0 ? formatterFor(axis) : formatterFor(paneSpecs[pane - 1]?.axis ?? "number"));
    const lastOf = (points: { value: number }[], fmt: (v: number) => string) => {
      for (let i = points.length - 1; i >= 0; i--) {
        const v = points[i]?.value;
        if (v !== undefined && v !== null && Number.isFinite(v)) return fmt(v);
      }
      return null;
    };

    // -- the price pane ---------------------------------------------------------------------
    let priceSeries: AnySeries | null = null;
    const data = candles ?? [];
    if (data.length && kind !== "none") {
      if (kind === "candles") {
        const up = cssVar("--up");
        const down = cssVar("--down");
        const series = api.addSeries(CandlestickSeries, {
          upColor: up,
          downColor: down,
          borderUpColor: up,
          borderDownColor: down,
          wickUpColor: up,
          wickDownColor: down,
          priceFormat: axis === "volume" ? VOLUME_FORMAT : PRICE_FORMAT,
        });
        series.setData(data.map((c) => ({ ...c, time: c.time as Time })));
        priceSeries = anySeries(series);
        track(series, { name: "OHLC", color: up, fmt: fmtFor(0), last: lastOf(data.map((c) => ({ value: c.close })), fmtFor(0)) });
      } else {
        const colour = cssVar("--accent");
        const points = data.map((c) => ({ time: c.time as Time, value: c.close }));
        const series =
          kind === "area"
            ? api.addSeries(AreaSeries, {
                lineColor: colour,
                topColor: cssVar("--accent-soft"),
                bottomColor: cssVar("--accent-soft"),
                lineWidth: 2,
                priceFormat: PRICE_FORMAT,
              })
            : api.addSeries(LineSeries, { color: colour, lineWidth: 2, priceFormat: PRICE_FORMAT });
        series.setData(points);
        priceSeries = anySeries(series);
        track(series, { name: "Close", color: colour, fmt: fmtFor(0), last: lastOf(points, fmtFor(0)) });
      }
    }

    if (volume && data.some((c) => c.volume !== undefined)) {
      // Dimmed bars: the volume supports the price, it does not compete with it.
      const upDim = cssVar("--up-dim");
      const downDim = cssVar("--down-dim");
      const series = api.addSeries(HistogramSeries, {
        priceScaleId: "volume",
        priceFormat: VOLUME_FORMAT,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      api.priceScale("volume").applyOptions({ scaleMargins: { top: 0.78, bottom: 0 } });
      series.setData(data.map((c) => ({ time: c.time as Time, value: c.volume ?? 0, color: c.close >= c.open ? upDim : downDim })));
      track(series, { name: "Volume", color: cssVar("--text-2"), fmt: formatterFor("volume"), last: lastOf(data.map((c) => ({ value: c.volume ?? 0 })), formatterFor("volume")) });
    }

    if (priceSeries && markers.length) {
      const ordered = [...markers].sort((a, b) => a.time.localeCompare(b.time));
      createSeriesMarkers(
        priceSeries,
        ordered.map(
          (m): SeriesMarker<Time> => ({
            time: m.time as Time,
            position: m.kind === "exit" ? "aboveBar" : "belowBar",
            shape: m.kind === "entry" ? "arrowUp" : m.kind === "exit" ? "arrowDown" : "circle",
            color: m.kind === "event" ? cssVar("--warn") : m.kind === "entry" ? cssVar("--accent") : cssVar("--down"),
            text: m.text,
            size: 1,
          }),
        ),
      );
    }

    // -- overlays and oscillator panes -------------------------------------------------------
    const firstOfPane = new Map<number, AnySeries>();
    for (const line of lines) {
      const pane = line.pane ?? 0;
      const onVolumeScale = line.priceScaleId === "volume";
      const fmt = onVolumeScale ? formatterFor("volume") : fmtFor(pane);
      const format = onVolumeScale ? VOLUME_FORMAT : PRICE_FORMAT;
      const colour = resolveColor(line.color ?? palette[line.tone ?? "accent"]);
      const series =
        line.kind === "histogram"
          ? api.addSeries(
              HistogramSeries,
              {
                color: colour,
                priceFormat: format,
                priceScaleId: line.priceScaleId,
                lastValueVisible: false,
                priceLineVisible: false,
              },
              pane,
            )
          : api.addSeries(
              LineSeries,
              {
                color: colour,
                lineWidth: line.lineWidth ?? 2,
                lineStyle: line.dashed ? LineStyle.Dashed : LineStyle.Solid,
                priceFormat: format,
                priceScaleId: line.priceScaleId,
                lastValueVisible: false,
                crosshairMarkerVisible: false,
              },
              pane,
            );
      series.setData(line.points.map((p) => ({ time: p.time as Time, value: p.value })));
      if (!firstOfPane.has(pane)) firstOfPane.set(pane, anySeries(series));
      track(series, { name: line.name, color: colour, fmt, last: lastOf(line.points, fmt) });
    }

    // Reference levels per pane, and how much room each pane gets.
    const paneApis = api.panes();
    for (let i = 0; i < paneSpecs.length; i++) {
      const spec = paneSpecs[i]!;
      const anchor = firstOfPane.get(i + 1);
      for (const level of spec.levels ?? []) {
        anchor?.createPriceLine({
          price: level,
          color: cssVar("--border-strong"),
          lineWidth: 1,
          lineStyle: LineStyle.Dotted,
          axisLabelVisible: true,
          title: "",
        });
      }
      paneApis[i + 1]?.setStretchFactor(spec.stretch ?? 1);
    }
    paneApis[0]?.setStretchFactor(paneSpecs.length ? 3 : 1);
    api.timeScale().fitContent();

    const onMove = (param: MouseEventParams) => {
      if (param.time === undefined) {
        setHover(null); // back to the resting legend, which render already has
        return;
      }
      const out: LegendEntry[] = [];
      for (const [series, m] of meta) {
        const raw = param.seriesData.get(series);
        const value = raw === undefined ? Number.NaN : valueOf(raw);
        out.push({ name: m.name, color: m.color, value: Number.isFinite(value) ? m.fmt(value) : m.last });
      }
      setHover(out);
    };

    if (legend) api.subscribeCrosshairMove(onMove);

    return () => {
      api.remove();
    };
  }, [candles, lines, markers, panesKey, height, axis, kind, volume, legend]);

  const shown = hover ?? resting;

  return (
    <div className="relative w-full" style={{ height }}>
      <div ref={host} role="img" aria-label={label} className="h-full w-full" />
      {legend && shown.length > 0 && (
        <div className="pointer-events-none absolute left-1.5 top-1 flex max-w-[calc(100%-4rem)] flex-wrap gap-x-3 gap-y-0.5 px-1.5 py-1 text-2xs">
          {shown.map((h, i) => (
            <span key={`${h.name}-${i}`} className="num flex items-center gap-1.5 whitespace-nowrap">
              <span aria-hidden="true" className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: h.color }} />
              <span className="text-fg-2">{h.name}</span>
              <span className={h.value === null ? "text-fg-2" : "text-fg-0"}>{h.value ?? "—"}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
