import { lazy, Suspense } from "react";

import type { ChartProps } from "./ChartImpl";

export type { AxisKind, Candle, ChartLine, ChartMarker, ChartProps, PaneSpec } from "./ChartImpl";

// lightweight-charts is loaded only when a chart is on screen: it stays out of the initial
// route's budget.
const ChartImpl = lazy(() => import("./ChartImpl"));

export function Chart(props: ChartProps) {
  return (
    <Suspense
      fallback={
        <div className="flex items-center justify-center" style={{ height: props.height ?? 320 }}>
          <span className="rq-skeleton h-full w-full" />
        </div>
      }
    >
      <ChartImpl {...props} />
    </Suspense>
  );
}
