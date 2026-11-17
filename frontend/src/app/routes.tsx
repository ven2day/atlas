import { lazy } from "react";

// Every screen is its own chunk: the initial route stays inside the 250 KB budget.
const CommandCenter = lazy(() => import("../screens/CommandCenter"));
const DecisionInspector = lazy(() => import("../screens/DecisionInspector"));
const Decisions = lazy(() => import("../screens/Decisions"));
const Blotter = lazy(() => import("../screens/Blotter"));
const RiskCenter = lazy(() => import("../screens/RiskCenter"));
const AIDesk = lazy(() => import("../screens/AIDesk"));
const Experiment = lazy(() => import("../screens/Experiment"));
const Market = lazy(() => import("../screens/Market"));
const System = lazy(() => import("../screens/System"));

export interface Screen {
  path: string;
  label: string;
  icon: string;
  key: string; // single-key shortcut (off by default)
  /** The nav group it sits in, and the one-line description under its page title. */
  group: string;
  blurb: string;
  element: JSX.Element;
}

export const SCREENS: Screen[] = [
  {
    path: "/",
    icon: "command",
    label: "Command",
    key: "1",
    group: "Desk",
    blurb: "How the books stand right now: equity, risk headroom, open positions, today's signals and alerts.",
    element: <CommandCenter />,
  },
  {
    path: "/decisions",
    icon: "decisions",
    label: "Decisions",
    key: "2",
    group: "Desk",
    blurb: "Every signal and what each book did with it. Open a row for the full lineage of that decision.",
    element: <Decisions />,
  },
  {
    path: "/blotter",
    icon: "blotter",
    label: "Blotter",
    key: "3",
    group: "Desk",
    blurb: "Orders, fills, positions, closed trades and rejections, with shared filters and CSV export.",
    element: <Blotter />,
  },
  {
    path: "/risk",
    icon: "risk",
    label: "Risk",
    key: "4",
    group: "Controls",
    blurb: "Limit utilisation, kill switches and the HALT / RESUME / FLATTEN controls, sector exposure and equity against its peak.",
    element: <RiskCenter />,
  },
  {
    path: "/ai",
    icon: "ai",
    label: "AI desk",
    key: "5",
    group: "Controls",
    blurb: "Which model plays which role, what it costs, how the decision models are calibrated, and whether the advisors earn their keep.",
    element: <AIDesk />,
  },
  {
    path: "/experiment",
    icon: "experiment",
    label: "Experiment",
    key: "6",
    group: "Controls",
    blurb: "The paired books side by side against the pre-registration the experiment is judged on.",
    element: <Experiment />,
  },
  {
    path: "/market",
    icon: "market",
    label: "Market",
    key: "7",
    group: "Research",
    blurb: "Research one symbol: quote, daily history, indicators over real OHLCV, and the system's own entries, exits and announcements.",
    element: <Market />,
  },
  {
    path: "/system",
    icon: "system",
    label: "System",
    key: "8",
    group: "Research",
    blurb: "Engine health, feed freshness, reconciliation, the read-only configuration, logs and versions.",
    element: <System />,
  },
];

export const NAV_GROUPS = [...new Set(SCREENS.map((s) => s.group))];

export const INSPECTOR = { path: "/decisions/:id", element: <DecisionInspector /> };
