import type { Config } from "tailwindcss";

// The design tokens as semantic names over the CSS variables in index.css. Surfaces are a
// four-step ramp (bg-0 page → bg-3 hover), the accent is emerald, and every state colour has
// a matching `-soft` tint so a badge can be a filled pill instead of an outlined stamp.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      bg: {
        0: "var(--bg-0)",
        1: "var(--bg-1)",
        2: "var(--bg-2)",
        3: "var(--bg-3)",
      },
      line: { DEFAULT: "var(--border)", strong: "var(--border-strong)" },
      fg: { 0: "var(--text-0)", 1: "var(--text-1)", 2: "var(--text-2)" },
      accent: {
        DEFAULT: "var(--accent)",
        hover: "var(--accent-hover)",
        fg: "var(--accent-fg)",
        soft: "var(--accent-soft)",
        line: "var(--accent-line)",
      },
      up: { DEFAULT: "var(--up)", soft: "var(--up-soft)" },
      down: { DEFAULT: "var(--down)", soft: "var(--down-soft)" },
      warn: { DEFAULT: "var(--warn)", soft: "var(--warn-soft)" },
      crit: { bg: "var(--crit-bg)", fg: "var(--crit-fg)", soft: "var(--down-soft)" },
      overlay: "var(--overlay)", // behind dialogs and the mobile navigation sheet
    },
    borderRadius: {
      none: "0",
      sm: "var(--radius-sm)",
      DEFAULT: "var(--radius)",
      md: "var(--radius)",
      lg: "var(--radius-lg)",
      xl: "var(--radius-xl)",
      full: "var(--radius-pill)",
    },
    boxShadow: {
      none: "none",
      card: "var(--shadow-card)",
      raised: "var(--shadow-raised)",
      popover: "var(--shadow-popover)",
    },
    fontFamily: { sans: "var(--font-ui)", mono: "var(--font-mono)" },
    fontSize: {
      "2xs": ["11px", { lineHeight: "16px" }], // labels, badges
      xs: ["12px", { lineHeight: "18px" }], // dense tables
      sm: ["13px", { lineHeight: "20px" }], // secondary UI
      base: ["14px", { lineHeight: "22px" }], // base UI
      lg: ["16px", { lineHeight: "24px" }], // card titles
      xl: ["18px", { lineHeight: "26px" }], // page titles
      "2xl": ["22px", { lineHeight: "28px" }], // stat values
      "3xl": ["28px", { lineHeight: "34px" }], // hero values (a quote)
      "4xl": ["34px", { lineHeight: "40px" }], // the ceiling
    },
    letterSpacing: { tight: "-0.015em", wide: "0.06em" },
    extend: {
      transitionDuration: { DEFAULT: "150ms" },
      maxWidth: { screen: "100vw" },
      spacing: { row: "40px", gutter: "16px" },
      keyframes: {
        "fade-in": { from: { opacity: "0" }, to: { opacity: "1" } },
        "rise-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 150ms ease-out",
        "rise-in": "rise-in 180ms ease-out",
      },
    },
  },
  plugins: [],
} satisfies Config;
