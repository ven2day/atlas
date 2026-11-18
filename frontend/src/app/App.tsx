// The application shell (§6.5): top bar, navigation, the workspace, status bar, the command
// palette and toasts; it owns the event stream and turns its events into fresh projections.

import { useQueryClient } from "@tanstack/react-query";
import { Suspense, useEffect, useRef } from "react";
import { Outlet, useNavigate } from "react-router";

import { invalidator } from "../api/queries";
import { useSettings } from "../lib/settings";
import { StreamClient, useStream } from "../lib/stream";
import { useUi } from "../lib/ui";
import { DemoBanner, Unauthorized, useUnauthorized } from "../shell/Banners";
import { CommandPalette } from "../shell/CommandPalette";
import { Nav } from "../shell/Nav";
import { StatusBar } from "../shell/StatusBar";
import { TopBar } from "../shell/TopBar";
import { Toaster, useToasts } from "../ui/Toast";
import { SCREENS } from "./routes";

function useEventStream() {
  const client = useQueryClient();
  useEffect(() => {
    const stream = new StreamClient(invalidator(client));
    stream.start();
    return () => stream.stop();
  }, [client]);
}

/** Warnings and critical alerts from the stream become toasts (aria-live). */
function useAlertToasts() {
  const push = useToasts((s) => s.push);
  const seen = useRef(0);
  const events = useStream((s) => s.events);
  const notices = useStream((s) => s.notices);
  const noticed = useRef(0);
  useEffect(() => {
    for (const e of events) {
      if ((e.seq ?? 0) <= seen.current) continue;
      seen.current = e.seq ?? seen.current;
      const data = e.data as Record<string, unknown>;
      if (e.type === "Alert" && data.level !== "INFO") {
        push({ level: data.level === "CRITICAL" ? "crit" : "warn", title: String(data.key), body: String(data.message) });
      } else if (e.type === "KillSwitchChanged") {
        push({
          level: data.current === "FLATTEN" ? "crit" : "warn",
          title: `Book ${e.book_id}: ${String(data.previous)} → ${String(data.current)}`,
          body: `${String(data.reason)} (${String(data.actor)})`,
        });
      }
    }
  }, [events, push]);
  useEffect(() => {
    for (const n of notices.slice(noticed.current)) {
      const message = (n.data as { message?: string }).message;
      push({ level: n.type === "error" ? "crit" : "info", title: n.type === "error" ? "Run error" : "Run stopped", body: message });
    }
    noticed.current = notices.length;
  }, [notices, push]);
}

function useKeyboard() {
  const navigate = useNavigate();
  const shortcuts = useSettings((s) => s.shortcuts);
  const { palette, setPalette } = useUi();
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPalette(!palette);
        return;
      }
      const target = e.target as HTMLElement | null;
      const typing = target?.closest("input, textarea, select, [contenteditable=true], [role=dialog]");
      if (!shortcuts || typing || e.ctrlKey || e.metaKey || e.altKey) return;
      const screen = SCREENS.find((s) => s.key === e.key);
      if (screen) navigate(screen.path);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navigate, shortcuts, palette, setPalette]);
}

export default function App() {
  useEventStream();
  useAlertToasts();
  useKeyboard();
  if (useUnauthorized()) return <Unauthorized />;
  return (
    <div className="flex h-full bg-bg-0 text-fg-0">
      <Nav />
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        <DemoBanner />
        <TopBar />
        <main id="workspace" className="min-h-0 min-w-0 flex-1 overflow-auto">
          <Suspense fallback={<ScreenFallback />}>
            <Outlet />
          </Suspense>
        </main>
        <StatusBar />
      </div>
      <CommandPalette />
      <Toaster />
    </div>
  );
}

/** Shown while a screen's chunk loads: the shape of a page, not a blank frame. */
function ScreenFallback() {
  return (
    <div className="flex flex-col gap-5 p-4 sm:p-6" aria-busy="true" aria-label="Loading screen">
      <div className="flex flex-col gap-2">
        <span className="rq-skeleton h-6 w-48" />
        <span className="rq-skeleton h-3.5 w-80 max-w-full" />
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }, (_, i) => (
          <span key={i} className="rq-skeleton h-20" />
        ))}
      </div>
      <span className="rq-skeleton h-72 w-full" />
    </div>
  );
}
