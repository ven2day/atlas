// Technical indicators, computed in the browser from the daily OHLCV the API actually returns
// (`GET /api/market/{symbol}/bars`). Nothing here invents a bar: every series is aligned to the
// input by index and is `null` through its warm-up, so the chart shows only what the data
// supports. Warm-up length matters - a 200-session EMA over a 30-bar window draws almost nothing,
// and that is the honest answer.

export interface Bar {
  date: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

export type Series = (number | null)[];

export type Category = "trend" | "momentum" | "volatility" | "volume";

/** Where an indicator's output is drawn. */
export type Placement = "overlay" | "pane" | "volume";

export interface IndicatorParam {
  key: string;
  label: string;
  min: number;
  max: number;
  step?: number;
  default: number;
}

export interface IndicatorLine {
  name: string;
  values: Series;
  /** A histogram (the MACD bars) rather than a line. */
  kind?: "line" | "histogram";
  /** A dashed reference line, e.g. a channel midline. */
  dashed?: boolean;
  /** Index into the chart's categorical palette. */
  tone?: number;
}

/** Parameter values as the operator set them; each `compute` destructures its own with defaults. */
export type Params = Record<string, number>;

export interface IndicatorDef {
  id: string;
  name: string;
  abbr: string;
  category: Category;
  placement: Placement;
  description: string;
  params: IndicatorParam[];
  /** Horizontal reference lines drawn in its own pane, e.g. 30 / 70 for RSI. */
  levels?: number[];
  format: "number" | "pct" | "price" | "volume";
  compute: (bars: Bar[], params: Params) => IndicatorLine[];
}

// -- primitives ---------------------------------------------------------------------------------

const ok = (n: number | null | undefined): number | null =>
  n === null || n === undefined || !Number.isFinite(n) ? null : n;

const filled = (n: number): Series => Array.from({ length: n }, () => null);

/** Reads `values[i]`, treating an out-of-range slot the same as a missing one. */
function at(values: Series, i: number): number | null {
  const v = values[i];
  return v === undefined ? null : v;
}

function column(bars: Bar[], key: keyof Bar): number[] {
  return bars.map((b) => Number(b[key]));
}

function sumWindow(values: Series, index: number, period: number): number | null {
  if (index + 1 < period) return null;
  let total = 0;
  for (let i = index - period + 1; i <= index; i++) {
    const v = at(values, i);
    if (v === null) return null;
    total += v;
  }
  return total;
}

/** Simple moving average. */
export function sma(values: Series, period: number): Series {
  if (period < 1) return filled(values.length);
  const out = filled(values.length);
  for (let i = 0; i < values.length; i++) {
    const total = sumWindow(values, i, period);
    out[i] = total === null ? null : ok(total / period);
  }
  return out;
}

/** Exponential moving average, seeded with the SMA of the first `period` values. */
export function ema(values: Series, period: number): Series {
  const out = filled(values.length);
  if (period < 1) return out;
  const k = 2 / (period + 1);
  let prev: number | null = null;
  for (let i = 0; i < values.length; i++) {
    const v = at(values, i);
    if (v === null) continue;
    if (prev === null) {
      const seed = sumWindow(values, i, period);
      if (seed === null) continue;
      prev = seed / period;
    } else {
      prev = v * k + prev * (1 - k);
    }
    out[i] = ok(prev);
  }
  return out;
}

/** Wilder's smoothing (RMA): the basis of RSI, ATR and ADX. */
export function rma(values: Series, period: number): Series {
  const out = filled(values.length);
  if (period < 1) return out;
  let prev: number | null = null;
  for (let i = 0; i < values.length; i++) {
    const v = at(values, i);
    if (v === null) continue;
    if (prev === null) {
      const seed = sumWindow(values, i, period);
      if (seed === null) continue;
      prev = seed / period;
    } else {
      prev = (prev * (period - 1) + v) / period;
    }
    out[i] = ok(prev);
  }
  return out;
}

/** Weighted moving average (linear weights, the newest bar counting most). */
export function wma(values: Series, period: number): Series {
  const out = filled(values.length);
  const denom = (period * (period + 1)) / 2;
  for (let i = period - 1; i < values.length; i++) {
    let total = 0;
    let missing = false;
    for (let j = 0; j < period; j++) {
      const v = at(values, i - j);
      if (v === null) {
        missing = true;
        break;
      }
      total += v * (period - j);
    }
    out[i] = missing ? null : ok(total / denom);
  }
  return out;
}

/** Population standard deviation over the window. */
export function stdev(values: Series, period: number): Series {
  const out = filled(values.length);
  for (let i = period - 1; i < values.length; i++) {
    const total = sumWindow(values, i, period);
    if (total === null) continue;
    const mean = total / period;
    let acc = 0;
    for (let j = i - period + 1; j <= i; j++) acc += ((at(values, j) ?? 0) - mean) ** 2;
    out[i] = ok(Math.sqrt(acc / period));
  }
  return out;
}

function extreme(values: Series, period: number, pick: (...v: number[]) => number): Series {
  const out = filled(values.length);
  for (let i = period - 1; i < values.length; i++) {
    const slice: number[] = [];
    for (let j = i - period + 1; j <= i; j++) {
      const v = at(values, j);
      if (v === null) break;
      slice.push(v);
    }
    out[i] = slice.length === period ? ok(pick(...slice)) : null;
  }
  return out;
}

export function hhv(values: Series, period: number): Series {
  return extreme(values, period, Math.max);
}

export function llv(values: Series, period: number): Series {
  return extreme(values, period, Math.min);
}

function shift(values: Series, by: number): Series {
  const out = filled(values.length);
  for (let i = by; i < values.length; i++) out[i] = at(values, i - by);
  return out;
}

function trueRange(bars: Bar[]): Series {
  const out = filled(bars.length);
  for (let i = 0; i < bars.length; i++) {
    const bar = bars[i]!;
    if (i === 0) {
      out[i] = ok(bar.high - bar.low);
      continue;
    }
    const prevClose = bars[i - 1]!.close;
    out[i] = ok(Math.max(bar.high - bar.low, Math.abs(bar.high - prevClose), Math.abs(bar.low - prevClose)));
  }
  return out;
}

function zip(values: Series, fn: (v: number, i: number) => number | null): Series {
  return values.map((v, i) => (v === null ? null : ok(fn(v, i))));
}

function combine(a: Series, b: Series, fn: (x: number, y: number) => number | null): Series {
  return a.map((x, i) => {
    const y = at(b, i);
    return x === null || y === null ? null : ok(fn(x, y));
  });
}

const param = (key: string, label: string, min: number, max: number, value: number, step = 1): IndicatorParam => ({
  key,
  label,
  min,
  max,
  step,
  default: value,
});

export function defaultsOf(def: IndicatorDef): Params {
  return Object.fromEntries(def.params.map((p) => [p.key, p.default]));
}

// -- trend --------------------------------------------------------------------------------------

const smaDef: IndicatorDef = {
  id: "sma",
  name: "Simple moving average",
  abbr: "SMA",
  category: "trend",
  placement: "overlay",
  description: "The mean close over N sessions. Flattens noise; slow to turn.",
  params: [param("period", "Period", 2, 200, 20)],
  format: "price",
  compute: (bars, { period = 20 }) => [{ name: `SMA ${period}`, values: sma(column(bars, "close"), period) }],
};

const emaDef: IndicatorDef = {
  id: "ema",
  name: "Exponential moving average",
  abbr: "EMA",
  category: "trend",
  placement: "overlay",
  description: "A moving average weighted towards recent closes, so it turns sooner than the SMA.",
  params: [param("period", "Period", 2, 200, 21)],
  format: "price",
  compute: (bars, { period = 21 }) => [{ name: `EMA ${period}`, values: ema(column(bars, "close"), period) }],
};

const wmaDef: IndicatorDef = {
  id: "wma",
  name: "Weighted moving average",
  abbr: "WMA",
  category: "trend",
  placement: "overlay",
  description: "Linearly weighted mean close: the newest session counts most.",
  params: [param("period", "Period", 2, 200, 20)],
  format: "price",
  compute: (bars, { period = 20 }) => [{ name: `WMA ${period}`, values: wma(column(bars, "close"), period) }],
};

const donchianDef: IndicatorDef = {
  id: "donchian",
  name: "Donchian channel",
  abbr: "DC",
  category: "trend",
  placement: "overlay",
  description: "The highest high and lowest low of the last N sessions, with their midline.",
  params: [param("period", "Period", 2, 200, 20)],
  format: "price",
  compute: (bars, { period = 20 }) => {
    const upper = hhv(column(bars, "high"), period);
    const lower = llv(column(bars, "low"), period);
    return [
      { name: `DC upper ${period}`, values: upper, tone: 0 },
      { name: `DC lower ${period}`, values: lower, tone: 0 },
      { name: `DC mid ${period}`, values: combine(upper, lower, (a, b) => (a + b) / 2), dashed: true, tone: 1 },
    ];
  },
};

const keltnerDef: IndicatorDef = {
  id: "keltner",
  name: "Keltner channel",
  abbr: "KC",
  category: "trend",
  placement: "overlay",
  description: "An EMA banded by a multiple of ATR: a smoother alternative to Bollinger.",
  params: [param("period", "Period", 2, 100, 20), param("mult", "ATR multiple", 0.5, 5, 2, 0.5)],
  format: "price",
  compute: (bars, { period = 20, mult = 2 }) => {
    const mid = ema(column(bars, "close"), period);
    const band = rma(trueRange(bars), period).map((v) => (v === null ? null : v * mult));
    return [
      { name: `KC mid ${period}`, values: mid, tone: 1 },
      { name: `KC upper ${period}`, values: combine(mid, band, (a, b) => a + b), tone: 0 },
      { name: `KC lower ${period}`, values: combine(mid, band, (a, b) => a - b), tone: 0 },
    ];
  },
};

const supertrendDef: IndicatorDef = {
  id: "supertrend",
  name: "Supertrend",
  abbr: "ST",
  category: "trend",
  placement: "overlay",
  description: "An ATR trailing band that flips side when the close crosses it.",
  params: [param("period", "ATR period", 2, 50, 10), param("mult", "Multiplier", 0.5, 6, 3, 0.5)],
  format: "price",
  compute: (bars, { period = 10, mult = 3 }) => {
    const out = filled(bars.length);
    if (bars.length === 0) return [{ name: `Supertrend ${period},${mult}`, values: out }];
    const atr = rma(trueRange(bars), period);
    let finalUpper: number | null = null;
    let finalLower: number | null = null;
    let trend: 1 | -1 = 1;
    for (let i = 0; i < bars.length; i++) {
      const a = at(atr, i);
      const bar = bars[i]!;
      if (a === null) continue;
      const prevClose = i > 0 ? bars[i - 1]!.close : bar.close;
      const mid = (bar.high + bar.low) / 2;
      const basicUpper = mid + mult * a;
      const basicLower = mid - mult * a;
      const prevUpper: number | null = finalUpper;
      const prevLower: number | null = finalLower;
      if (prevUpper === null || prevLower === null) {
        finalUpper = basicUpper;
        finalLower = basicLower;
      } else {
        finalUpper = basicUpper < prevUpper || prevClose > prevUpper ? basicUpper : prevUpper;
        finalLower = basicLower > prevLower || prevClose < prevLower ? basicLower : prevLower;
      }
      const upper = finalUpper ?? basicUpper;
      const lower = finalLower ?? basicLower;
      if (bar.close > upper) trend = 1;
      else if (bar.close < lower) trend = -1;
      out[i] = ok(trend === 1 ? lower : upper);
    }
    return [{ name: `Supertrend ${period},${mult}`, values: out }];
  },
};

const sarDef: IndicatorDef = {
  id: "psar",
  name: "Parabolic SAR",
  abbr: "PSAR",
  category: "trend",
  placement: "overlay",
  description: "A stop that accelerates towards the trend and flips when the trend reverses.",
  params: [param("step", "Step ×100", 1, 10, 2), param("max", "Max ×100", 5, 40, 20)],
  format: "price",
  compute: (bars, { step = 2, max = 20 }) => {
    const out = filled(bars.length);
    if (bars.length < 3) return [{ name: "PSAR", values: out }];
    const afStep = step / 100;
    const afMax = max / 100;
    let bull = bars[1]!.close >= bars[0]!.close;
    let af = afStep;
    let ep = bull ? bars[0]!.high : bars[0]!.low;
    let sar = bull ? bars[0]!.low : bars[0]!.high;
    out[0] = ok(sar);
    for (let i = 1; i < bars.length; i++) {
      const bar = bars[i]!;
      const prev = bars[i - 1]!;
      // The classic clamp uses the prior two sessions; at i = 1 there is only one.
      const prev2 = bars[i - 2] ?? prev;
      sar += af * (ep - sar);
      sar = bull ? Math.min(sar, prev.low, prev2.low) : Math.max(sar, prev.high, prev2.high);
      const reversed = bull ? bar.low < sar : bar.high > sar;
      if (reversed) {
        sar = ep;
        bull = !bull;
        ep = bull ? bar.high : bar.low;
        af = afStep;
      } else if (bull ? bar.high > ep : bar.low < ep) {
        ep = bull ? bar.high : bar.low;
        af = Math.min(af + afStep, afMax);
      }
      out[i] = ok(sar);
    }
    return [{ name: "PSAR", values: out }];
  },
};

const adxDef: IndicatorDef = {
  id: "adx",
  name: "ADX / directional movement",
  abbr: "ADX",
  category: "trend",
  placement: "pane",
  description: "Trend strength (ADX) with its directional legs (+DI, −DI). Above ~25 is trending.",
  params: [param("period", "Period", 2, 50, 14)],
  levels: [20, 40],
  format: "number",
  compute: (bars, { period = 14 }) => {
    const n = bars.length;
    const plusDM: Series = filled(n);
    const minusDM: Series = filled(n);
    for (let i = 1; i < n; i++) {
      const up = bars[i]!.high - bars[i - 1]!.high;
      const down = bars[i - 1]!.low - bars[i]!.low;
      plusDM[i] = ok(up > down && up > 0 ? up : 0);
      minusDM[i] = ok(down > up && down > 0 ? down : 0);
    }
    const atr = rma(trueRange(bars), period);
    const plusDI = combine(rma(plusDM, period), atr, (a, b) => (b === 0 ? 0 : (100 * a) / b));
    const minusDI = combine(rma(minusDM, period), atr, (a, b) => (b === 0 ? 0 : (100 * a) / b));
    const dx = combine(plusDI, minusDI, (a, b) => (a + b === 0 ? 0 : (100 * Math.abs(a - b)) / (a + b)));
    return [
      { name: `ADX ${period}`, values: rma(dx, period), tone: 0 },
      { name: "+DI", values: plusDI, tone: 1 },
      { name: "−DI", values: minusDI, tone: 2 },
    ];
  },
};

const vwapDef: IndicatorDef = {
  id: "rvwap",
  name: "Rolling VWAP",
  abbr: "rVWAP",
  category: "trend",
  placement: "overlay",
  description:
    "Typical price weighted by volume over a rolling N sessions. The API serves daily bars only, so a session-anchored VWAP is not available.",
  params: [param("period", "Period", 2, 100, 20)],
  format: "price",
  compute: (bars, { period = 20 }) => {
    const out = filled(bars.length);
    for (let i = period - 1; i < bars.length; i++) {
      let pv = 0;
      let vol = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const b = bars[j]!;
        pv += ((b.high + b.low + b.close) / 3) * b.volume;
        vol += b.volume;
      }
      out[i] = vol > 0 ? ok(pv / vol) : null;
    }
    return [{ name: `rVWAP ${period}`, values: out }];
  },
};

