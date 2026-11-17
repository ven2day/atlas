import { Fragment } from "react";
import { NavLink } from "react-router";

import { SCREENS } from "../app/routes";
import { cx } from "../lib/cx";
import { useSettings } from "../lib/settings";
import { useUi } from "../lib/ui";
import { Icon } from "./icons";

/** Horizontal, grouped primary navigation. It scrolls sideways rather than hiding on a phone,
 *  so every screen stays one swipe away and no safety control is ever off-screen. */
export function Nav() {
  const shortcuts = useSettings((s) => s.shortcuts);
  const setPalette = useUi((s) => s.setPalette);

  return (
    <nav aria-label="Screens" className="z-20 shrink-0 border-b border-line bg-bg-1">
      <div className="flex items-stretch gap-1 px-3 sm:px-5">
        <div className="flex min-w-0 flex-1 items-stretch gap-1 overflow-x-auto">
          {SCREENS.map((s, i) => (
            <Fragment key={s.path}>
              {i > 0 && s.group !== SCREENS[i - 1]!.group && (
                <span aria-hidden="true" className="my-3 w-px shrink-0 bg-line" />
              )}
              <NavLink
                to={s.path}
                end={s.path === "/"}
                title={s.blurb}
                className={({ isActive }) =>
                  cx(
                    "relative flex h-11 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium transition-colors duration-150",
                    isActive ? "bg-bg-2 text-fg-0" : "text-fg-2 hover:bg-bg-2 hover:text-fg-0",
                  )
                }
              >
                {({ isActive }) => (
                  <>
                    <Icon name={s.icon} size={17} className={isActive ? "text-accent-fg" : undefined} />
                    <span className="whitespace-nowrap">{s.label}</span>
                    {shortcuts && <kbd className="num rounded-sm border border-line-strong px-1 text-[10px] text-fg-2">{s.key}</kbd>}
                    {isActive && (
                      <span aria-hidden="true" className="absolute inset-x-2.5 -bottom-px h-0.5 rounded-full bg-accent" />
                    )}
                  </>
                )}
              </NavLink>
            </Fragment>
          ))}
        </div>
        <button
          type="button"
          onClick={() => setPalette(true)}
          className="ml-2 hidden h-11 shrink-0 items-center gap-2 rounded-md border border-line bg-bg-2 px-3 text-sm text-fg-2 transition-colors duration-150 hover:border-line-strong hover:text-fg-0 md:flex"
        >
          <Icon name="search" size={15} />
          <span className="hidden lg:inline">Search or jump to…</span>
          <kbd className="num rounded-sm border border-line-strong px-1.5 py-0.5 text-[10px] text-fg-2">Ctrl K</kbd>
        </button>
      </div>
    </nav>
  );
}
