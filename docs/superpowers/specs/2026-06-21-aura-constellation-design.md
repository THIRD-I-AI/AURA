# AURA Constellation — Interactive Knowledge-Graph Canvas + Discoverable Terminal Entry

> **Sprint S47 · issue #123.** Follow-up to the S46 Terminal Cockpit. Adds the
> flagship "visually stunning, interactable" graphic the cockpit was missing,
> and fixes the deferred (undiscoverable) entry point. Additive — the existing
> app and the S46 cockpit are extended, not broken.

**Goal:** A pannable / zoomable / draggable **force-directed knowledge-graph
canvas** — AURA's data lineage rendered as a living network of glowing,
on-brand, type-coded nodes that you explore by hand and that **cross-filters the
cockpit** on click — plus a **discoverable launcher** into `/app/terminal` from
the main app.

## Decisions (settled in brainstorming)

- **Graph library: `@xyflow/react` (React Flow 12).** Verified current (June
  2026): 37k★, pushed within a day, MIT, TypeScript-native, React-19 peer
  (`react>=17`). Chosen over react-force-graph (canvas-drawn nodes → hard to make
  on-brand + wire clean clicks) and reagraph (smaller) because React Flow renders
  **DOM custom nodes** — so nodes are styled with the exact AURA token system,
  and node selection/click integrates cleanly with the cockpit cross-filter.
- **Layout: `d3-force`** (tiny, standard) computes organic node positions; React
  Flow renders + handles pan/zoom/drag/minimap/selection. A pure layout function
  keeps the physics testable and the render thin.
- **Data: the existing `lineageService.get()`** (`GET /lineage` →
  `LineageGraph{ nodes: {id,type:'table'|'saved_query'|'dashboard',label,metadata}, edges:{id,source,target}, summary }`).
  No backend change.