// -- momentum -----------------------------------------------------------------------------------

const rsiDef: IndicatorDef = {
  id: "rsi",
  name: "Relative strength index",
  abbr: "RSI",
  category: "momentum",
  placement: "pane",
  description: "Wilder's 0-100 oscillator: the share of recent movement that was up.",
  params: [param("period", "Period", 2, 50, 14)],
  levels: [30, 70],
  format: "number",
  compute: (bars, { period = 14 }) => {
    const n = bars.length;
    const gains: Series = filled(n);
    const losses: Series = filled(n);
    for (let i = 1; i < n; i++) {
      const change = bars[i]!.close - bars[i - 1]!.close;
      gains[i] = ok(Math.max(change, 0));
      losses[i] = ok(Math.max(-change, 0));
    }
    const avgGain = rma(gains, period);
    const avgLoss = rma(losses, period);
    return [
      {
        name: `RSI ${period}`,
        values: combine(avgGain, avgLoss, (g, l) => (g + l === 0 ? 50 : (100 * g) / (g + l))),
      },
    ];
  },
};

const macdDef: IndicatorDef = {
  id: "macd",
  name: "MACD",
  abbr: "MACD",
  category: "momentum",
  placement: "pane",
  description: "The gap between a fast and a slow EMA, its signal line, and the histogram between them.",
  params: [param("fast", "Fast", 2, 50, 12), param("slow", "Slow", 3, 100, 26), param("signal", "Signal", 2, 50, 9)],
  levels: [0],
  format: "number",
  compute: (bars, { fast = 12, slow = 26, signal = 9 }) => {
    const close = column(bars, "close");
    const line = combine(ema(close, fast), ema(close, slow), (a, b) => a - b);
    const sig = ema(line, signal);
    return [
      { name: `MACD ${fast},${slow}`, values: line, tone: 0 },
      { name: `Signal ${signal}`, values: sig, tone: 1 },
      { name: "Histogram", values: combine(line, sig, (a, b) => a - b), kind: "histogram", tone: 2 },
    ];
  },
};

