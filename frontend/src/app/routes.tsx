import { lazy } from "react";

// Every screen is its own chunk: the initial route stays inside the 250 KB budget.
const CommandCenter = lazy(() => import("../screens/CommandCenter"));
const DecisionInspector = lazy(() => import("../screens/DecisionInspector"));
const Decisions = lazy(() => import("../screens/Decisions"));
const Blotter = lazy(() => import("../screens/Blotter"));
const RiskCenter = lazy(() => import("../screens/RiskCenter"));
const AIDesk = lazy(() => import("../screens/AIDesk"));
const Experiment = lazy(() => import("../screens/Experiment"));
const Research = lazy(() => import("../screens/Research"));
const Market = lazy(() => import("../screens/Market"));
const System = lazy(() => import("../screens/System"));

export interface Screen {
  path: string;
  label: string;
  icon: string;
  key: string; // single-key shortcut (off by default)
  /** The nav section it sits in, and the one-line description under its page title. */
  group: string;
  blurb: string;
  element: JSX.Element;
}

/** The sidebar's sections, in display order. Every screen below belongs to exactly one. */
export const NAV_SECTIONS: { name: string; hint: string }[] = [
  { name: "Overview", hint: "Where the books stand right now" },
  { name: "Trading", hint: "Signals, orders, positions and the controls that stop them" },
  { name: "Research", hint: "Candidates, forecasts and what the market is doing" },
  { name: "System", hint: "Engine health, feeds and configuration" },
];

/**
 * The screens, in single-key-shortcut order (1-9). The order here is the shortcut order and the
 * command palette's order; the sidebar renders them grouped by `NAV_SECTIONS`, so regrouping a
 * screen never silently rebinds its key.
 */
export const SCREENS: Screen[] = [
  {
    path: "/",
    icon: "command",
    label: "Command",
    key: "1",
    group: "Overview",
    blurb: "How the books stand right now: equity, risk headroom, open positions, today's signals and alerts.",
    element: <CommandCenter />,
  },
  {
    path: "/decisions",
    icon: "decisions",
    label: "Decisions",
    key: "2",
    group: "Trading",
    blurb: "Every signal and what each book did with it. Open a row for the full lineage of that decision.",
    element: <Decisions />,
  },
  {
    path: "/blotter",
    icon: "blotter",
    label: "Blotter",
    key: "3",
    group: "Trading",
    blurb: "Orders, fills, positions, closed trades and rejections, with shared filters and CSV export.",
    element: <Blotter />,
  },
  {
    path: "/risk",
    icon: "risk",
    label: "Risk",
    key: "4",
    group: "Trading",
    blurb: "Limit utilisation, kill switches and the HALT / RESUME / FLATTEN controls, sector exposure and equity against its peak.",
    element: <RiskCenter />,
  },
  {
    path: "/ai",
    icon: "ai",
    label: "AI desk",
    key: "5",
    group: "Research",
    blurb: "Which model plays which role, what it costs, how the decision models are calibrated, and whether the advisors earn their keep.",
    element: <AIDesk />,
  },
  {
    path: "/experiment",
    icon: "experiment",
    label: "Experiment",
    key: "6",
    group: "Trading",
    blurb: "The paired books side by side against the pre-registration the experiment is judged on.",
    element: <Experiment />,
  },
  {
    path: "/research",
    icon: "spark",
    label: "Research",
    key: "7",
    group: "Research",
    blurb:
      "Event-aware candidates with probabilistic forecasts, the evidence and conditions behind each, and how well past forecasts calibrated. Decision support only: it never places an order.",
    element: <Research />,
  },
  {
    path: "/market",
    icon: "market",
    label: "Market",
    key: "8",
    group: "Research",
    blurb: "Research one symbol: quote, daily history, indicators over real OHLCV, and the system's own entries, exits and announcements.",
    element: <Market />,
  },
  {
    path: "/system",
    icon: "system",
    label: "System",
    key: "9",
    group: "System",
    blurb: "Engine health, feed freshness, reconciliation, the read-only configuration, logs and versions.",
    element: <System />,
  },
];

export const NAV_GROUPS = NAV_SECTIONS.map((s) => s.name);

/** The screens bucketed into sidebar sections, in `NAV_SECTIONS` order. */
export function groupedScreens(): { name: string; hint: string; screens: Screen[] }[] {
  return NAV_SECTIONS.map((section) => ({
    ...section,
    screens: SCREENS.filter((s) => s.group === section.name),
  })).filter((section) => section.screens.length > 0);
}

export const INSPECTOR = { path: "/decisions/:id", element: <DecisionInspector /> };
