// Ctrl/⌘+K: go to a screen, open a symbol or a decision by id, halt, toggle the preferences.

import { Command } from "cmdk";
import { useState } from "react";
import { useNavigate } from "react-router";

import { SCREENS } from "../app/routes";
import { useSettings } from "../lib/settings";
import { useUi } from "../lib/ui";
import { Icon } from "./icons";

const ID = /^[A-Za-z0-9_-]{8,64}$/;
const SYMBOL = /^[A-Z0-9&_.-]{1,20}$/;

const item =
  "flex h-10 cursor-pointer items-center gap-2.5 rounded-md px-2.5 text-sm text-fg-1 " +
  "data-[selected=true]:bg-bg-3 data-[selected=true]:text-fg-0";
const group =
  "px-1.5 py-1.5 text-2xs text-fg-2 [&_[cmdk-group-heading]]:px-2.5 [&_[cmdk-group-heading]]:py-1.5 " +
  "[&_[cmdk-group-heading]]:text-2xs [&_[cmdk-group-heading]]:font-medium [&_[cmdk-group-heading]]:uppercase " +
  "[&_[cmdk-group-heading]]:tracking-wide [&_[cmdk-group-heading]]:text-fg-2";

export function CommandPalette() {
  const { palette, setPalette, setHalt } = useUi();
  const { shortcuts, toggleShortcuts, density, setDensity } = useSettings();
  const navigate = useNavigate();
  const [query, setQuery] = useState("");
  const text = query.trim();

  const run = (action: () => void) => {
    setPalette(false);
    setQuery("");
    action();
  };

  return (
    <Command.Dialog
      open={palette}
      onOpenChange={(open) => {
        setPalette(open);
        if (!open) setQuery("");
      }}
      label="Command palette"
      overlayClassName="fixed inset-0 z-40 bg-overlay"
      contentClassName="fixed left-1/2 top-[12%] z-50 w-[620px] max-w-[92vw] -translate-x-1/2 overflow-hidden rounded-xl border border-line-strong bg-bg-1 shadow-popover"
    >
      <div className="flex items-center gap-2.5 border-b border-line px-4">
        <Icon name="search" size={16} className="shrink-0 text-fg-2" />
        <Command.Input
          value={query}
          onValueChange={setQuery}
          placeholder="Go to a screen, a symbol (INFY) or a decision id…"
          className="h-12 w-full bg-transparent text-sm text-fg-0 outline-none placeholder:text-fg-2"
        />
      </div>
      <Command.List className="max-h-[420px] overflow-auto p-2">
        <Command.Empty className="px-3 py-6 text-center text-sm text-fg-2">
          No match for “{text}”.
        </Command.Empty>
        {text && (ID.test(text) || SYMBOL.test(text)) && (
          <Command.Group heading="Open" className={group}>
            {ID.test(text) && (
              <Command.Item value={`decision ${text}`} onSelect={() => run(() => navigate(`/decisions/${text}`))} className={item}>
                <Icon name="decisions" size={16} className="text-fg-2" />
                Decision <span className="truncate font-mono text-xs text-fg-2">{text}</span>
              </Command.Item>
            )}
            {SYMBOL.test(text.toUpperCase()) && (
              <Command.Item
                value={`symbol ${text}`}
                onSelect={() => run(() => navigate(`/market?symbol=${encodeURIComponent(text.toUpperCase())}`))}
                className={item}
              >
                <Icon name="market" size={16} className="text-fg-2" />
                Research <span className="font-mono text-xs">{text.toUpperCase()}</span>
              </Command.Item>
            )}
          </Command.Group>
        )}
        <Command.Group heading="Go to" className={group}>
          {SCREENS.map((s) => (
            <Command.Item key={s.path} value={`go ${s.label}`} onSelect={() => run(() => navigate(s.path))} className={item}>
              <Icon name={s.icon} size={16} className="text-fg-2" />
              <span className="flex-1 truncate">{s.label}</span>
              {shortcuts && <kbd className="num rounded-sm border border-line-strong px-1 text-[10px] text-fg-2">{s.key}</kbd>}
            </Command.Item>
          ))}
        </Command.Group>
        <Command.Group heading="Actions" className={group}>
          <Command.Item value="halt all books" onSelect={() => run(() => setHalt(true))} className={item}>
            <Icon name="stop" size={16} className="text-down" />
            Halt all books
          </Command.Item>
          <Command.Item value="toggle single-key shortcuts" onSelect={() => run(toggleShortcuts)} className={item}>
            <Icon name="terminal" size={16} className="text-fg-2" />
            {shortcuts ? "Turn single-key shortcuts off" : "Turn single-key shortcuts on"}
          </Command.Item>
          <Command.Item
            value="toggle table density"
            onSelect={() => run(() => setDensity(density === "compact" ? "comfortable" : "compact"))}
            className={item}
          >
            <Icon name="blotter" size={16} className="text-fg-2" />
            {density === "compact" ? "Use roomy table rows" : "Use compact table rows"}
          </Command.Item>
        </Command.Group>
      </Command.List>
      <div className="flex items-center gap-4 border-t border-line bg-bg-2 px-4 py-2 text-2xs text-fg-2">
        <span><kbd className="rounded-sm border border-line-strong px-1 font-mono">Up</kbd> <kbd className="rounded-sm border border-line-strong px-1 font-mono">Down</kbd> to navigate</span>
        <span><kbd className="rounded-sm border border-line-strong px-1 font-mono">Enter</kbd> to open</span>
        <span><kbd className="rounded-sm border border-line-strong px-1 font-mono">Esc</kbd> to close</span>
      </div>
    </Command.Dialog>
  );
}
