import * as RadixTabs from "@radix-ui/react-tabs";
import type { ReactNode } from "react";

import { cx } from "../lib/cx";

export interface TabSpec {
  value: string;
  label: ReactNode;
  /** Shown after the label: a count or a state. */
  meta?: ReactNode;
  content: ReactNode;
}

/** Radix tabs as a segmented pill control: arrows move between tabs, content mounts on demand. */
export function Tabs({
  tabs,
  value,
  onValueChange,
  label,
  className,
  listClassName,
}: {
  tabs: TabSpec[];
  value?: string;
  onValueChange?: (value: string) => void;
  label: string;
  className?: string;
  listClassName?: string;
}) {
  return (
    <RadixTabs.Root
      value={value}
      defaultValue={value === undefined ? tabs[0]?.value : undefined}
      onValueChange={onValueChange}
      className={cx("flex min-h-0 flex-1 flex-col gap-3", className)}
    >
      <RadixTabs.List
        aria-label={label}
        className={cx(
          "flex shrink-0 items-center gap-1 overflow-x-auto rounded-lg border border-line bg-bg-1 p-1",
          listClassName,
        )}
      >
        {tabs.map((t) => (
          <RadixTabs.Trigger
            key={t.value}
            value={t.value}
            className={cx(
              "inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md px-3.5 text-sm font-medium",
              "text-fg-2 transition-colors duration-150 hover:text-fg-0",
              "data-[state=active]:bg-bg-3 data-[state=active]:text-fg-0 data-[state=active]:shadow-card",
            )}
          >
            {t.label}
            {t.meta !== undefined && <span className="num text-2xs text-fg-2">{t.meta}</span>}
          </RadixTabs.Trigger>
        ))}
      </RadixTabs.List>
      {tabs.map((t) => (
        <RadixTabs.Content key={t.value} value={t.value} className="min-h-0 flex-1 outline-none">
          {t.content}
        </RadixTabs.Content>
      ))}
    </RadixTabs.Root>
  );
}