const stochDef: IndicatorDef = {
  id: "stoch",
  name: "Stochastic oscillator",
  abbr: "STOCH",
  category: "momentum",
  placement: "pane",
  description: "Where the close sits in the last N sessions' range, smoothed into %K and %D.",
  params: [param("k", "%K period", 2, 50, 14), param("smooth", "%K smooth", 1, 10, 3), param("d", "%D period", 1, 20, 3)],
  levels: [20, 80],
  format: "number",
  compute: (bars, { k = 14, smooth = 3, d = 3 }) => {
    const high = hhv(column(bars, "high"), k);
    const low = llv(column(bars, "low"), k);
    const pctK: Series = filled(bars.length);
    for (let i = 0; i < bars.length; i++) {
      const h = at(high, i);
      const l = at(low, i);
      if (h === null || l === null) continue;
      pctK[i] = ok(h === l ? 50 : (100 * (bars[i]!.close - l)) / (h - l));
    }
    const smoothK = sma(pctK, smooth);
    return [
      { name: `%K ${k},${smooth}`, values: smoothK, tone: 0 },
      { name: `%D ${d}`, values: sma(smoothK, d), tone: 1 },
    ];
  },
};

const stochRsiDef: IndicatorDef = {
  id: "stochrsi",
  name: "Stochastic RSI",
  abbr: "STOCHRSI",
  category: "momentum",
  placement: "pane",
  description: "The stochastic of the RSI: more sensitive, and noisier, than either alone.",
  params: [param("rsi", "RSI period", 2, 50, 14), param("k", "Stoch period", 2, 50, 14), param("d", "%D", 1, 20, 3)],
  levels: [20, 80],
  format: "number",
  compute: (bars, { rsi = 14, k = 14, d = 3 }) => {
    const base = rsiDef.compute(bars, { period: rsi })[0]!.values;
    const high = hhv(base, k);
    const low = llv(base, k);
    const line: Series = base.map((v, i) => {
      const h = at(high, i);
      const l = at(low, i);
      if (v === null || h === null || l === null) return null;
      return ok(h === l ? 50 : (100 * (v - l)) / (h - l));
    });
    return [
      { name: "%K", values: line, tone: 0 },
      { name: "%D", values: sma(line, d), tone: 1 },
    ];
  },
};

