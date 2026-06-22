# S49 — Screen-Aware Fluid Shell + Page-Aware Context Rail

**Status:** Approved (2026-06-22)
**Author:** Rohith (Claude Opus 4.8) with Mounith
**Supersedes:** the S48 `max-width: 1600px` content cap (removed here)

## Problem

S48 added `max-width: 1600px; margin-inline: auto` to `.app-shell__main-inner`
above 1700px. Measured live, this strands a large dead gap on wide screens:

| Viewport | Dead gap | Content uses |
|---|---|---|
| 2560px | 720px | 69% of available width |
| 3440px | 1320px | 59% of available width |

The app is not fluid: it snaps to one comfortable column and wastes the rest.
User asks for a "really aware frontend" with "an anchor point for the screen
sizes" so the app is "smart, aware and fluid" and the dead space instead "opens
some extra functions on the right." The login page also floats sparsely in a void.

## Goals

1. **One awareness anchor.** A single source of truth for the current screen
   that every adaptive component reads, instead of scattered media queries.
2. **No dead gap, no stretched cards.** Content fills its column fluidly at
   every width; cards reach a comfortable max and pack into more columns rather
   than ballooning.
3. **Wide screens gain functionality.** The reclaimed width becomes a
   page-aware Context rail that reframes per page.
