import type { ReactNode } from "react";

import { cx } from "../lib/cx";

/** A plain, readable table for short, fixed-shape data. Long or filterable lists use DataTable. */
export function Table({
  head,
  rows,
  align = [],
  minWidth,
  className,
  footer,
}: {
  head: ReactNode[];
  rows: ReactNode[][];
  align?: ("l" | "r")[];
  /** Keeps wide tables readable on a phone: they scroll sideways instead of crushing. */
  minWidth?: number;
  className?: string;
  footer?: ReactNode;
}) {
  return (
    <div className={cx("w-full overflow-x-auto", className)}>
      <table className="w-full border-collapse text-sm" style={minWidth ? { minWidth } : undefined}>
        <thead>
          <tr className="border-b border-line-strong bg-bg-2 text-left text-2xs uppercase tracking-wide text-fg-2">
            {head.map((h, i) => (
              <th
                key={i}
                scope="col"
                className={cx("whitespace-nowrap px-4 py-2.5 font-medium", align[i] === "r" && "text-right")}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-b border-line last:border-0">
              {r.map((c, j) => (
                <td key={j} className={cx("px-4 py-2.5 align-middle text-fg-0", align[j] === "r" && "num text-right")}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
        {footer && (
          <tfoot>
            <tr>
              <td colSpan={head.length} className="px-4 py-2.5 text-2xs leading-relaxed text-fg-2">
                {footer}
              </td>
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
