# RakshaQuant console (frontend v2)

The browser terminal for the v2 engine: nine screens over the web API (`src/web`), built to the
design spec in `docs/plan/2026-10-01-platform-v2-plan.md` §6. It only *formats* server
projections: it never computes money, and nothing is fabricated outside the demo.

React 18 + Vite + strict TypeScript, TanStack Query (REST projections) and a zustand store fed by
the WebSocket event stream, TanStack Table + Virtual, Radix primitives, cmdk, lightweight-charts
(lazy-loaded), IBM Plex. Screenshots of every screen: `docs/ui/screens/`.

## Layout

A grouped **sidebar** (`src/shell/Nav.tsx`) on desktop, collapsing to an icon rail; below `lg` it
becomes an off-canvas sheet opened from the top bar. Sections are declared once, in
`NAV_SECTIONS` in `src/app/routes.tsx`:

| Section | Screens |
| --- | --- |
| Overview | Command |
| Trading | Decisions, Blotter, Risk, Experiment |
| Research | AI desk, Research, Market |
| System | System |

The top bar carries what must never be buried: session state and clock, data freshness, broker
reconciliation, each book's kill switch, today's AI spend, and **HALT**. The sidebar holds
navigation only - no safety control lives in it, so collapsing or closing the sidebar can never
put HALT out of reach. Single-key shortcuts (off by default) keep their original 1-9 bindings
regardless of which section a screen sits in.

Dense detail lives in `ui/Collapsible.tsx` (summary first, body on demand), and every query goes
through `ui/QueryState.tsx` so loading, error, empty and stale all look - and read - the same on
every screen.

## Requirements

- **Node `^20.19` or `>=22.12`** (Vite 8). `frontend/.npmrc` pins the https npm registry.
- The backend's `web` extra: `uv sync --extra web` (from the repo root).

## Run

```bash
# production build, served by the backend at http://127.0.0.1:8000
npm ci && npm run build
cd .. && uv run python scripts/run_live_trading.py --mode web            # paper session
cd .. && uv run python scripts/run_live_trading.py --mode web --demo     # the demo tape
```

The server prints a one-time link ending in `#token=…`: open that link. The console keeps the
token for the tab and removes it from the address bar; a restart issues a new token.

For development, start the backend with `--dev` (it then accepts the Vite origin) and run
`npm run dev` (`http://localhost:5173`, proxying `/api` and `/ws`); open it with the printed
`#token=…` fragment appended.

## Check

```bash
npm run typecheck     # the app (browser types) and the tests/tooling (Node types)
npm run lint          # ESLint with react-hooks
npm test              # Vitest: formatters, components, the stream reducer, the §6.2 design rules
npm run build && npm run check:bundle   # initial-route JS must stay under 250 KB gzipped
npm run e2e           # Playwright against the real server replaying the demo tape
```

`npm run e2e` starts `run_live_trading.py --mode web --demo` itself, waits for the demo session to
finish, and runs every screen, the Decision Inspector lineage, HALT/RESUME, auth, an axe scan and
a 5,000-row scrolling test. Without a downloaded browser, `PW_CHANNEL=msedge npm run e2e` uses
the installed Edge. `RQ_SCREENSHOTS=1` writes the screenshots to `docs/ui/screens/`.

## The API contract

`src/api/types.gen.ts` is generated from the backend's OpenAPI document; never edit it:

```bash
cd .. && uv run --extra web python scripts/export_openapi.py && cd frontend && npm run gen:api
```

CI regenerates both and fails on any difference.
