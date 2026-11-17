// The visual system's guardrails, checked on every test run rather than by eye.
// The console was redesigned from the dense terminal look to a calm research app: cards with
// real radii and one soft shadow, an emerald accent, and colour that is always paired with
// text. These are the patterns that would undo that.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const ROOT = import.meta.dirname;

const BANNED: [string, RegExp][] = [
  ["gradients", /gradient/i],
  ["glass / blur", /backdrop-blur|backdrop-filter/],
  ["shadows outside the three tokens", /shadow-(sm|md|lg|xl|2xl|inner)\b/],
  ["off-scale radii", /rounded-\[/],
  ["bouncy motion", /animate-(bounce|ping|spin|pulse)\b/],
  ["emoji", /\p{Extended_Pictographic}/u],
  ["'AI-powered' copy", /ai[- ]powered|✨/i],
  ["purple/indigo palettes", /\b(purple|indigo|violet|fuchsia)-\d/],
];

/** Colours come from the tokens in index.css, never from a literal in a component. */
const HEX: [string, RegExp][] = [["hard-coded colours", /#[0-9a-fA-F]{3,8}\b/]];

function sources(dir: string): string[] {
  return readdirSync(dir).flatMap((name) => {
    const path = join(dir, name);
    if (statSync(path).isDirectory()) return sources(path);
    const own = /\.(tsx?|css)$/.test(name) && !/\.test\.tsx?$/.test(name) && !name.endsWith(".gen.ts");
    return own ? [path] : [];
  });
}

describe("design rules", () => {
  const files = sources(ROOT);
  const components = files.filter((f) => f.endsWith(".tsx"));

  it("finds the sources", () => {
    expect(files.length).toBeGreaterThan(10);
  });

  it.each(BANNED)("no %s anywhere", (_, pattern) => {
    const offenders = files.filter((file) => pattern.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });

  it.each(HEX)("no %s in a component", (_, pattern) => {
    const offenders = components.filter((file) => pattern.test(readFileSync(file, "utf8")));
    expect(offenders).toEqual([]);
  });

  it("keeps the tokens the theme reads", () => {
    const css = readFileSync(join(ROOT, "index.css"), "utf8");
    for (const token of [
      "--bg-0",
      "--bg-1",
      "--bg-2",
      "--bg-3",
      "--border",
      "--border-strong",
      "--text-0",
      "--text-1",
      "--text-2",
      "--accent",
      "--accent-fg",
      "--up",
      "--down",
      "--warn",
      "--crit-bg",
      "--crit-fg",
      "--radius-lg",
      "--shadow-card",
    ]) {
      expect(css, token).toContain(`${token}:`);
    }
  });
});