const williamsDef: IndicatorDef = {
  id: "williams",
  name: "Williams %R",
  abbr: "%R",
  category: "momentum",
  placement: "pane",
  description: "An inverted stochastic on a −100 to 0 scale.",
  params: [param("period", "Period", 2, 50, 14)],
  levels: [-80, -20],
  format: "number",
  compute: (bars, { period = 14 }) => {
    const high = hhv(column(bars, "high"), period);
    const low = llv(column(bars, "low"), period);
    const out = filled(bars.length);
    for (let i = 0; i < bars.length; i++) {
      const h = at(high, i);
      const l = at(low, i);
      if (h === null || l === null) continue;
      out[i] = ok(h === l ? -50 : (-100 * (h - bars[i]!.close)) / (h - l));
    }
    return [{ name: `Williams %R ${period}`, values: out }];
  },
};

const rocDef: IndicatorDef = {
  id: "roc",
  name: "Rate of change",
  abbr: "ROC",
  category: "momentum",
  placement: "pane",
  description: "The percentage change against the close N sessions ago.",
  params: [param("period", "Period", 1, 60, 12)],
  levels: [0],
  format: "pct",
  compute: (bars, { period = 12 }) => {
    const close = column(bars, "close");
    const prev = shift(close, period);
    return [{ name: `ROC ${period}`, values: combine(close, prev, (a, b) => (b === 0 ? null : ((a - b) / b) * 100)) }];
  },
};

