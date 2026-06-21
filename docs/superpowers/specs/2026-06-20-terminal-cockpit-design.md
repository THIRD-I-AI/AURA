# AURA Terminal Cockpit — Phase 1

> **Sprint S46 · issue #120.** A Bloomberg/Palantir-style hybrid cockpit for AURA:
> a dense, keyboard-driven, dockable multi-panel command terminal where panels
> cross-filter. Phase 1 of a multi-phase effort; later phases (multi-dimension
> linking, server-side layouts, multi-monitor popouts, more panels) are described
> at the end and are **not built here**.

**Goal:** A full-viewport command cockpit at `/app/terminal` where a user
arranges live panels (Query, Datasets, Findings, Live Feed), drags/splits/docks
them, saves named layouts, drives everything from a command palette + keyboard,
and sees one real cross-filter ripple across panels — all **additive**, with the
existing AURA app untouched.

## Decisions (settled in brainstorming)

- **Interaction model:** hybrid cockpit — tiled dockable panels **plus**
  cross-filtering (object selection in one panel reacts in others).
- **Placement:** a dedicated **full-viewport** workspace at `/app/terminal`,
  alongside the existing app. The current pages keep working unchanged; the
  cockpit reuses their data services rather than forking logic. This is how we
  meet the hard "no breakage / flows smooth" requirement — the regression surface
  is near-zero because we add a route and compose existing services.
- **Panel engine:** **dockview** (`dockview-react`). Verified current state
  (June 2026): React-19 in its stable peer deps, zero-dependency core, MIT,
  highest-adoption docking lib (~99k weekly downloads), and it natively does
  tabs + splits + drag-dock + floating panels + popout-to-separate-window +
  `api.toJSON()`/`fromJSON()` layout serialization. Popout is the Phase-2
  multi-monitor hook; Phase 1 uses tabs/splits/float + saved layouts.
- **v1 panels (all four):** Query+Results, Datasets & schema, Audit findings/HITL,
  Live monitoring feed — each a thin wrapper over an existing AURA service/hook,
  **no backend change**.

## Favorable starting point (verified)

- Routing: `src/AppRoutes.tsx` has `/app/*` → `ProtectedRoute` → a lazy
  `Dashboard` shell. A new **sibling** `/app/terminal/*` route renders the
  cockpit full-bleed without the sidebar shell.
- State: `src/store/index.tsx` is a custom **Context + `useReducer`**, "zero
  third-party deps." The cockpit selection bus follows this exact pattern — no
  new state library.
- Real-time: `src/hooks/useSSE.ts` + `socket.io-client`/`ws` already power the
  streaming pages; the Live Feed panel reuses them.
- Services: `src/services/api.ts` exposes service objects
  (`savedQueryService`, `analyticsService`, `connectorService`, …) the panels
  reuse directly.
- Design tokens: `src/ui/primitives.css` already defines a dark, dense token set
  (`--bg-base/canvas/elevated`, `--accent*`, borders); `@fontsource/jetbrains-mono`
  is installed. The terminal theme is a thin high-density overlay, not a new system.
- A `CommandPalette.tsx` already exists (fuzzy search + `PageType` nav). We reuse
  its fuzzy-search UX pattern but drive the cockpit palette from a terminal
  command registry (panels/layouts/actions), leaving the existing palette intact.

## Architecture

```
/app/terminal/*  (ProtectedRoute, full viewport — NO sidebar shell)
  └─ TerminalWorkspace
       ├─ CockpitTopBar      (layout switcher · ⌘K palette · status strip · ← back to app)
       ├─ CockpitProvider    (Context + useReducer: selection bus + layout state)
       ├─ DockviewReact      (the dockable panel host)
       │    └─ for each panel: PanelErrorBoundary → <Suspense> → lazy Panel
       └─ TerminalCommandPalette  (⌘/Ctrl-K)
```

### 1. `TerminalWorkspace` (`src/terminal/TerminalWorkspace.tsx`)
Hosts a single `DockviewReact`. On mount, loads the saved/default layout via
`api.fromJSON()`; on layout change, debounced-persists `api.toJSON()` to
localStorage. Renders `CockpitProvider`, `CockpitTopBar`, and the command palette.

