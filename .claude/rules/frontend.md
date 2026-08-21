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