const momDef: IndicatorDef = {
  id: "momentum",
  name: "Momentum",
  abbr: "MOM",
  category: "momentum",
  placement: "pane",
  description: "The plain point difference against the close N sessions ago.",
  params: [param("period", "Period", 1, 60, 10)],
  levels: [0],
  format: "number",
  compute: (bars, { period = 10 }) => {
    const close = column(bars, "close");
    return [{ name: `Momentum ${period}`, values: combine(close, shift(close, period), (a, b) => a - b) }];
  },
};

const cciDef: IndicatorDef = {
  id: "cci",
  name: "Commodity channel index",
  abbr: "CCI",
  category: "momentum",
  placement: "pane",
  description: "The typical price against its own mean, in units of mean deviation.",
  params: [param("period", "Period", 2, 60, 20)],
  levels: [-100, 100],
  format: "number",
  compute: (bars, { period = 20 }) => {
    const tp = bars.map((b) => ok((b.high + b.low + b.close) / 3));
    const mean = sma(tp, period);
    const out = filled(bars.length);
    for (let i = period - 1; i < bars.length; i++) {
      const m = at(mean, i);
      if (m === null) continue;
      let dev = 0;
      for (let j = i - period + 1; j <= i; j++) dev += Math.abs((at(tp, j) ?? 0) - m);
      dev /= period;
      out[i] = dev === 0 ? null : ok(((at(tp, i) ?? 0) - m) / (0.015 * dev));
    }
    return [{ name: `CCI ${period}`, values: out }];
  },
};

