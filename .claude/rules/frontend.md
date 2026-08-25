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

## Anti-slop layout discipline

- **No floating soft cards.** `border border-border rounded-none` is the house
  card, not `shadow-xl rounded-3xl` — sharp corners, hairline borders, no drop
  shadow (already stated above; restating because it's the #1 generic-SaaS tell).
  `Panel`/`PanelHeader`/`PanelBody` already give you this — compose from them
  before hand-rolling a card `<div>`.
- **Three-pane pattern** (collapsible left nav → central data view → optional
  right detail drawer) is the right default shape for a new complex view — the
  Workbench shell already has the collapsible left rail (`Workbench.tsx`); a
  right contextual drawer doesn't exist yet anywhere, so don't assume one is
  already built when reaching for this pattern.
- **Cascade-layer trap**: Tailwind utilities here compile into `@layer utilities`
  (see `tailwind.css`'s header comment), and `design-system.css` still carries a
  few plain, unlayered rules (`a { color: var(--text-link) }` is one — hit this
  exact conflict migrating `AuditFrontDoor.tsx`, PR #202). Unlayered CSS beats
  ANY layered CSS regardless of specificity. If a Tailwind color/text utility on an `<a>`/native element
  visibly doesn't apply, this is almost always why — check `getComputedStyle`
  before assuming the class is wrong, and use the `!` important-prefix
  (`!text-signal`) as the fix, not a specificity hack.

## Forms

- Every input gets a real `<label htmlFor>` (or a visually-hidden one) — never a
  placeholder standing in as the label.
- Invalid state is `aria-invalid` + the paired `aria-invalid:border-destructive`
  styling `Button`/form primitives already carry (see `button.tsx`) — don't
  invent a separate red-border convention.
- Every async submit button needs a real `disabled`/loading state during the
  request — this codebase already does this everywhere real fetches happen
  (`disabled={busy === r.id}` etc. in the native panels); keep doing it.

## Loading states — a known gap, not yet resolved

`workbench.css` already defines a shimmer skeleton primitive (`.aw-skeleton`,
gradient + `awshimmer` animation) sized to match real content shapes. It is
**not currently exposed as a reusable `ui-kit` component**, and none of the
native panels use it — they all use `EmptyState intent="awaiting"` (icon + one
line of text) for loading, which is simpler but doesn't show the shape of the
incoming data. Don't silently "upgrade" a panel to a shimmer skeleton on your
own judgment call — that's a real, visible product decision (simple state vs.
shaped skeleton), not a style nit. If you think a specific view needs a shaped
skeleton, say so and ask, or build `Skeleton` as a proper `ui-kit` primitive
first so every consumer gets it the same way, rather than hand-rolling one
shimmer div per file.

## Semantic color naming — two layers, don't cross them

- **Hand-authored panel content** uses the app's own tokens: `text-danger`
  `bg-danger` `text-warn` `text-signal` (bound to `tokens.css` — see Data
  density & tokens above).
- **shadcn primitives** (`Button variant="destructive"`, `aria-invalid:*`) use
  shadcn's own `destructive` slot (bound to `--color-destructive`), a
  different theme key. Both are correct in their own layer — don't rename one
  to match the other, and don't invent a third name.

## Icons & focus rings

- Icon size tracks the text it sits beside: `size-4` (16px) next to `text-sm`,
  `size-3.5` next to `text-xs`/`2xs` — match, don't eyeball it.
- Custom interactive elements (anything not already a shadcn primitive) need a
  visible focus ring matching the existing primitive convention:
  `focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50`
  (copy this from `button.tsx` verbatim, don't invent `ring-2 ring-ring`). If it
  can't be tabbed to and focus-ringed, it isn't done.

## No fabricated data

Covered at length already in `Workbench.tsx`'s own comments — never map a
hardcoded array of N sample items to prototype a list. Real components read
real state (`x: T[] | null`) and render the honest `0`/`1`/`1000+` cases, same
as every native panel already does.

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