### 2. Panel registry (`src/terminal/panels/registry.ts`)
A single map: `panelId → { title, icon, component }`, where `component` is a
`React.lazy(() => import('./XPanel'))`. dockview's `components` prop is built from
this registry, so adding a panel later is a one-line entry. Exported
`PANEL_IDS` drives the "open panel" commands and the default layouts.

### 3. Panel contract
Every panel is a self-contained component that:
- receives dockview panel `params` (its panel API),
- reads/writes shared selection via the `useCockpit()` hook,
- reuses an existing AURA service/hook for data (no new backend, no forked logic),
- is wrapped by the registry host in `PanelErrorBoundary` + `Suspense`.

`PanelErrorBoundary` (`src/terminal/PanelErrorBoundary.tsx`) is a class component
catching render errors and showing an in-panel error card with a "Reload panel"
button (remounts that panel only). One panel crashing never takes down the
workspace — the core robustness guarantee.

### 4. Cockpit store / selection bus (`src/terminal/CockpitProvider.tsx`)
Matches the house `store/index.tsx` pattern: `createContext` + `useReducer`,
zero deps. Holds the **selection context**. Phase-1 dimension:
```ts
interface CockpitState { activeDatasetId: string | null; }
type CockpitAction = { type: 'SET_ACTIVE_DATASET'; id: string | null };
```
`useCockpit()` returns `{ activeDatasetId, setActiveDataset }`. The reducer is
trivially testable in isolation. Selection is **not** persisted (ephemeral
session state); only layout is persisted.

### 5. Layout persistence (`src/terminal/layoutStore.ts`)
- Save: `dockviewApi.toJSON()` → `JSON.stringify` → `localStorage["aura.terminal.layout.<name>"]`,
  debounced (~500ms) on dockview layout-change events.
- Load: read key → `JSON.parse` → `dockviewApi.fromJSON()`, all inside a
  `try/catch`; on any parse/restore error, discard the bad value and fall back
  to the built-in default layout (never white-screen).
- Ships 2–3 **default layouts** (`Analyst`, `Auditor`, `Ops`) as code-defined
  dockview JSON; the top-bar switcher applies one via `fromJSON()`.
- Per-user **server-side** persistence is a Phase-2 seam (a `layoutStore` adapter
  interface so swapping localStorage → an API is a one-file change).

### 6. Command palette (`src/terminal/TerminalCommandPalette.tsx`)
Reuses the existing fuzzy-search pattern (extract `fuzzyScore` into a shared util
so both palettes use one implementation). Commands come from a terminal command
registry: `Open <panel>`, `Switch layout → <name>`, `Run query…`, `Focus dataset…`,
`Reset layout`, `Back to app`. Opened with `⌘/Ctrl-K`; arrow-key navigation +
Enter to run; Esc closes.

### 7. Theme overlay (`src/terminal/terminal.css`)
A scoped `.aura-terminal` class layering high-density rules on the existing
tokens: tighter paddings, `jetbrains-mono` for numeric/data cells,
monospace-tabular numbers, dockview tab/group skinned to AURA tokens. No global
token changes (so the rest of the app is unaffected).

### 8. Entry point
Reachable from the existing app without disturbing it: a "Terminal" item in the
existing sidebar nav (`Layout/nav.ts`) and a command in the existing
`CommandPalette` (`Open Terminal`), both `navigate('/app/terminal')`. The cockpit
top bar's "← Back to app" returns to `/app`.

## The four panels (each thin, reusing existing code)

- **Query + Results** (`QueryPanel.tsx`) — reuses `chatService` (`api.ts`, the
  unified `/chat` NL→SQL pipeline): prompt input → generated SQL →
  result table → auto chart (recharts). Reads `activeDatasetId` to scope/hint the
  question; if a dataset is active, it is passed as the query's dataset context.
- **Datasets & schema** (`DatasetsPanel.tsx`) — reuses `connectorService`/the
  files data (and the `store` files state): a dataset list + column-level
  schema/profile. Selecting a row calls `setActiveDataset(id)` — **the
  cross-filter source.**
- **Audit findings / HITL** (`FindingsPanel.tsx`) — reuses the audit/findings
  service: findings list + exception detail + signed-certificate context.
  Filters its list by `activeDatasetId` when one is set (cross-filter consumer).