// -- volatility ---------------------------------------------------------------------------------

const bbDef: IndicatorDef = {
  id: "bbands",
  name: "Bollinger bands",
  abbr: "BB",
  category: "volatility",
  placement: "overlay",
  description: "An SMA banded by N standard deviations: the squeeze before a move.",
  params: [param("period", "Period", 2, 100, 20), param("mult", "Std dev", 0.5, 4, 2, 0.5)],
  format: "price",
  compute: (bars, { period = 20, mult = 2 }) => {
    const close = column(bars, "close");
    const mid = sma(close, period);
    const dev = stdev(close, period).map((v) => (v === null ? null : v * mult));
    return [
      { name: `BB upper ${period}`, values: combine(mid, dev, (a, b) => a + b), tone: 0 },
      { name: `BB lower ${period}`, values: combine(mid, dev, (a, b) => a - b), tone: 0 },
      { name: `BB mid ${period}`, values: mid, dashed: true, tone: 1 },
    ];
  },
};

const atrDef: IndicatorDef = {
  id: "atr",
  name: "Average true range",
  abbr: "ATR",
  category: "volatility",
  placement: "pane",
  description: "Wilder's mean true range: the size of a typical session, which is how this system sizes risk.",
  params: [param("period", "Period", 2, 50, 14)],
  format: "price",
  compute: (bars, { period = 14 }) => [{ name: `ATR ${period}`, values: rma(trueRange(bars), period) }],
};

const atrPctDef: IndicatorDef = {
  id: "atrp",
  name: "ATR percentage",
  abbr: "ATR%",
  category: "volatility",
  placement: "pane",
  description: "ATR as a percentage of the close, so two symbols can be compared.",
  params: [param("period", "Period", 2, 50, 14)],
  format: "pct",
  compute: (bars, { period = 14 }) => {
    const atr = rma(trueRange(bars), period);
    return [{ name: `ATR% ${period}`, values: zip(atr, (v, i) => (bars[i]!.close === 0 ? null : (100 * v) / bars[i]!.close)) }];
  },
};

const sdDef: IndicatorDef = {
  id: "stdev",
  name: "Standard deviation",
  abbr: "SD",
  category: "volatility",
  placement: "pane",
  description: "The dispersion of closes around their mean over N sessions.",
  params: [param("period", "Period", 2, 100, 20)],
  format: "price",
  compute: (bars, { period = 20 }) => [{ name: `SD ${period}`, values: stdev(column(bars, "close"), period) }],
};

