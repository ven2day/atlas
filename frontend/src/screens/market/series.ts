// Turns the operator's active indicators into chart series. The bars passed in are exactly what
// `GET /api/market/{symbol}/bars` returned: no bar is added, interpolated or repeated, and a
// value that is still in its warm-up is simply not plotted.

import type { ChartLine, PaneSpec } from "../../ui/Chart";
import type { ActiveIndicator, Bar } from "../../lib/indicators";
import { BY_ID, clampParams, instanceLabel } from "../../lib/indicators";
import { chartColor } from "../../lib/palette";

/** Lines per instance that get their own colour before the palette repeats. */
const LINES_PER_INSTANCE = 3;

/** The colour of the nth active instance - shared by the chart and the picker's swatches. */
export function instanceColor(index: number): string {
  return chartColor(index * LINES_PER_INSTANCE);
}

export interface BuiltSeries {
  lines: ChartLine[];
  panes: PaneSpec[];
  /** Colour of each active instance, keyed by its store key, for matching swatches in the UI. */
  colors: Record<string, string>;
  /** Keys whose series came out empty: the window is shorter than the indicator's warm-up. */
  tooShort: { key: string; label: string }[];
}

function toPoints(dates: string[], values: (number | null)[]): { time: string; value: number }[] {
  const out: { time: string; value: number }[] = [];
  for (let i = 0; i < values.length; i++) {
    const v = values[i];
    const time = dates[i];
    if (v === null || v === undefined || time === undefined || !Number.isFinite(v)) continue;
    out.push({ time, value: v });
  }
  return out;
}

const AXIS_OF = { number: "number", pct: "pct", price: "price", volume: "volume" } as const;

export function buildIndicatorSeries(bars: Bar[], active: ActiveIndicator[], volumeOn: boolean): BuiltSeries {
  const dates = bars.map((b) => b.date);
  const lines: ChartLine[] = [];
  const panes: PaneSpec[] = [];
  const colors: Record<string, string> = {};
  const tooShort: BuiltSeries["tooShort"] = [];
  let nextPane = 0;

  active.forEach((item, index) => {
    const def = BY_ID.get(item.id);
    if (!def) return;
    const params = clampParams(def, item.params);
    const base = index * LINES_PER_INSTANCE;
    colors[item.key] = chartColor(base);

    const computed = def.compute(bars, params);
    const drawn = computed.some((line) => line.values.some((v) => v !== null));
    if (!drawn) tooShort.push({ key: item.key, label: instanceLabel(def, params) });

    // Volume indicators live on the volume scale when it is shown; otherwise they get a pane.
    const ownPane = def.placement === "pane" || (def.placement === "volume" && !volumeOn);
    let pane = 0;
    let priceScaleId: string | undefined;
    if (ownPane) {
      nextPane += 1;
      pane = nextPane;
      panes.push({ levels: def.levels, axis: AXIS_OF[def.format], stretch: 1 });
    } else if (def.placement === "volume") {
      priceScaleId = "volume";
    }

    computed.forEach((line, j) => {
      lines.push({
        name: line.name,
        points: toPoints(dates, line.values),
        color: chartColor(base + j),
        dashed: line.dashed,
        kind: line.kind,
        pane,
        priceScaleId,
      });
    });
  });

  return { lines, panes, colors, tooShort };
}