- **Live monitoring feed** (`LiveFeedPanel.tsx`) — reuses `useSSE`/the streaming
  socket: a real-time scrolling feed of streaming metrics + UASR healing events,
  newest on top, with severity coloring. Read-only ticker in v1.

## Data flow

- Panels call the **same** existing services/hooks as today's pages → no new
  backend, no API changes.
- **Cross-filter:** Datasets panel → `setActiveDataset(id)` → `CockpitProvider`
  reducer → Query + Findings panels re-render scoped to that dataset. One real,
  visible ripple in v1 (multi-hop is Phase 2).
- **Layout:** dockview change event → debounced `toJSON` → localStorage; load on
  mount → `fromJSON`.

## Error handling & robustness (hard requirement)

- **Additive route** + **per-panel error boundaries** + **lazy/code-split
  panels** + **dockview zero-dep core** + **guarded localStorage** = a small,
  contained blast radius.
- A throwing panel → in-panel error card, workspace survives.
- A corrupt persisted layout → caught, discarded, default layout loaded.
- A panel's data service failing → the panel shows its own error/empty state
  (reusing existing service error handling); other panels unaffected.
- The existing app and its tests are untouched by construction — the only edits
  to existing files are: one route in `AppRoutes.tsx`, one nav item in
  `Layout/nav.ts`, one command in `CommandPalette.tsx`, and extracting `fuzzyScore`
  into a shared util (re-imported by the existing palette — behavior identical).

## Testing (Vitest, repo conventions: `tsc --noEmit`, `eslint --max-warnings 0`, `vitest run`)

- **Cockpit reducer:** `SET_ACTIVE_DATASET` updates state; `useCockpit` exposes it.
- **Cross-filter:** rendering Datasets + Findings under one `CockpitProvider`,
  selecting a dataset row filters the Findings list (real behavior, not a mock).
- **PanelErrorBoundary:** a child that throws renders the error card + "Reload
  panel"; sibling content is unaffected.
- **layoutStore:** `toJSON→localStorage→fromJSON` round-trips; a corrupt value
  falls back to the default without throwing (localStorage mocked).
- **Panel registry:** every `PANEL_ID` resolves to a lazy component + title + icon.
- **Each panel** renders against a mocked service and (where applicable) reacts to
  `activeDatasetId`.
- **Command palette:** fuzzy search ranks an exact panel name first; Enter runs it.
- Existing frontend suite stays green (additive).

## File structure (new, isolated under `src/terminal/`)

```
src/terminal/
  TerminalWorkspace.tsx        — dockview host + layout load/save + providers
  CockpitProvider.tsx          — Context+useReducer selection bus (useCockpit)
  CockpitTopBar.tsx            — layout switcher · ⌘K · status strip · back
  TerminalCommandPalette.tsx   — cockpit command surface
  PanelErrorBoundary.tsx       — per-panel crash isolation
  layoutStore.ts               — localStorage adapter + default layouts
  commands.ts                  — terminal command registry
  terminal.css                 — high-density theme overlay
  panels/
    registry.ts                — panelId → {title, icon, lazy component}
    QueryPanel.tsx
    DatasetsPanel.tsx
    FindingsPanel.tsx
    LiveFeedPanel.tsx
  __tests__/                   — vitest specs per above
src/utils/fuzzyScore.ts        — extracted shared fuzzy matcher
```
Existing files touched (minimal): `src/AppRoutes.tsx` (one route),
`src/components/Layout/nav.ts` (one nav item), `src/components/CommandPalette.tsx`
(one command + import shared fuzzyScore), `package.json` (add `dockview-react`).

## Out of scope — roadmap (NOT built here)

- **Phase 2:** multi-dimension cross-filter (finding → dataset → lineage chains);
  server-side per-user/per-tenant saved layouts (via the `layoutStore` adapter
  seam); multi-monitor **popout windows** (dockview popout API).
- **Phase 3:** more panels — lineage graph, counterfactual/causal, pipelines,
  cost — registered through the same panel registry; object-centric drill
  (click any entity → fan out its connections).
- **Phase 4:** a richer command grammar (mnemonic commands), per-panel toolbars,
  alerting on live-feed thresholds.
- Mobile/small-screen is best-effort in v1 (the cockpit targets desktop); a
  responsive fallback is a later refinement.