const rangeDef: IndicatorDef = {
  id: "range",
  name: "Session range %",
  abbr: "RANGE",
  category: "volatility",
  placement: "pane",
  description: "How wide each session traded, as a percentage of its close.",
  params: [],
  format: "pct",
  compute: (bars) => [
    {
      name: "Range %",
      values: bars.map((b) => ok(b.close === 0 ? null : ((b.high - b.low) / b.close) * 100)),
      kind: "histogram",
    },
  ],
};

// -- volume -------------------------------------------------------------------------------------

const obvDef: IndicatorDef = {
  id: "obv",
  name: "On-balance volume",
  abbr: "OBV",
  category: "volume",
  placement: "pane",
  description: "Volume added on up closes and subtracted on down closes: cumulative conviction.",
  params: [],
  format: "volume",
  compute: (bars) => {
    const out = filled(bars.length);
    let total = 0;
    for (let i = 0; i < bars.length; i++) {
      if (i > 0) {
        const change = bars[i]!.close - bars[i - 1]!.close;
        if (change > 0) total += bars[i]!.volume;
        else if (change < 0) total -= bars[i]!.volume;
      }
      out[i] = ok(total);
    }
    return [{ name: "OBV", values: out }];
  },
};

const adlDef: IndicatorDef = {
  id: "adl",
  name: "Accumulation / distribution",
  abbr: "ADL",
  category: "volume",
  placement: "pane",
  description: "Volume weighted by where the close sits in the session's range, accumulated.",
  params: [],
  format: "volume",
  compute: (bars) => {
    const out = filled(bars.length);
    let total = 0;
    for (let i = 0; i < bars.length; i++) {
      const b = bars[i]!;
      const span = b.high - b.low;
      if (i > 0) total += span === 0 ? 0 : ((b.close - b.low - (b.high - b.close)) / span) * b.volume;
      out[i] = ok(total);
    }
    return [{ name: "ADL", values: out }];
  },
};

function moneyFlowVolume(bars: Bar[]): Series {
  const out = filled(bars.length);
  for (let i = 1; i < bars.length; i++) {
    const b = bars[i]!;
    const prev = bars[i - 1]!;
    const tp = (b.high + b.low + b.close) / 3;
    const prevTp = (prev.high + prev.low + prev.close) / 3;
    out[i] = ok((tp - prevTp) * b.volume);
  }
  return out;
}

const cmfDef: IndicatorDef = {
  id: "cmf",
  name: "Chaikin money flow",
  abbr: "CMF",
  category: "volume",
  placement: "pane",
  description: "Money-flow volume over total volume across N sessions: above zero is accumulation.",
  params: [param("period", "Period", 2, 60, 20)],
  levels: [0],
  format: "number",
  compute: (bars, { period = 20 }) => {
    // CMF is sum(money-flow volume) / sum(volume); the two means share the window length, so
    // their ratio is the ratio of the sums.
    const mfv = moneyFlowVolume(bars);
    const vol = column(bars, "volume");
    return [
      { name: `CMF ${period}`, values: combine(sma(mfv, period), sma(vol, period), (a, b) => (b === 0 ? null : a / b)) },
    ];
  },
};

const mfiDef: IndicatorDef = {
  id: "mfi",
  name: "Money flow index",
  abbr: "MFI",
  category: "volume",
  placement: "pane",
  description: "A volume-weighted RSI: the share of money flow that was positive.",
  params: [param("period", "Period", 2, 50, 14)],
  levels: [20, 80],
  format: "number",
  compute: (bars, { period = 14 }) => {
    const out = filled(bars.length);
    for (let i = period; i < bars.length; i++) {
      let pos = 0;
      let neg = 0;
      for (let j = i - period + 1; j <= i; j++) {
        const b = bars[j]!;
        const prev = bars[j - 1]!;
        const tp = (b.high + b.low + b.close) / 3;
        const prevTp = (prev.high + prev.low + prev.close) / 3;
        const flow = tp * b.volume;
        if (tp > prevTp) pos += flow;
        else if (tp < prevTp) neg += flow;
      }
      out[i] = ok(neg === 0 ? 100 : 100 - 100 / (1 + pos / neg));
    }
    return [{ name: `MFI ${period}`, values: out }];
  },
};

