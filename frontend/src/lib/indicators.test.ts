// The indicator maths, checked against values that can be worked out by hand. Everything is
// computed from the bars given: a warm-up that has not finished is `null`, never a guess.

import { describe, expect, it } from "vitest";

import {
  BY_ID,
  INDICATORS,
  clampParams,
  defaultsOf,
  ema,
  instanceLabel,
  rma,
  sma,
  stdev,
  wma,
  type Bar,
} from "./indicators";

/** A bar series of `n` sessions whose close follows `close(i)`; high/low bracket it. */
function bars(n: number, close: (i: number) => number, volume = (i: number) => 1000 + i * 10): Bar[] {
  return Array.from({ length: n }, (_, i) => {
    const c = close(i);
    return { date: `2026-01-${String(i + 1).padStart(2, "0")}`, open: c, high: c + 1, low: c - 1, close: c, volume: volume(i) };
  });
}

const flat = (n: number, price = 100) => bars(n, () => price);
const rising = (n: number) => bars(n, (i) => 100 + i);

describe("moving averages", () => {
  it("SMA is the window mean, null through the warm-up", () => {
    const out = sma([1, 2, 3, 4, 5], 3);
    expect(out.slice(0, 2)).toEqual([null, null]);
    expect(out[2]).toBe(2);
    expect(out[4]).toBe(4);
  });

  it("EMA is seeded with the SMA and then weights the new value", () => {
    // period 3 → k = 0.5: seed 2, then 3*0.5 + 2*0.5 = 2.5… walking 1..5 gives 2, 3, 4
    expect(ema([1, 2, 3, 4, 5], 3)).toEqual([null, null, 2, 3, 4]);
  });

  it("EMA of a flat series is that series, after the warm-up", () => {
    const out = ema(new Array(10).fill(42) as number[], 5);
    expect(out[4]).toBe(42);
    expect(out[9]).toBe(42);
  });

  it("WMA weights the newest bar most", () => {
    // period 2 over [10, 20]: (10*1 + 20*2) / 3
    expect(wma([10, 20], 2)[1]).toBeCloseTo(50 / 3, 10);
  });

  it("RMA (Wilder) of a constant series is that constant", () => {
    const out = rma(flat(10).map(() => 3), 4);
    expect(out[9]).toBeCloseTo(3, 10);
  });

  it("standard deviation of a flat series is zero", () => {
    expect(stdev([5, 5, 5, 5], 3)[3]).toBe(0);
  });
});

describe("momentum", () => {
  it("RSI is 100 when every session closed higher", () => {
    const rsi = BY_ID.get("rsi")!.compute(rising(30), { period: 14 });
    expect(rsi[0]!.values[29]).toBeCloseTo(100, 8);
  });

  it("RSI is 0 when every session closed lower", () => {
    const falling = bars(30, (i) => 200 - i);
    const rsi = BY_ID.get("rsi")!.compute(falling, { period: 14 });
    expect(rsi[0]!.values[29]).toBeCloseTo(0, 8);
  });

  it("MACD's histogram is the line minus its signal", () => {
    const out = BY_ID.get("macd")!.compute(rising(80), { fast: 12, slow: 26, signal: 9 });
    const line = out[0]!.values;
    const signal = out[1]!.values;
    const hist = out[2]!.values;
    const i = line.findIndex((v, j) => v !== null && signal[j] !== null);
    expect(i).toBeGreaterThan(0);
    expect(hist[i]).toBeCloseTo(line[i]! - signal[i]!, 10);
  });

  it("ROC over one session is that day's percentage change", () => {
    const out = BY_ID.get("roc")!.compute(rising(5), { period: 1 });
    // closes 100..104, so the last session moved 103 → 104
    expect(out[0]!.values[4]).toBeCloseTo((1 / 103) * 100, 8);
  });

  it("Williams %R sits at 0 when the close is the high of the window", () => {
    const data: Bar[] = Array.from({ length: 20 }, (_, i) => ({
      date: `2026-01-${String(i + 1).padStart(2, "0")}`,
      open: 100 + i,
      high: 100 + i,
      low: 99 + i,
      close: 100 + i,
      volume: 1000,
    }));
    const out = BY_ID.get("williams")!.compute(data, { period: 14 });
    expect(out[0]!.values[19]).toBeCloseTo(0, 8);
  });
});

