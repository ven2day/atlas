// Line glyphs on a 24 px grid, 1.6 stroke, monochrome: they take the colour of their context.

const PATHS = {
  // navigation
  command: "M4 4h7v6H4zM13 4h7v4h-7zM13 10h7v10h-7zM4 12h7v8H4z",
  decisions:
    "M3.5 6.6 5.3 8.4 8.6 5.1M3.5 16.6l1.8 1.8 3.3-3.3M11.5 6.7h9M11.5 16.7h9M11.5 11.7h6",
  blotter: "M3.5 5h17v14h-17zM3.5 9.6h17M3.5 14.3h17M9.2 5v14",
  risk: "M12 3.2 19.4 6v5.3c0 4.2-3.1 7.5-7.4 8.9-4.3-1.4-7.4-4.7-7.4-8.9V6zM9.2 12.1l2.1 2.1 4.3-4.4",
  ai: "M7.6 7.6h8.8v8.8H7.6zM10.1 4v3.6M13.9 4v3.6M10.1 16.4V20M13.9 16.4V20M4 10.1h3.6M4 13.9h3.6M16.4 10.1H20M16.4 13.9H20",
  experiment: "M9.6 3.6v5.1L5 16.9c-.9 1.6.2 3.5 2 3.5h10c1.8 0 2.9-1.9 2-3.5l-4.6-8.2V3.6M8.2 3.6h7.6M7.4 14.6h9.2",
  market: "M4.2 10h4.9v6H4.2zM14.9 6.2h4.9v8.4h-4.9zM6.6 6.4v3.6M6.6 16v3.4M17.4 3.4v2.8M17.4 14.6v5.9M9.1 13h5.8",
  system: "M4 5h16v5.2H4zM4 13.8h16V19H4zM7.2 7.6h.01M7.2 16.4h.01",
  // controls
  search: "M10.6 4.2a6.4 6.4 0 1 0 0 12.8 6.4 6.4 0 0 0 0-12.8zM15.3 15.3l5 5",
  chevronDown: "M6 9.5l6 6 6-6",
  chevronUp: "M6 14.5l6-6 6 6",
  chevronRight: "M9.5 6l6 6-6 6",
  chevronLeft: "M14.5 6l-6 6 6 6",
  close: "M6 6l12 12M18 6L6 18",
  menu: "M4 7h16M4 12h16M4 17h16",
  download: "M12 4v11M7.6 10.6 12 15l4.4-4.4M4.6 19.4h14.8",
  external: "M14 4.4h5.6V10M19.2 4.8l-8.4 8.4M18 14v5.6H4.4V6H10",
  copy: "M8.8 8.8h10.8v10.8H8.8zM4.4 15.2V4.4H15.2",
  check: "M5 12.6l4.6 4.6L19 6.8",
  alert: "M12 4.2l8.9 15.4H3.1zM12 9.8v4.1M12 16.6v.9",
  info: "M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8zM12 11v5.4M12 7.9v.9",
  sliders: "M4 7.2h9.6M18.4 7.2H20M4 16.8h3.6M12.4 16.8H20M15.8 4.6v5.2M9.8 14.2v5.2",
  refresh: "M19.6 12a7.6 7.6 0 1 1-2.3-5.4M19.8 4.2v4.6h-4.6",
  arrowUpRight: "M7 17L17 7M9.2 7H17v7.8",
  arrowDownRight: "M7 7l10 10M17 9.2V17H9.2",
  arrowUp: "M12 19V5M6.4 10.6 12 5l5.6 5.6",
  arrowDown: "M12 5v14M6.4 13.4 12 19l5.6-5.6",
  plus: "M12 5v14M5 12h14",
  minus: "M5 12h14",
  filter: "M4 6h16l-6.2 7.2v5.4l-3.6-2v-3.4z",
  calendar: "M4.6 6.2h14.8v13.6H4.6zM4.6 10.4h14.8M8.6 3.8v4.4M15.4 3.8v4.4",
  clock: "M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8zM12 7.6V12l3.2 2.1",
  link: "M10.2 13.8a3.8 3.8 0 0 0 5.5.3l2.6-2.6a3.8 3.8 0 0 0-5.4-5.4l-1.2 1.2M13.8 10.2a3.8 3.8 0 0 0-5.5-.3l-2.6 2.6a3.8 3.8 0 0 0 5.4 5.4l1.2-1.2",
  layers: "M12 3.6 3.6 8 12 12.4 20.4 8zM3.6 12.4 12 16.8l8.4-4.4M3.6 16.6 12 21l8.4-4.4",
  trending: "M3.6 16.6 9 11.2l3.4 3.4 7-7.4M14.4 7.2h5.2v5.2",
  gauge: "M4.2 17.4a8.4 8.4 0 1 1 15.6 0M12 17.4l4.2-5.4",
  pause: "M9.4 5v14M14.6 5v14",
  play: "M7.6 4.8 19 12 7.6 19.2z",
  stop: "M8.2 3.6h7.6L20.4 8.2v7.6l-4.6 4.6H8.2L3.6 15.8V8.2zM12 8.2v4.4M12 15.4v.8",
  database:
    "M12 3.6c4.4 0 8 1.3 8 3s-3.6 3-8 3-8-1.3-8-3 3.6-3 8-3zM4 6.6v10.8c0 1.7 3.6 3 8 3s8-1.3 8-3V6.6M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3",
  wallet: "M3.6 7.6h13.8a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H3.6zM3.6 7.6V6.2a1.6 1.6 0 0 1 1.6-1.6h10.6M16.4 13.2h.01",
  terminal: "M4.4 5h15.2v14H4.4zM8 9.6l2.2 2.4L8 14.4M12.6 14.6h4",
  lineChart: "M3.6 16.8 8.4 11l3.4 3.4 5-7.2M13.6 7.2h4.8V12",
  candleChart: "M4.4 10.2h4.6v5.6H4.4zM15 6.6h4.6v8H15zM6.7 6.6v3.6M6.7 15.8v3M17.3 3.6v3M17.3 14.6v4.4",
  volume: "M4 15.6h3.2V20H4zM9.6 11.2h3.2V20H9.6zM15.2 6.4h3.2V20h-3.2z",
  book: "M5 4.6h8.6a2.8 2.8 0 0 1 2.8 2.8v12H7.8a2.8 2.8 0 0 1-2.8-2.8zM16.4 7.4h2.6v12h-2.6",
  history: "M4.2 12a7.8 7.8 0 1 0 2.4-5.6M4.2 4.6v4.8h4.8M12 8.4V12l2.8 1.8",
  eye: "M2.8 12S6.6 6.2 12 6.2 21.2 12 21.2 12 17.4 17.8 12 17.8 2.8 12 2.8 12zM12 9.6a2.4 2.4 0 1 0 0 4.8 2.4 2.4 0 0 0 0-4.8z",
  dot: "M12 8.4a3.6 3.6 0 1 0 0 7.2 3.6 3.6 0 0 0 0-7.2z",
  panelLeft: "M3.6 4.8h16.8v14.4H3.6zM9.6 4.8v14.4",
  xCircle: "M12 3.6a8.4 8.4 0 1 0 0 16.8 8.4 8.4 0 0 0 0-16.8zM9.4 9.4l5.2 5.2M14.6 9.4l-5.2 5.2",
  spark: "M12 3.6l1.9 5.1 5.1 1.9-5.1 1.9-1.9 5.1-1.9-5.1-5.1-1.9 5.1-1.9zM18.4 16.2l.7 1.9 1.9.7-1.9.7-.7 1.9-.7-1.9-1.9-.7 1.9-.7z",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({
  name,
  size = 20,
  className,
  strokeWidth = 1.6,
}: {
  name: IconName | string;
  size?: number;
  className?: string;
  strokeWidth?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      className={className}
      style={{ flexShrink: 0 }}
    >
      <path
        d={PATHS[name as IconName] ?? ""}
        stroke="currentColor"
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