const vptDef: IndicatorDef = {
  id: "vpt",
  name: "Volume price trend",
  abbr: "VPT",
  category: "volume",
  placement: "pane",
  description: "OBV scaled by the size of each move rather than its direction alone.",
  params: [],
  format: "volume",
  compute: (bars) => {
    const out = filled(bars.length);
    let total = 0;
    for (let i = 0; i < bars.length; i++) {
      const prevClose = i > 0 ? bars[i - 1]!.close : 0;
      if (prevClose !== 0) total += ((bars[i]!.close - prevClose) / prevClose) * bars[i]!.volume;
      out[i] = ok(total);
    }
    return [{ name: "VPT", values: out }];
  },
};

const volSmaDef: IndicatorDef = {
  id: "volsma",
  name: "Volume moving average",
  abbr: "VOL SMA",
  category: "volume",
  placement: "volume",
  description: "The mean volume over N sessions, drawn over the volume histogram.",
  params: [param("period", "Period", 2, 60, 20)],
  format: "volume",
  compute: (bars, { period = 20 }) => [{ name: `Volume SMA ${period}`, values: sma(column(bars, "volume"), period) }],
};

// -- the registry -------------------------------------------------------------------------------

export const INDICATORS: IndicatorDef[] = [
  smaDef,
  emaDef,
  wmaDef,
  bbDef,
  donchianDef,
  keltnerDef,
  supertrendDef,
  sarDef,
  adxDef,
  vwapDef,
  rsiDef,
  macdDef,
  stochDef,
  stochRsiDef,
  williamsDef,
  rocDef,
  momDef,
  cciDef,
  atrDef,
  atrPctDef,
  sdDef,
  rangeDef,
  obvDef,
  adlDef,
  cmfDef,
  mfiDef,
  vptDef,
  volSmaDef,
];

export const BY_ID = new Map(INDICATORS.map((d) => [d.id, d]));

export const CATEGORY_LABEL: Record<Category, string> = {
  trend: "Trend",
  momentum: "Momentum",
  volatility: "Volatility",
  volume: "Volume",
};

export interface Preset {
  id: string;
  name: string;
  description: string;
  items: { id: string; params?: Params }[];
}

/** Combinations worth one click, built only from the indicators above. */
export const PRESETS: Preset[] = [
  {
    id: "trend-ribbon",
    name: "Trend ribbon",
    description: "Price against its 20 / 50 / 200 session means.",
    items: [
      { id: "sma", params: { period: 20 } },
      { id: "ema", params: { period: 50 } },
      { id: "ema", params: { period: 200 } },
    ],
  },
  {
    id: "swing",
    name: "Swing check",
    description: "Bollinger bands with RSI and MACD: how stretched, and whether it is turning.",
    items: [{ id: "bbands" }, { id: "rsi" }, { id: "macd" }],
  },
  {
    id: "volatility",
    name: "Volatility",
    description: "ATR and its percentage form, with a Keltner channel on price.",
    items: [{ id: "atr" }, { id: "atrp" }, { id: "keltner" }],
  },
  {
    id: "volume",
    name: "Volume confirmation",
    description: "OBV, accumulation/distribution and the money flow index.",
    items: [{ id: "obv" }, { id: "adl" }, { id: "mfi" }],
  },
];

/** One indicator instance the operator has turned on, with its own parameter values. */
export interface ActiveIndicator {
  /** Unique per instance: the same indicator can be on twice with different periods. */
  key: string;
  id: string;
  params: Params;
}

export function clampParams(def: IndicatorDef, params: Params): Params {
  const out: Params = {};
  for (const p of def.params) {
    const raw = params[p.key];
    const n = typeof raw === "number" && Number.isFinite(raw) ? raw : p.default;
    out[p.key] = Math.min(p.max, Math.max(p.min, p.step !== undefined && p.step < 1 ? n : Math.round(n)));
  }
  return out;
}

/** The label shown on an active indicator's chip, e.g. "EMA 50". */
export function instanceLabel(def: IndicatorDef, params: Params): string {
  const p = clampParams(def, params);
  const suffix = def.params.map((x) => p[x.key]).join(",");
  return suffix ? `${def.abbr} ${suffix}` : def.abbr;
}
