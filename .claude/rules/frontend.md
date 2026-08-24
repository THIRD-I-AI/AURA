---
description: Frontend style guide and the vite dev-server staleness trap
paths:
  - "frontend/**"
---

# Frontend Rules

## Style

- **shadcn/ui + Tailwind v4** for all new frontend work. Match the patterns in
  `src/ui-kit/` and the already-retrofitted panels under `src/workbench/panels/`.
- Tailwind utility classes and `@theme` tokens; avoid inline styles.
- Components strictly typed — `npm run build` (`tsc -b && vite build`) is the
  typecheck CI actually runs. `tsc --noEmit` against the root tsconfig misses
  project references, which once shipped 20 type errors CI-green.
- Verify design tokens by grepping the **built CSS**, not by eyeballing the dev
  server.

## Icons

- **lucide-react exclusively** for UI icons (nav, buttons, status) — `iconLibrary: "lucide"`
  in `components.json`. Raw inline `<svg>` stays fine for actual data visualizations
  (`components/radar/SystemRadar.tsx` draws its rings/nodes by hand) — that's drawing
  data, not iconography. Don't mix icon sets for the same UI role.

## Data density & tokens

- Tables, analytics, and settings favor **compact, grid-aligned density** over loose
  airy spacing — dense terminal-authority is the house style (`tokens.css`,
  `workbench.css`), not generic SaaS whitespace.
- Colors: **only** `tokens.css` custom properties via the Tailwind `@theme inline`
  bindings — `bg-base` `bg-surface` `bg-raised` `text-text-primary` `text-text-secondary`
  `border-border-hairline` `text-signal` `text-warn` `text-danger` (full list in
  `frontend/CLAUDE.md`). Never an arbitrary/raw hex value in a component.
- Type: `font-mono` (JetBrains Mono) for data/labels, `font-display` for headings —
  no ad hoc `font-family` declarations. Sharp corners (`rounded-none`) is the house
  style; don't introduce rounded cards.
- Avoid clichés: no floating card without a bordered `Panel` backing it, no carousels,
  no purple/indigo gradient text — see the "Avoid AI slop tropes" list in
  `frontend/CLAUDE.md`; this doesn't duplicate that list, just flags it applies here too.

## Tables, filters & heavy data views

- Compose every loading/empty/error state from `@/components/ui-kit`'s `EmptyState`
  (`intent="awaiting|empty|error"`), not a raw "Loading…" string — one that can go
  stale after a fetch fails is a real bug, not a style nit (see `CostPanel.tsx` for
  the corrected pattern: `error` intent gets a Retry action, `awaiting` intent for
  genuinely-empty data).
- Add sorting/filtering when a column's data actually varies enough to be worth
  sorting — most current tables (Query History, Cost) are short read-only lists;
  don't add UI for controls with nothing meaningful to control.
- Overflow: `truncate` + a `title` attribute (or a shadcn Tooltip) on any cell that
  can clip long values — don't let content silently overflow the grid.
- Row actions live inline, or in a sticky multi-select bar for bulk operations —
  not a modal, for anything already visible in a dense table.
- Virtualize only once a dataset realistically renders 100+ concurrent DOM rows,
  and add a virtualization library deliberately then (none is installed today) —
  don't hand-roll one, and don't add the machinery to a workspace-scoped list
  that's realistically a handful of rows.

## Responsive checks

Page-level `overflow-x: hidden` hides overflow from a naive check. The real probe
is per-element: `rect.right > clientWidth`. Test at multiple widths, including
the human's actual viewport, before claiming fidelity.

## Vite dev-server staleness — read before switching branches

**Switching git branches under a live `vite` dev server breaks the running app**:
dead buttons, blank pages, phantom file-chooser dialogs under automation. Vite
holds its module graph in memory; a branch switch that makes whole modules appear
or disappear leaves that graph stale, so the browser gets a broken bundle. The
backend and the on-disk code are fine — only the dev-server process is poisoned.

- **Fix:** `cd frontend && npm run dev:fresh` (`vite --force`, also clears the
  dep-optimize cache), then hard-reload the browser.
- **Prevention:** the committed `post-checkout` hook (`.github/hooks/post-checkout`,
  installed by `scripts/install-hooks.ps1`) warns when a branch switch touches
  `frontend/`.
- **Discipline:** do NOT churn the working-tree branch while the human is using a
  dev server. If you must, restart vite afterward and say so. When automation
  looks right but the human's tab does not, suspect stale vite before the code.