describe("volatility", () => {
  it("ATR is never negative and warms up in silence", () => {
    const out = BY_ID.get("atr")!.compute(rising(30), { period: 14 });
    expect(out[0]!.values.slice(0, 13).every((v) => v === null)).toBe(true);
    expect(out[0]!.values.slice(14).every((v) => v !== null && v >= 0)).toBe(true);
  });

  it("Bollinger brackets its own midline, which is the SMA", () => {
    const data = bars(40, (i) => 100 + (i % 5) * 2);
    const [upper, lower, mid] = BY_ID.get("bbands")!.compute(data, { period: 20, mult: 2 });
    const i = 39;
    expect(mid!.values[i]).toBeCloseTo(sma(data.map((b) => b.close), 20)[i]!, 10);
    expect(upper!.values[i]!).toBeGreaterThan(mid!.values[i]!);
    expect(lower!.values[i]!).toBeLessThan(mid!.values[i]!);
  });

  it("a flat series has no bands to show", () => {
    const [upper, lower, mid] = BY_ID.get("bbands")!.compute(flat(30), { period: 20, mult: 2 });
    expect(upper!.values[29]).toBe(mid!.values[29]);
    expect(lower!.values[29]).toBe(mid!.values[29]);
  });
});

describe("volume", () => {
  it("OBV adds volume on an up close and subtracts it on a down close", () => {
    const data: Bar[] = [
      { date: "2026-01-01", open: 100, high: 101, low: 99, close: 100, volume: 10 },
      { date: "2026-01-02", open: 100, high: 106, low: 100, close: 105, volume: 20 },
      { date: "2026-01-03", open: 105, high: 105, low: 101, close: 102, volume: 30 },
    ];
    const out = BY_ID.get("obv")!.compute(data, {});
    expect(out[0]!.values).toEqual([0, 20, -10]);
  });

  it("MFI is 100 when money only ever flows in", () => {
    const out = BY_ID.get("mfi")!.compute(rising(30), { period: 14 });
    expect(out[0]!.values[29]).toBeCloseTo(100, 8);
  });
});

describe("the registry", () => {
  it("every indicator computes a series per bar, and nothing else", () => {
    const data = bars(260, (i) => 100 + Math.sin(i / 7) * 8 + i * 0.05, (i) => 500_000 + i * 1000);
    for (const def of INDICATORS) {
      const lines = def.compute(data, defaultsOf(def));
      expect(lines.length, def.id).toBeGreaterThan(0);
      for (const line of lines) {
        expect(line.values, `${def.id}/${line.name}`).toHaveLength(data.length);
        expect(line.values.every((v) => v === null || Number.isFinite(v)), `${def.id}/${line.name}`).toBe(true);
        expect(line.name.length, def.id).toBeGreaterThan(0);
      }
    }
  });

  it("an indicator whose warm-up exceeds the window draws nothing rather than inventing values", () => {
    const out = BY_ID.get("ema")!.compute(rising(10), { period: 200 });
    expect(out[0]!.values.every((v) => v === null)).toBe(true);
  });

  it("is addressable by id, and every preset names a real indicator", () => {
    expect(BY_ID.size).toBe(INDICATORS.length);
    for (const def of INDICATORS) expect(BY_ID.get(def.id)).toBe(def);
  });

  it("clamps parameters into range and rounds whole-number steps", () => {
    const rsi = BY_ID.get("rsi")!;
    expect(clampParams(rsi, { period: 999 })).toEqual({ period: 50 });
    expect(clampParams(rsi, { period: -3 })).toEqual({ period: 2 });
    expect(clampParams(rsi, {})).toEqual({ period: 14 });
    expect(clampParams(rsi, { period: 7.6 })).toEqual({ period: 8 });
    const kc = BY_ID.get("keltner")!;
    expect(clampParams(kc, { period: 20, mult: 99 })).toEqual({ period: 20, mult: 5 });
    expect(clampParams(kc, { period: 20, mult: 1.5 })).toEqual({ period: 20, mult: 1.5 });
  });

  it("labels an instance with its parameters", () => {
    expect(instanceLabel(BY_ID.get("ema")!, { period: 50 })).toBe("EMA 50");
    expect(instanceLabel(BY_ID.get("obv")!, {})).toBe("OBV");
    expect(instanceLabel(BY_ID.get("macd")!, { fast: 12, slow: 26, signal: 9 })).toBe("MACD 12,26,9");
  });
});