4. **Nothing breaks below `wide`.** Laptops, tablets, phones, and split-windows
   are unaffected (the rail simply doesn't exist there).

## Non-Goals

- Rewriting individual page internals beyond removing stretch and wiring rail
  content. (Per-page deep redesigns are out of scope.)
- The AI-Copilot or Command-center rail flavors (page-aware chosen).
- Changing the Terminal cockpit's own layout engine (dockview); it keeps its
  S48 mobile fallback, repointed at the new anchor.

## Architecture

### 1. The anchor — `ViewportProvider` + `useViewport()`

`frontend/src/shell/ViewportProvider.tsx` (new). A context mounted once at the
true app root (in `main.tsx`, wrapping the whole router so both the authed
`/app/*` shell **and** the public `/login` page read the same anchor). It watches the
viewport via a single `ResizeObserver` on `document.documentElement` plus a
`resize` listener (rAF-throttled), and exposes:

```ts
export type ViewportClass = 'compact' | 'cozy' | 'standard' | 'wide' | 'ultrawide';

export interface Viewport {
  width: number;            // px, live
  height: number;           // px, live
  screen: ViewportClass;      // derived from width via BREAKPOINTS
  hasRail: boolean;         // screen === 'wide' || 'ultrawide'
  sidebarMode: 'drawer' | 'rail' | 'full';
  atLeast: (c: ViewportClass) => boolean;  // ordered comparison helper
}

export const BREAKPOINTS = { cozy: 768, standard: 1200, wide: 1600, ultrawide: 2200 } as const;
```

Derivation (the "anchor points"):

| `screen` | width range | `sidebarMode` | `hasRail` |
|---|---|---|---|
| `compact` | `< 768` | `drawer` | false |
| `cozy` | `768–1199` | `rail` | false |
| `standard` | `1200–1599` | `full` | false |
| `wide` | `1600–2199` | `full` | **true** |
| `ultrawide` | `≥ 2200` | `full` | **true** |

`useViewport()` returns the context value; throws only if used outside the
provider (consumers in the authed tree are always inside it). SSR/test-safe:
when `window` is absent, defaults to `{ width: 1280, screen: 'standard', … }`.

The existing `frontend/src/terminal/useMediaQuery.ts` stays as a low-level
primitive; `ViewportProvider` uses the same guard pattern. The terminal's
`TerminalWorkspace` switches its `useMediaQuery('(max-width: 860px)')` to
`useViewport().atLeast('standard')` so there is one anchor app-wide. The S48
`useMediaQuery` hook + tests remain (still used as the primitive).

### 2. The fluid shell — `AppLayout.tsx` + `AppLayout.css`

`.app-shell` becomes a CSS grid driven by a `data-viewport` / `data-rail`
attribute set from `useViewport()` (so CSS and JS agree on the anchor):

```
.app-shell                       display: grid; height: 100vh;
  grid-template-columns: auto minmax(0, 1fr);            /* no rail */
.app-shell[data-rail='true']
  grid-template-columns: auto minmax(0, 1fr) var(--rail-w);  /* with rail */
  --rail-w: clamp(300px, 22vw, 460px);
```

- Column 1: `<Sidebar>` (its own width per `sidebarMode`).
- Column 2: `.app-shell__content` (Header + `.app-shell__main` →
  `.app-shell__main-inner`). **The S48 `max-width`/`margin-inline` cap is
  deleted.** `.app-shell__main-inner` fills its grid column at every width.
- Column 3 (only when `data-rail='true'`): `<ContextRail>`.

`.app-shell__main` keeps `overflow-y: auto` (independent scroll); the rail
scrolls independently too. Padding grows by tier via `data-viewport` (e.g.
`--space-4` compact → `--space-6` standard → `--space-8` wide+).

The mobile drawer behavior (S48, `max-width: 767px`) is preserved but keyed off
`data-viewport='compact'`.

### 3. Fluid content — kill the stretch

A shared utility class set in `design-system.css`:

```css
/* Cards pack into more columns on wide screens, capped so they never balloon. */
.fluid-cards { display: grid; gap: var(--space-4);
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); }
.fluid-cards--cap > * { max-width: 420px; }   /* opt-in hard cap per tile */
```

The dashboard KPI strip (`dashboard-grid--kpis`) and quick-start panels
(`dashboard-grid--panels`) adopt fluid packing instead of fixed
`repeat(4,1fr)` / `repeat(2,1fr)`, so 4 tiles stay ~240–300px and the row's
leftover width is small (the rail consumes the far-right). Charts/tables (which
benefit from width) fill the content column.

### 4. The page-aware Context rail — `ContextRail`

`frontend/src/shell/ContextRail.tsx` (new) + `ContextRail.css`. Rendered as grid
column 3 only when `useViewport().hasRail`. Structure:

```
<aside class="context-rail" data-collapsed=…>
  <header> page-title · collapse/pin toggle </header>
  <div class="context-rail__body"> {slot for current page} </div>
</aside>
```

Per-page content is resolved by a small registry keyed on `PageType`:

```ts
// frontend/src/shell/railRegistry.tsx
export const RAIL_CONTENT: Partial<Record<PageType, React.LazyExoticComponent<React.FC>>>;
```

Initial slots (others fall back to a default "Activity + Quick actions" panel):

- **dashboard** → `DashboardRail`: live activity feed (recent runs/certs from
  existing services), recent saved queries, system pulse (reuse `useSystemHealth`),
  and a compact quick-ask box that routes to `/app/chat` with the prompt.
- **lineage** → `LineageRail`: the node inspector (today's right-hand `<aside>`
  inside `Lineage.tsx` moves here when the rail exists; Lineage detects
  `hasRail` and renders inspector inline only when the rail is absent).
- **queries / library** → `HistoryRail`: recent query history + saved/starred
  queries with reopen-in-chat.
- **default** → `DefaultRail`: quick actions + system pulse + recent activity.

Collapse state persists in `localStorage['aura.rail.collapsed']`. Collapsed = a
thin (48px) strip with the pin icon to reopen; the grid column shrinks to 48px.
`prefers-reduced-motion` respected for the collapse transition.

All rail data comes from existing services (`savedQueryService`,
`useSystemHealth`, query-history store, lineage state); **no new backend.**

### 5. Login polish — `AuthForm`

`frontend/src/auth/AuthForm` (existing) gains a responsive two-pane: on
`atLeast('standard')` a left brand/value panel (logo, one-line value prop,
subtle gradient/grid texture in theme tokens) beside the form; on
`compact`/`cozy` the brand panel hides and the form centers as today. Pure
CSS + the anchor; no auth-logic changes.

## File Structure

```
frontend/src/shell/                         (new home for the awareness layer)
  ViewportProvider.tsx      — anchor: context + provider + useViewport
  ViewportProvider.test.tsx — class derivation + resize + SSR default
  ContextRail.tsx           — rail chrome (header, collapse, slot)
  ContextRail.css
  ContextRail.test.tsx      — renders only when hasRail; collapse persists
  railRegistry.tsx          — PageType → rail content (lazy)
  rails/DashboardRail.tsx    rails/LineageRail.tsx
  rails/HistoryRail.tsx      rails/DefaultRail.tsx
frontend/src/components/Layout/AppLayout.tsx  — grid shell, mounts rail, data-attrs
frontend/src/components/Layout/AppLayout.css  — grid columns, DELETE S48 cap, tier padding
frontend/src/styles/design-system.css         — .fluid-cards utility
frontend/src/main.tsx                          — wrap the router in <ViewportProvider>
frontend/src/pages/Lineage.tsx                 — inspector moves to rail when hasRail
frontend/src/auth/…                            — AuthForm two-pane
frontend/src/terminal/TerminalWorkspace.tsx    — read useViewport instead of useMediaQuery
```

## Data Flow

`ViewportProvider` (resize → rAF → setState) → `useViewport()` → `AppLayout`
sets `data-viewport`/`data-rail` on `.app-shell` and conditionally renders
`<ContextRail page={currentPage} />` → `ContextRail` looks up `RAIL_CONTENT[page]`
and renders it (lazy + Suspense + error boundary) → rail components read existing
services/stores. No prop-drilling of width; everything reads the anchor.

## Error Handling

- Rail content wrapped in an error boundary (reuse `ErrorBoundary`); a crashing
  rail panel never takes down the page.
- `useViewport` outside provider throws (developer error, caught in tests).
- Lazy rail slots use Suspense with a light skeleton.

## Testing (Vitest)

- `ViewportProvider`: width→class derivation at each boundary (767/768/1199/1200/
  1599/1600/2199/2200), `hasRail`/`sidebarMode` mapping, resize updates state,
  SSR/no-window default.
- `ContextRail`: renders nothing when `hasRail` false; renders the registry slot
  for a page; collapse toggles + persists to localStorage; unknown page → default.
- `AppLayout`: sets `data-rail='true'` only when `hasRail`; mounts rail in grid
  col 3; existing AppLayout tests stay green.
- Fluid grids verified by build + live Playwright probe (dead-gap = 0 at
  1366/1920/2560/3440; 0 doc overflow at 360/768/1280).
- Full suite + `npm run build` green; `tsc`/`eslint` clean.

## Migration / Compatibility

- Additive: new `shell/` module; `AppLayout` rewired but same public props.
- S48 `useMediaQuery` + its tests remain (primitive). S48 `terminal-mobile.css`
  + `MobileTerminalStack` remain; only the width source changes.
- Removing the S48 cap is the intended reversal; documented here.

## Verification Checklist

- [ ] 1366 laptop: full-width content, no rail, no overflow.
- [ ] 1920: content + rail, dead gap = 0.
- [ ] 2560 / 3440: content + wider rail, dead gap = 0, cards not stretched.
- [ ] 768 / 360: drawer/stacked, 0 doc overflow, rail absent.
- [ ] Drag window laptop↔monitor: layout reframes (class change observed).
- [ ] Login two-pane on desktop, centered on mobile.
- [ ] Terminal cockpit + Constellation unaffected (reads anchor).
```
