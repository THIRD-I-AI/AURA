# Frontend design system — MANDATORY conventions

Auto-loaded when working under `frontend/`. **All frontend UI work uses
shadcn/ui + Tailwind v4. This is non-negotiable — do not hand-roll inline
`style={{…}}` objects for new/rebuilt components.**

## The stack (already wired in this repo)

- **shadcn/ui** — style `new-york`, config in `frontend/components.json`.
  Primitives live in **`src/components/ui-kit/`** (alias `@/components/ui-kit`).
  Add new ones with `npx shadcn@latest add <component>` — they land in `ui-kit`.
- **Tailwind v4** — entry `src/styles/tailwind.css`. Its `@theme inline` block
  **binds every utility to a design token** in `tokens.css`, so a class like
  `bg-surface` compiles to `background-color: var(--bg-surface)` and stays
  theme-correct (dark default + the `certificate` light island). Tailwind
  utilities ARE the terminal-authority design — use them, not raw hex.
- **`cn()`** from `@/lib/cn` (clsx + tailwind-merge) for conditional/merged
  classes. **Icons:** `lucide-react`.

## Rules

1. **Compose from `@/components/ui-kit`** first: `Panel` / `PanelHeader` /
   `PanelBody`, `Card` (+ `CardHeader/Title/Description/Content/Action`),
   `Button` (cva variants), `EmptyState` (`intent="awaiting|empty|error"`),
   `StatusGlyph`. Need something new → `npx shadcn@latest add …`, then compose.
2. **Style with Tailwind token utilities**, never inline styles or raw colors:
   - Surfaces: `bg-base` `bg-surface` `bg-surface-2` `bg-sunken` `bg-raised` `bg-card`
   - Text: `text-text-primary` `text-text-secondary` `text-text-tertiary`
   - Borders: `border border-border-hairline` `border-border-subtle` `border-border-strong`
   - Accents: `text-signal` / `bg-signal` (green), `text-warn` `text-danger` `text-info`
   - Type: `font-mono` (JetBrains Mono) for data/labels, `font-display` for headings
   - Sharp corners (`rounded-none`) is the house style; mono, dense, green-signal.
3. **`cn()` for conditionals** — `cn('base', active && 'bg-selected text-text-primary')`.
4. **Dark-first, token-driven.** Never use Tailwind's `dark:` variant — theming is
   `[data-theme]`-driven through the tokens (already handled). Don't add raw hex.
5. **Do NOT use the legacy `src/components/ui/`** (Material-Design-era, CSS-file
   based). `ui-kit` is the current system. `UI_SYSTEM.md` is stale — this file wins.
6. **A11y:** clickable elements are real `<button>`/`<a>` (or `role` + `tabIndex` +
   key handler), inputs have labels/`id`/`name`, views have a `main` landmark.

## Verify before pushing (from `frontend/`)

```sh
npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run
```

## Workbench native panels

Nav → view mapping is `src/workbench/viewRegistry.ts`; native panels live in
`src/workbench/panels/`. **New panels must be built with ui-kit + Tailwind**
(the earlier `panels/*Panel.tsx` written with inline styles are being migrated
to this system — match the migrated ones, not the inline-styled ones).
