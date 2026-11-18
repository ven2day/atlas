// Grouped sidebar navigation. On a desktop it is a persistent column that collapses to an icon
// rail; below `lg` it becomes an off-canvas sheet opened from the top bar.
//
// Two safety properties are deliberate:
//   * the sheet closes on navigation and on Escape, so it can never trap the operator;
//   * HALT is not in here. It stays in the top bar on every screen at every width, because a
//     control that stops trading must never be one tap deeper than the screen you are on.

import { useEffect } from "react";
import { NavLink, useLocation } from "react-router";

import { groupedScreens, type Screen } from "../app/routes";
import { cx } from "../lib/cx";
import { useSettings } from "../lib/settings";
import { useUi } from "../lib/ui";
import { Icon } from "./icons";

const SECTIONS = groupedScreens();

function NavItem({
  screen,
  collapsed,
  shortcuts,
  onNavigate,
}: {
  screen: Screen;
  collapsed: boolean;
  shortcuts: boolean;
  onNavigate?: () => void;
}) {
  return (
    <NavLink
      to={screen.path}
      end={screen.path === "/"}
      onClick={onNavigate}
      title={collapsed ? `${screen.label} — ${screen.blurb}` : screen.blurb}
      aria-label={collapsed ? screen.label : undefined}
      className={({ isActive }) =>
        cx(
          "group relative flex min-h-9 items-center gap-2.5 rounded-md text-sm font-medium transition-colors duration-150",
          collapsed ? "justify-center px-0 py-2" : "px-2.5 py-2",
          isActive ? "bg-bg-3 text-fg-0" : "text-fg-2 hover:bg-bg-2 hover:text-fg-0",
        )
      }
    >
      {({ isActive }) => (
        <>
          {isActive && (
            <span
              aria-hidden="true"
              className={cx(
                "absolute inset-y-1.5 w-0.5 rounded-full bg-accent",
                collapsed ? "-left-px" : "left-0",
              )}
            />
          )}
          <Icon
            name={screen.icon}
            size={17}
            className={cx("shrink-0", isActive ? "text-accent-fg" : "text-fg-2 group-hover:text-fg-1")}
          />
          {!collapsed && <span className="min-w-0 flex-1 truncate">{screen.label}</span>}
          {!collapsed && shortcuts && (
            <kbd className="num shrink-0 rounded-sm border border-line-strong px-1 text-[10px] text-fg-2">
              {screen.key}
            </kbd>
          )}
        </>
      )}
    </NavLink>
  );
}

function NavList({
  collapsed,
  onNavigate,
}: {
  collapsed: boolean;
  onNavigate?: () => void;
}) {
  const shortcuts = useSettings((s) => s.shortcuts);
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto px-2.5 py-3">
      {SECTIONS.map((section) => (
        <section key={section.name} aria-label={section.name} className="flex flex-col gap-0.5">
          {!collapsed && (
            <h2 className="rq-label px-2.5 pb-1.5" title={section.hint}>
              {section.name}
            </h2>
          )}
          {collapsed && (
            <span
              aria-hidden="true"
              className="mx-auto mb-1.5 h-px w-6 bg-line first:hidden"
              title={section.name}
            />
          )}
          {section.screens.map((screen) => (
            <NavItem
              key={screen.path}
              screen={screen}
              collapsed={collapsed}
              shortcuts={shortcuts}
              onNavigate={onNavigate}
            />
          ))}
        </section>
      ))}
    </div>
  );
}

function NavFooter({ collapsed }: { collapsed: boolean }) {
  const { navCollapsed, setNavCollapsed } = useSettings();
  const setPalette = useUi((s) => s.setPalette);
  return (
    <div className="flex shrink-0 flex-col gap-1 border-t border-line px-2.5 py-2.5">
      <button
        type="button"
        onClick={() => setPalette(true)}
        title="Search or jump to a screen (Ctrl/Cmd+K)"
        className={cx(
          "flex min-h-9 items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-fg-2 transition-colors duration-150 hover:bg-bg-2 hover:text-fg-0",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon name="search" size={17} className="shrink-0" />
        {!collapsed && (
          <>
            <span className="min-w-0 flex-1 truncate text-left">Search</span>
            <kbd className="num shrink-0 rounded-sm border border-line-strong px-1 text-[10px]">Ctrl K</kbd>
          </>
        )}
      </button>
      <button
        type="button"
        onClick={() => setNavCollapsed(!navCollapsed)}
        aria-pressed={navCollapsed}
        title={navCollapsed ? "Expand navigation" : "Collapse navigation to icons"}
        className={cx(
          "hidden min-h-8 items-center gap-2.5 rounded-md px-2.5 py-1.5 text-xs text-fg-2 transition-colors duration-150 hover:bg-bg-2 hover:text-fg-0 lg:flex",
          collapsed && "justify-center px-0",
        )}
      >
        <Icon name={navCollapsed ? "chevronRight" : "panelLeft"} size={16} className="shrink-0" />
        {!navCollapsed && <span className="truncate">Collapse</span>}
      </button>
    </div>
  );
}

export function Nav() {
  const collapsed = useSettings((s) => s.navCollapsed);
  const { navOpen, setNavOpen } = useUi();
  const location = useLocation();

  // Navigating closes the sheet; so does Escape. Neither depends on the user finding a close box.
  useEffect(() => {
    setNavOpen(false);
  }, [location.pathname, setNavOpen]);
  useEffect(() => {
    if (!navOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setNavOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [navOpen, setNavOpen]);

  return (
    <>
      <aside
        aria-label="Screens"
        className={cx(
          "z-30 hidden shrink-0 flex-col border-r border-line bg-bg-1 transition-[width] duration-150 lg:flex",
          collapsed ? "w-[64px]" : "w-60",
        )}
      >
        <NavList collapsed={collapsed} />
        <NavFooter collapsed={collapsed} />
      </aside>

      {navOpen && (
        <div className="fixed inset-0 z-50 lg:hidden">
          <button
            type="button"
            aria-label="Close navigation"
            onClick={() => setNavOpen(false)}
            className="absolute inset-0 animate-fade-in bg-overlay"
          />
          <div
            role="dialog"
            aria-modal="true"
            aria-label="Screens"
            className="absolute inset-y-0 left-0 flex w-64 max-w-[85vw] animate-rise-in flex-col border-r border-line bg-bg-1 shadow-raised"
          >
            <div className="flex shrink-0 items-center justify-between border-b border-line px-3 py-2.5">
              <span className="text-sm font-semibold tracking-tight text-fg-0">Navigate</span>
              <button
                type="button"
                onClick={() => setNavOpen(false)}
                className="flex h-8 w-8 items-center justify-center rounded-md text-fg-2 transition-colors duration-150 hover:bg-bg-2 hover:text-fg-0"
                aria-label="Close navigation"
              >
                <Icon name="close" size={16} />
              </button>
            </div>
            <NavList collapsed={false} onNavigate={() => setNavOpen(false)} />
            <NavFooter collapsed={false} />
          </div>
        </div>
      )}
    </>
  );
}