- **Scope:** v1 renders the existing table/query/dashboard graph, beautifully
  and interactively. **Findings/certificate nodes need a backend graph
  extension** (the lineage builder doesn't emit them) → deferred; the node-type
  system is built to extend.
- **Additive:** a new cockpit panel registered through `panels/registry.ts`; the
  static-SVG Lineage page is left untouched (a later refinement may embed this).

## Architecture

### 1. Entry point (`/app/terminal` is reachable + obvious)
Two complementary, low-risk entries:
- **Header launcher** — a visible "▦ Terminal" button in the app header's
  right-side action zone (`Header.tsx`, `app-header__right`) using
  `useNavigate()` → `/app/terminal`. Always present; the primary discoverable
  entry.
- **Sidebar nav item** — a `terminal` entry in `NAV_ITEMS` (`Layout/nav.ts`) with
  a matching `NAV_ICON_MAP['terminal']` icon in `Sidebar.tsx` (so the S35a
  icon-rail test stays green). Because nav items dispatch `onItemClick(id)` into
  the App's `PageType` router (and `terminal` is a route, not a page), the App's
  item handler gets one special-case: `if (id === 'terminal') navigate('/app/terminal')`.

### 2. `ConstellationPanel` (`src/terminal/panels/ConstellationPanel.tsx`)
The flagship cockpit panel. On mount: `lineageService.get()` →
`computeConstellationLayout(graph)` (pure) → React Flow `nodes`/`edges`. Renders
`<ReactFlow>` with `<Background>`, `<MiniMap>`, `<Controls>`, custom node type,
and a search box. Loading / empty / error states. Registered as panel id
`constellation` (5th panel) in `registry.ts` (title "Constellation", an
icon). It inherits the panel error boundary + lazy-load from the S46 shell.

### 3. Layout (`src/terminal/constellation/layout.ts`) — pure + testable
- `computeConstellationLayout(graph: LineageGraph): { nodes: RFNode[]; edges: RFEdge[] }`.
- Builds a `d3-force` simulation (`forceManyBody` repulsion, `forceLink` on
  edges, `forceCenter`, `forceCollide` by node radius), ticks it to settle
  (fixed iteration count for determinism — no animation loop in the sim itself),
  and maps each lineage node to a React Flow node `{ id, type:'aura', position:{x,y},
  data:{ label, kind, degree } }` and each edge to `{ id, source, target,
  animated:true }`. `degree` (connection count) drives node size.

### 4. `AuraNode` (`src/terminal/constellation/AuraNode.tsx`) — the on-brand node
A custom React Flow node: a glowing disc + label, color-coded by `kind`
(`table`→cyan, `saved_query`→green, `dashboard`→blue; extensible map), sized by
`degree`, with a soft halo and a type glyph. React Flow `Handle`s (hidden) for
edge attachment. Class names (`aura-node`, `aura-node--<kind>`, `is-focused`,
`is-dimmed`) so the theme styles it.

### 5. Interaction layer (in `ConstellationPanel`)
- **Pan / zoom / drag**: native React Flow (`fitView`, zoom controls, draggable
  nodes, minimap).
- **Focus** (the Palantir effect): hovering/selecting a node computes its
  1-hop neighbor set; neighbors + incident edges get `is-focused`, everything
  else `is-dimmed` (opacity down). Clearing selection restores all.
- **Cross-filter**: clicking a `table` node calls `useCockpit().setActiveDataset(label)`
  → the Query + Findings panels scope to it (the S46 bus). A non-table node just
  focuses.
- **Search-to-fly**: a search input filters/looks up a node by label and
  `setCenter()`s + focuses it.

### 6. Theme (`src/terminal/terminal.css`, additions)
Scoped `.aura-terminal` rules for `.constellation-panel` (the canvas fills the
panel), the React Flow surface remapped onto AURA tokens (dark canvas, dotted
`<Background>` in `--t-line`, minimap/controls skinned), `.aura-node` glow +
per-kind colors + `is-focused`/`is-dimmed`, and animated edge styling. Honors
`prefers-reduced-motion` (no edge dash animation).

## Data flow

```
lineageService.get()  →  LineageGraph
  → computeConstellationLayout (d3-force, pure)  →  RF nodes/edges
  → <ReactFlow> renders AuraNodes + animated edges
  → hover/click → focus (dim non-neighbors); table click → setActiveDataset → cockpit cross-filter
```

## Error handling & robustness

- Loading spinner; empty graph → an on-brand "no lineage yet" state; fetch error
  → in-panel error (reusing the panel error styling). The panel is already
  wrapped in the S46 `PanelErrorBoundary`, so a React Flow crash is contained.
- The d3-force sim runs a bounded iteration count (no infinite/async loop) and is
  recomputed only when the graph identity changes (memoized), so re-renders are
  cheap.
- React Flow is dynamically imported via the registry's `lazy()` (code-split), so
  it never weighs on the rest of the app.

## Testing

- **`computeConstellationLayout` (pure, unit):** given a small graph, returns one
  RF node per lineage node with finite `{x,y}`, correct `kind`/`degree`, and one
  edge per lineage edge with `animated:true`. Deterministic (fixed sim seed/iters).
- **Focus logic (pure helper `neighborSet(edges, id)`):** returns the node + its
  1-hop neighbors.
- **`ConstellationPanel` render (mock `@xyflow/react` + `lineageService`):** jsdom
  can't lay out React Flow, so mock `ReactFlow` to capture its `nodes`/`edges`
  props and invoke its `onNodeClick`; assert the panel passes the computed
  graph, and that clicking a `table` node calls `setActiveDataset(label)` while a
  `dashboard` node does not. Assert loading→data and the empty/error states.
- **Entry point:** `NAV_ITEMS` contains `terminal` and `NAV_ICON_MAP` has its
  icon (guards the S35a test); the header launcher renders and `navigate`s to
  `/app/terminal`; the App item-handler special-cases `terminal` to navigate.
- **No breakage:** the whole existing frontend suite stays green; `npm run build`
  (tsc -b) passes. New deps `@xyflow/react`, `d3-force`, `@types/d3-force`.

## File structure

```
src/terminal/constellation/
  layout.ts            — computeConstellationLayout (d3-force, pure) + neighborSet
  AuraNode.tsx         — custom React Flow node (on-brand, kind-coded, glow)
  __tests__/           — layout + neighborSet unit tests
src/terminal/panels/
  ConstellationPanel.tsx   — the panel (React Flow host + interactions + states)
  registry.ts              — +constellation entry (MODIFY)
src/terminal/terminal.css  — constellation/node/edge theme (MODIFY)
src/terminal/__tests__/ConstellationPanel.test.tsx
Entry point (MODIFY):
  src/components/Layout/Header.tsx        — launcher button
  src/components/Layout/nav.ts            — NAV_ITEMS += terminal
  src/components/Layout/Sidebar.tsx       — NAV_ICON_MAP += terminal icon
  src/App.tsx (or the onItemClick consumer) — special-case 'terminal' → navigate
package.json — +@xyflow/react ^12, +d3-force ^3, +@types/d3-force
```

## Out of scope — later

- **Findings / certificate nodes** — needs the backend lineage builder to emit
  them (+ risk-colored nodes, dataset→finding→cert edges). The node-kind system
  is built to extend.
- **3D constellation mode** (react-force-graph / WebGL) — a future "max dazzle"
  toggle.
- **Replacing the static Lineage page** with this canvas — v1 is additive; the
  page swap is a later refinement.
- The other flagship candidates (live command-center board, brushable result
  charts) — separate future sprints.
