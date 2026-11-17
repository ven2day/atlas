// The categorical palette for chart series. Values are CSS variable references, so a component
// can put one straight into a `style` attribute and the chart can resolve it to a colour.

export const CHART_COLOR_COUNT = 8;

/** A stable colour for the nth series: `var(--chart-1)` … cycling after 8. */
export function chartColor(index: number): string {
  const n = (((index % CHART_COLOR_COUNT) + CHART_COLOR_COUNT) % CHART_COLOR_COUNT) + 1;
  return `var(--chart-${n})`;
}
