# AURA Terminal Cockpit (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A full-viewport, dockable multi-panel command terminal at `/app/terminal` with four live panels (Query, Datasets, Findings, Live Feed), saved layouts, a command palette, and one real cross-filter — added without touching the existing app.

**Architecture:** A new `/app/terminal/*` route renders `TerminalWorkspace`, which hosts a `dockview-react` `DockviewReact`. Panels come from a registry, each wrapped in an error boundary + Suspense. A `CockpitProvider` (React Context + `useReducer`, house style) holds an `activeDataset` selection that ripples to the Query and Findings panels. Layouts persist to localStorage.

**Tech Stack:** React 19, Vite, TypeScript, `dockview-react@^6.6.1`, Vitest + @testing-library/react, recharts (existing), the existing `services/api.ts` + `hooks/useSSE.ts`.

## Global Constraints

- Additive only. The existing app and its tests MUST stay green. The only edits to existing files: one route in `src/AppRoutes.tsx`, one nav item in `src/components/Layout/nav.ts`, one command + a `fuzzyScore` import swap in `src/components/CommandPalette.tsx`, `package.json` (add `dockview-react`), and a `ResizeObserver` stub in `src/test/setup.ts`.
- Panel engine is `dockview-react@^6.6.1`. Import the React component from `dockview-react` and its CSS from `dockview-react/dist/styles/dockview.css`.
- Cross-filter dimension (v1): `activeDataset: string | null` = a dataset **filename** (from `uploadService`). Query passes it as `chatService` `uploadedFile`; Findings substring-matches it (AuditFinding has no dataset field).
- House state pattern: React `createContext` + `useReducer`, zero third-party state libs (mirror `src/store/index.tsx`).
- All new code under `src/terminal/`; the one extracted util at `src/utils/fuzzyScore.ts`.
- Pre-push (run from `frontend/`, all must pass): `npx tsc --noEmit`, `npx eslint src --max-warnings 0`, `npx vitest run`.
- Tests: Vitest (globals, jsdom, `./src/test/setup.ts`), `@testing-library/react`, `data-testid` selectors, matchers from `@testing-library/jest-dom/vitest`.
- Conventional Commits; co-author `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`. Branch `feature/terminal-cockpit` (already checked out). Subagents implement + commit, do NOT push.
- Work in `frontend/` (all paths below are relative to `frontend/` unless noted).

## File Structure

```
src/utils/fuzzyScore.ts                — extracted shared fuzzy matcher (Task 1)
src/terminal/
  CockpitProvider.tsx                  — Context+useReducer selection bus + useCockpit (Task 2)
  PanelErrorBoundary.tsx               — per-panel crash isolation (Task 3)
  layoutStore.ts                       — localStorage persist/restore + DEFAULT_LAYOUTS (Task 4)
  panels/registry.ts                   — PANEL_REGISTRY + PANEL_IDS (Task 5)
  panels/QueryPanel.tsx                — (stub Task 5 → real Task 8)
  panels/DatasetsPanel.tsx             — (stub Task 5 → real Task 9)
  panels/FindingsPanel.tsx             — (stub Task 5 → real Task 10)
  panels/LiveFeedPanel.tsx             — (stub Task 5 → real Task 11)
  TerminalWorkspace.tsx                — dockview host + providers + layout wiring (Task 6)
  CockpitTopBar.tsx                    — layout switcher · ⌘K · status · back (Task 7)
  TerminalCommandPalette.tsx           — cockpit command surface (Task 12)
  commands.ts                          — terminal command registry (Task 12)
  terminal.css                         — high-density theme overlay (Task 13)
  __tests__/                           — one spec per unit
Modified: src/AppRoutes.tsx, src/components/Layout/nav.ts,
          src/components/CommandPalette.tsx, src/test/setup.ts, package.json
```

---

## Task 1: Extract shared `fuzzyScore` util

**Files:**
- Create: `src/utils/fuzzyScore.ts`
- Modify: `src/components/CommandPalette.tsx` (replace local `fuzzyScore` with an import)
- Test: `src/utils/__tests__/fuzzyScore.test.ts`

**Interfaces:**
- Produces: `export function fuzzyScore(haystack: string, needle: string): number` — identical logic to the current `CommandPalette.tsx` `fuzzyScore` (empty needle → 1, exact → 100, startsWith → 80, includes → 60, subsequence → >0, else 0).

- [ ] **Step 1: Write the failing test**

`src/utils/__tests__/fuzzyScore.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { fuzzyScore } from '../fuzzyScore';

describe('fuzzyScore', () => {
  it('ranks exact > prefix > substring > subsequence > none', () => {
    expect(fuzzyScore('query', '')).toBe(1);
    expect(fuzzyScore('Query', 'query')).toBe(100);
    expect(fuzzyScore('Query Panel', 'query')).toBe(80);
    expect(fuzzyScore('Open Query', 'query')).toBe(60);
    expect(fuzzyScore('Query', 'qy')).toBeGreaterThan(0);
    expect(fuzzyScore('Query', 'zzz')).toBe(0);
  });
});
```

- [ ] **Step 2: Run it — expect FAIL** (`Cannot find module '../fuzzyScore'`)

Run: `npx vitest run src/utils/__tests__/fuzzyScore.test.ts`

- [ ] **Step 3: Create the util** (copy the exact body currently in `CommandPalette.tsx`)

`src/utils/fuzzyScore.ts`:
```ts
export function fuzzyScore(haystack: string, needle: string): number {
  if (!needle) return 1;
  const h = haystack.toLowerCase();
  const n = needle.toLowerCase();
  if (h === n) return 100;
  if (h.startsWith(n)) return 80;
  if (h.includes(n)) return 60;
  let last = -1;
  let runs = 0;
  for (const ch of n) {
    const idx = h.indexOf(ch, last + 1);
    if (idx === -1) return 0;
    if (idx === last + 1) runs += 1;
    last = idx;
  }
  return 1 + runs;
}
```
(If the current `CommandPalette.tsx` body differs in the subsequence tail, copy ITS exact lines instead — read lines 34–52 — so behavior is byte-identical.)

- [ ] **Step 4: Swap the import in `CommandPalette.tsx`** — delete the local `const fuzzyScore = …` block and add at the top: `import { fuzzyScore } from '../utils/fuzzyScore';`

- [ ] **Step 5: Run util test + the existing CommandPalette test**

Run: `npx vitest run src/utils/__tests__/fuzzyScore.test.ts src/components/__tests__` 
Expected: PASS (util test green; CommandPalette behavior unchanged). If no CommandPalette test exists, run `npx tsc --noEmit` to confirm the swap type-checks.

- [ ] **Step 6: Lint + commit**

```bash
npx eslint src/utils/fuzzyScore.ts src/components/CommandPalette.tsx --max-warnings 0
git add src/utils/fuzzyScore.ts src/utils/__tests__/fuzzyScore.test.ts src/components/CommandPalette.tsx
git commit -m "refactor(terminal): extract shared fuzzyScore util (S46)"
```

---

## Task 2: `CockpitProvider` — selection bus

**Files:**
- Create: `src/terminal/CockpitProvider.tsx`
- Test: `src/terminal/__tests__/CockpitProvider.test.tsx`

**Interfaces:**
- Produces:
  - `interface CockpitState { activeDataset: string | null }`
  - `function CockpitProvider({ children }: { children: React.ReactNode }): JSX.Element`
  - `function useCockpit(): { activeDataset: string | null; setActiveDataset: (name: string | null) => void }`

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/CockpitProvider.test.tsx`:
```tsx
import { act, render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { CockpitProvider, useCockpit } from '../CockpitProvider';

function Probe() {
  const { activeDataset, setActiveDataset } = useCockpit();
  return (
    <div>
      <span data-testid="active">{activeDataset ?? 'none'}</span>
      <button onClick={() => setActiveDataset('sales.csv')}>set</button>
    </div>
  );
}

describe('CockpitProvider', () => {
  it('defaults to no active dataset and updates on setActiveDataset', () => {
    render(<CockpitProvider><Probe /></CockpitProvider>);
    expect(screen.getByTestId('active').textContent).toBe('none');
    act(() => { screen.getByText('set').click(); });
    expect(screen.getByTestId('active').textContent).toBe('sales.csv');
  });
});
```

- [ ] **Step 2: Run — expect FAIL** (`Cannot find module '../CockpitProvider'`)

Run: `npx vitest run src/terminal/__tests__/CockpitProvider.test.tsx`

- [ ] **Step 3: Implement** (mirror `src/store/index.tsx`'s Context+useReducer style)

`src/terminal/CockpitProvider.tsx`:
```tsx
import { createContext, useContext, useReducer, useCallback, useMemo, type ReactNode } from 'react';

export interface CockpitState { activeDataset: string | null }
type CockpitAction = { type: 'SET_ACTIVE_DATASET'; name: string | null };

function reducer(state: CockpitState, action: CockpitAction): CockpitState {
  switch (action.type) {
    case 'SET_ACTIVE_DATASET':
      return { ...state, activeDataset: action.name };
    default:
      return state;
  }
}

interface CockpitContextValue {
  activeDataset: string | null;
  setActiveDataset: (name: string | null) => void;
}

const CockpitContext = createContext<CockpitContextValue | null>(null);

export function CockpitProvider({ children }: { children: ReactNode }) {
  const [state, dispatch] = useReducer(reducer, { activeDataset: null });
  const setActiveDataset = useCallback((name: string | null) => {
    dispatch({ type: 'SET_ACTIVE_DATASET', name });
  }, []);
  const value = useMemo(
    () => ({ activeDataset: state.activeDataset, setActiveDataset }),
    [state.activeDataset, setActiveDataset],
  );
  return <CockpitContext.Provider value={value}>{children}</CockpitContext.Provider>;
}

// eslint-disable-next-line react-refresh/only-export-components
export function useCockpit(): CockpitContextValue {
  const ctx = useContext(CockpitContext);
  if (!ctx) throw new Error('useCockpit must be used within a CockpitProvider');
  return ctx;
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `npx vitest run src/terminal/__tests__/CockpitProvider.test.tsx`

- [ ] **Step 5: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/CockpitProvider.tsx src/terminal/__tests__/CockpitProvider.test.tsx
git commit -m "feat(terminal): CockpitProvider selection bus (Context+useReducer) (S46)"
```

---

## Task 3: `PanelErrorBoundary` — crash isolation

**Files:**
- Create: `src/terminal/PanelErrorBoundary.tsx`
- Test: `src/terminal/__tests__/PanelErrorBoundary.test.tsx`

**Interfaces:**
- Produces: `class PanelErrorBoundary extends React.Component<{ panelTitle: string; children: React.ReactNode }>` — on a child render error, renders an error card with `data-testid="panel-error"`, the panel title, and a `Reload panel` button that resets the boundary (re-renders children).

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/PanelErrorBoundary.test.tsx`:
```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { PanelErrorBoundary } from '../PanelErrorBoundary';

function Boom({ explode }: { explode: boolean }) {
  if (explode) throw new Error('panel boom');
  return <div data-testid="ok">ok</div>;
}

describe('PanelErrorBoundary', () => {
  it('catches a child error and isolates it to an in-panel card', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {});
    render(
      <PanelErrorBoundary panelTitle="Query">
        <Boom explode={true} />
      </PanelErrorBoundary>,
    );
    expect(screen.getByTestId('panel-error')).toBeInTheDocument();
    expect(screen.getByText(/Query/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /reload panel/i })).toBeInTheDocument();
    spy.mockRestore();
  });

  it('renders children when they do not throw', () => {
    render(
      <PanelErrorBoundary panelTitle="Query">
        <Boom explode={false} />
      </PanelErrorBoundary>,
    );
    expect(screen.getByTestId('ok')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npx vitest run src/terminal/__tests__/PanelErrorBoundary.test.tsx`

- [ ] **Step 3: Implement**

`src/terminal/PanelErrorBoundary.tsx`:
```tsx
import React from 'react';

interface Props { panelTitle: string; children: React.ReactNode }
interface State { error: Error | null }

export class PanelErrorBoundary extends React.Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error) {
    // Contained on purpose: log, but never rethrow — the workspace survives.
    console.error(`[terminal] panel "${this.props.panelTitle}" crashed:`, error);
  }

  private reset = () => this.setState({ error: null });

  render() {
    if (this.state.error) {
      return (
        <div data-testid="panel-error" className="panel-error">
          <strong>{this.props.panelTitle} failed</strong>
          <p>{this.state.error.message}</p>
          <button onClick={this.reset}>Reload panel</button>
        </div>
      );
    }
    return this.props.children;
  }
}
```

- [ ] **Step 4: Run — expect PASS**

Run: `npx vitest run src/terminal/__tests__/PanelErrorBoundary.test.tsx`

- [ ] **Step 5: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/PanelErrorBoundary.tsx src/terminal/__tests__/PanelErrorBoundary.test.tsx
git commit -m "feat(terminal): per-panel error boundary for crash isolation (S46)"
```

---

## Task 4: `layoutStore` — persist/restore + default layouts

**Files:**
- Create: `src/terminal/layoutStore.ts`
- Test: `src/terminal/__tests__/layoutStore.test.ts`

**Interfaces:**
- Consumes (type only): `DockviewApi` from `dockview-react` — methods `toJSON()`, `fromJSON(data)`, `addPanel(opts)`.
- Produces:
  - `function persistLayout(name: string, api: Pick<DockviewApi, 'toJSON'>): void`
  - `function restoreLayout(name: string, api: Pick<DockviewApi, 'fromJSON'>): boolean` — true if a stored layout was applied, false (and storage left clean) on missing/corrupt.
  - `const DEFAULT_LAYOUTS: Record<'analyst'|'auditor'|'ops', (api: Pick<DockviewApi,'addPanel'>) => void>` — builders that `addPanel` the 4 panels (component keys `query`/`datasets`/`findings`/`livefeed`).
  - `const LAYOUT_NAMES: Array<'analyst'|'auditor'|'ops'>`

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/layoutStore.test.ts`:
```ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { persistLayout, restoreLayout, DEFAULT_LAYOUTS, LAYOUT_NAMES } from '../layoutStore';

beforeEach(() => localStorage.clear());

describe('layoutStore', () => {
  it('round-trips a layout through localStorage', () => {
    const saved = { grid: { root: 'x' } };
    persistLayout('default', { toJSON: () => saved } as never);
    let restored: unknown = null;
    const ok = restoreLayout('default', { fromJSON: (d: unknown) => { restored = d; } } as never);
    expect(ok).toBe(true);
    expect(restored).toEqual(saved);
  });

  it('returns false when nothing is stored', () => {
    const ok = restoreLayout('default', { fromJSON: () => { throw new Error('should not be called'); } } as never);
    expect(ok).toBe(false);
  });

  it('falls back (returns false) on a corrupt stored value, without throwing', () => {
    localStorage.setItem('aura.terminal.layout.default', '{not json');
    const ok = restoreLayout('default', { fromJSON: () => {} } as never);
    expect(ok).toBe(false);
  });

  it('every default layout builder adds the four panels', () => {
    for (const name of LAYOUT_NAMES) {
      const ids: string[] = [];
      DEFAULT_LAYOUTS[name]({ addPanel: (o: { id: string }) => { ids.push(o.id); return {} as never; } } as never);
      expect(ids).toEqual(expect.arrayContaining(['query', 'datasets', 'findings', 'livefeed']));
    }
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

Run: `npx vitest run src/terminal/__tests__/layoutStore.test.ts`

- [ ] **Step 3: Implement**

`src/terminal/layoutStore.ts`:
```ts
import type { DockviewApi } from 'dockview-react';

const KEY = (name: string) => `aura.terminal.layout.${name}`;

export function persistLayout(name: string, api: Pick<DockviewApi, 'toJSON'>): void {
  try {
    localStorage.setItem(KEY(name), JSON.stringify(api.toJSON()));
  } catch (err) {
    console.warn('[terminal] failed to persist layout', err);
  }
}

export function restoreLayout(name: string, api: Pick<DockviewApi, 'fromJSON'>): boolean {
  const raw = localStorage.getItem(KEY(name));
  if (!raw) return false;
  try {
    api.fromJSON(JSON.parse(raw));
    return true;
  } catch (err) {
    console.warn('[terminal] corrupt saved layout, discarding', err);
    localStorage.removeItem(KEY(name));
    return false;
  }
}

export const LAYOUT_NAMES = ['analyst', 'auditor', 'ops'] as const;

export const DEFAULT_LAYOUTS: Record<
  (typeof LAYOUT_NAMES)[number],
  (api: Pick<DockviewApi, 'addPanel'>) => void
> = {
  analyst: (api) => {
    api.addPanel({ id: 'query', component: 'query', title: 'Query' });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'query', direction: 'right' } });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'query', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'datasets', direction: 'below' } });
  },
  auditor: (api) => {
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings' });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'findings', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'findings', direction: 'below' } });
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed', position: { referencePanel: 'datasets', direction: 'below' } });
  },
  ops: (api) => {
    api.addPanel({ id: 'livefeed', component: 'livefeed', title: 'Live Feed' });
    api.addPanel({ id: 'findings', component: 'findings', title: 'Findings', position: { referencePanel: 'livefeed', direction: 'right' } });
    api.addPanel({ id: 'query', component: 'query', title: 'Query', position: { referencePanel: 'livefeed', direction: 'below' } });
    api.addPanel({ id: 'datasets', component: 'datasets', title: 'Datasets', position: { referencePanel: 'findings', direction: 'below' } });
  },
};
```

Note: this is the first file importing `dockview-react`. Add the dep now so the import type-checks:
```bash
npm install dockview-react@^6.6.1
```

- [ ] **Step 4: Run — expect PASS** (`npx vitest run src/terminal/__tests__/layoutStore.test.ts`)

- [ ] **Step 5: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add package.json package-lock.json src/terminal/layoutStore.ts src/terminal/__tests__/layoutStore.test.ts
git commit -m "feat(terminal): layout persistence + default layouts; add dockview-react dep (S46)"
```

---

## Task 5: Panel registry + four stub panels

**Files:**
- Create: `src/terminal/panels/registry.ts`, `src/terminal/panels/QueryPanel.tsx`, `DatasetsPanel.tsx`, `FindingsPanel.tsx`, `LiveFeedPanel.tsx` (stubs)
- Test: `src/terminal/__tests__/registry.test.ts`

**Interfaces:**
- Consumes: `IDockviewPanelProps` from `dockview-react`; a `lucide-react` icon per panel.
- Produces:
  - `type PanelId = 'query' | 'datasets' | 'findings' | 'livefeed'`
  - `interface PanelDef { title: string; icon: LucideIcon; component: React.LazyExoticComponent<React.FC<IDockviewPanelProps>> }`
  - `const PANEL_REGISTRY: Record<PanelId, PanelDef>`
  - `const PANEL_IDS: PanelId[]`
- Stub panels: each `export default function XPanel(_props: IDockviewPanelProps)` rendering `<div data-testid="<id>-panel">…</div>`.

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/registry.test.ts`:
```ts
import { describe, expect, it } from 'vitest';
import { PANEL_REGISTRY, PANEL_IDS } from '../panels/registry';

describe('panel registry', () => {
  it('exposes the four v1 panels, each with a title, icon, and lazy component', () => {
    expect(PANEL_IDS).toEqual(['query', 'datasets', 'findings', 'livefeed']);
    for (const id of PANEL_IDS) {
      const def = PANEL_REGISTRY[id];
      expect(def.title).toBeTruthy();
      expect(def.icon).toBeTruthy();
      expect(def.component).toBeTruthy();
    }
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Create the four stub panels**

`src/terminal/panels/QueryPanel.tsx`:
```tsx
import type { IDockviewPanelProps } from 'dockview-react';
export default function QueryPanel(_props: IDockviewPanelProps) {
  return <div data-testid="query-panel" className="aura-panel">Query</div>;
}
```
`src/terminal/panels/DatasetsPanel.tsx`:
```tsx
import type { IDockviewPanelProps } from 'dockview-react';
export default function DatasetsPanel(_props: IDockviewPanelProps) {
  return <div data-testid="datasets-panel" className="aura-panel">Datasets</div>;
}
```
`src/terminal/panels/FindingsPanel.tsx`:
```tsx
import type { IDockviewPanelProps } from 'dockview-react';
export default function FindingsPanel(_props: IDockviewPanelProps) {
  return <div data-testid="findings-panel" className="aura-panel">Findings</div>;
}
```
`src/terminal/panels/LiveFeedPanel.tsx`:
```tsx
import type { IDockviewPanelProps } from 'dockview-react';
export default function LiveFeedPanel(_props: IDockviewPanelProps) {
  return <div data-testid="livefeed-panel" className="aura-panel">Live Feed</div>;
}
```

- [ ] **Step 4: Create the registry**

`src/terminal/panels/registry.ts`:
```ts
import { lazy } from 'react';
import { Terminal, Database, ShieldAlert, Activity, type LucideIcon } from 'lucide-react';
import type { IDockviewPanelProps } from 'dockview-react';

export type PanelId = 'query' | 'datasets' | 'findings' | 'livefeed';

export interface PanelDef {
  title: string;
  icon: LucideIcon;
  component: React.LazyExoticComponent<React.FC<IDockviewPanelProps>>;
}

export const PANEL_REGISTRY: Record<PanelId, PanelDef> = {
  query:    { title: 'Query',     icon: Terminal,    component: lazy(() => import('./QueryPanel')) },
  datasets: { title: 'Datasets',  icon: Database,    component: lazy(() => import('./DatasetsPanel')) },
  findings: { title: 'Findings',  icon: ShieldAlert, component: lazy(() => import('./FindingsPanel')) },
  livefeed: { title: 'Live Feed', icon: Activity,    component: lazy(() => import('./LiveFeedPanel')) },
};

export const PANEL_IDS = Object.keys(PANEL_REGISTRY) as PanelId[];
```
(If any of those lucide icon names is not exported by the installed `lucide-react`, substitute a present one — verify with `npx tsc --noEmit`.)

- [ ] **Step 5: Run test + typecheck — expect PASS**

Run: `npx vitest run src/terminal/__tests__/registry.test.ts && npx tsc --noEmit`

- [ ] **Step 6: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/panels src/terminal/__tests__/registry.test.ts
git commit -m "feat(terminal): panel registry + four stub panels (S46)"
```

---

## Task 6: `TerminalWorkspace` shell + route + entry point + test infra

**Files:**
- Create: `src/terminal/TerminalWorkspace.tsx`
- Modify: `src/AppRoutes.tsx` (sibling `/app/terminal/*` route), `src/components/Layout/nav.ts` (nav item), `src/components/CommandPalette.tsx` ("Open Terminal" command), `src/test/setup.ts` (ResizeObserver stub)
- Test: `src/terminal/__tests__/TerminalWorkspace.test.tsx`

**Interfaces:**
- Consumes: `PANEL_REGISTRY`, `PanelErrorBoundary`, `CockpitProvider`, `restoreLayout`, `DEFAULT_LAYOUTS`, `persistLayout`; `DockviewReact`, `DockviewReadyEvent` from `dockview-react`.
- Produces: `export function TerminalWorkspace(): JSX.Element` (default export too) — full-viewport cockpit.

- [ ] **Step 1: Add the ResizeObserver stub to `src/test/setup.ts`** (dockview/jsdom needs it even when mocked downstream):
```ts
if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class {
    observe() {} unobserve() {} disconnect() {}
  } as unknown as typeof ResizeObserver;
}
```

- [ ] **Step 2: Write the failing test** (mock `dockview-react` — jsdom can't run its real DOM measurement; we verify OUR wiring: default layout builds 4 panels, persistence is attached)

`src/terminal/__tests__/TerminalWorkspace.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

// Mock dockview-react: capture onReady, expose a fake api.
const added: string[] = [];
let layoutChangeCb: (() => void) | null = null;
vi.mock('dockview-react', () => ({
  DockviewReact: (props: { onReady: (e: unknown) => void }) => {
    const api = {
      addPanel: (o: { id: string }) => { added.push(o.id); return {}; },
      fromJSON: () => {},
      toJSON: () => ({}),
      onDidLayoutChange: (cb: () => void) => { layoutChangeCb = cb; return { dispose() {} }; },
    };
    props.onReady({ api });
    return <div data-testid="dockview-mock" />;
  },
}));

import { TerminalWorkspace } from '../TerminalWorkspace';

describe('TerminalWorkspace', () => {
  it('mounts dockview and builds the default 4-panel layout', () => {
    added.length = 0;
    render(<MemoryRouter><TerminalWorkspace /></MemoryRouter>);
    expect(screen.getByTestId('dockview-mock')).toBeInTheDocument();
    expect(added).toEqual(expect.arrayContaining(['query', 'datasets', 'findings', 'livefeed']));
    expect(layoutChangeCb).toBeTypeOf('function'); // persistence wired
  });
});
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Implement `TerminalWorkspace.tsx`**

```tsx
import { Suspense, useCallback, useMemo, useRef } from 'react';
import { DockviewReact, type DockviewReadyEvent, type IDockviewPanelProps, type DockviewApi } from 'dockview-react';
import 'dockview-react/dist/styles/dockview.css';
import { CockpitProvider } from './CockpitProvider';
import { PanelErrorBoundary } from './PanelErrorBoundary';
import { PANEL_REGISTRY, type PanelId } from './panels/registry';
import { persistLayout, restoreLayout, DEFAULT_LAYOUTS } from './layoutStore';
import './terminal.css';

const LAYOUT_KEY = 'default';

function buildComponents(): Record<string, React.FC<IDockviewPanelProps>> {
  const out: Record<string, React.FC<IDockviewPanelProps>> = {};
  (Object.keys(PANEL_REGISTRY) as PanelId[]).forEach((id) => {
    const def = PANEL_REGISTRY[id];
    const Lazy = def.component;
    const Wrapped: React.FC<IDockviewPanelProps> = (props) => (
      <PanelErrorBoundary panelTitle={def.title}>
        <Suspense fallback={<div className="panel-loading">Loading…</div>}>
          <Lazy {...props} />
        </Suspense>
      </PanelErrorBoundary>
    );
    out[id] = Wrapped;
  });
  return out;
}

export function TerminalWorkspace() {
  const apiRef = useRef<DockviewApi | null>(null);
  const components = useMemo(buildComponents, []);

  const onReady = useCallback((event: DockviewReadyEvent) => {
    apiRef.current = event.api;
    const restored = restoreLayout(LAYOUT_KEY, event.api);
    if (!restored) DEFAULT_LAYOUTS.analyst(event.api);
    event.api.onDidLayoutChange(() => persistLayout(LAYOUT_KEY, event.api));
  }, []);

  return (
    <CockpitProvider>
      <div className="aura-terminal" data-testid="terminal-workspace">
        <DockviewReact
          className="dockview-theme-dark"
          components={components}
          onReady={onReady}
        />
      </div>
    </CockpitProvider>
  );
}

export default TerminalWorkspace;
```

- [ ] **Step 5: Wire the route in `src/AppRoutes.tsx`** — add a lazy import and a sibling route **before** the `/app/*` catch-all:
```tsx
const TerminalWorkspace = lazy(() => import('./terminal/TerminalWorkspace'));
// …inside <Routes>, above the /app/* route:
<Route path="/app/terminal/*" element={
  <ProtectedRoute>
    <Suspense fallback={<div>Loading…</div>}><TerminalWorkspace /></Suspense>
  </ProtectedRoute>
} />
```

- [ ] **Step 6: Add the entry points**

In `src/components/Layout/nav.ts`, add a nav item (match the existing item shape) pointing to the terminal — e.g. an item whose action navigates to `/app/terminal` (follow the file's existing item structure; if items use a `page`/`onItemClick` model, add a dedicated handler that calls `navigate('/app/terminal')`). In `src/components/CommandPalette.tsx`, add one command to the action group: `{ id: 'open-terminal', label: 'Open Terminal', group: 'action', run: () => { window.location.assign('/app/terminal'); } }` (or the file's existing navigation mechanism if it exposes one).

- [ ] **Step 7: Run the workspace test + full suite + typecheck**

Run: `npx vitest run src/terminal && npx tsc --noEmit`
Expected: PASS. Then `npx vitest run` (whole suite) — existing tests still green.

- [ ] **Step 8: Lint + commit**

```bash
npx eslint src --max-warnings 0
git add src/terminal/TerminalWorkspace.tsx src/terminal/__tests__/TerminalWorkspace.test.tsx src/AppRoutes.tsx src/components/Layout/nav.ts src/components/CommandPalette.tsx src/test/setup.ts
git commit -m "feat(terminal): dockview workspace shell + /app/terminal route + entry points (S46)"
```

---

## Task 7: `CockpitTopBar`

**Files:**
- Create: `src/terminal/CockpitTopBar.tsx`
- Modify: `src/terminal/TerminalWorkspace.tsx` (render the top bar above dockview; pass a layout-apply callback + ⌘K open + back)
- Test: `src/terminal/__tests__/CockpitTopBar.test.tsx`

**Interfaces:**
- Produces: `function CockpitTopBar(props: { onApplyLayout: (name: 'analyst'|'auditor'|'ops') => void; onOpenPalette: () => void; onBack: () => void }): JSX.Element` — renders a layout `<select>`/buttons (one per `LAYOUT_NAMES`), a `⌘K` button, and a "← Back to app" button.
- Consumes: `LAYOUT_NAMES` from `layoutStore`.

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/CockpitTopBar.test.tsx`:
```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { CockpitTopBar } from '../CockpitTopBar';

describe('CockpitTopBar', () => {
  it('applies a layout, opens the palette, and goes back', () => {
    const onApplyLayout = vi.fn();
    const onOpenPalette = vi.fn();
    const onBack = vi.fn();
    render(<CockpitTopBar onApplyLayout={onApplyLayout} onOpenPalette={onOpenPalette} onBack={onBack} />);
    fireEvent.click(screen.getByTestId('layout-auditor'));
    expect(onApplyLayout).toHaveBeenCalledWith('auditor');
    fireEvent.click(screen.getByTestId('open-palette'));
    expect(onOpenPalette).toHaveBeenCalled();
    fireEvent.click(screen.getByTestId('back-to-app'));
    expect(onBack).toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `CockpitTopBar.tsx`**

```tsx
import { LAYOUT_NAMES } from './layoutStore';

interface Props {
  onApplyLayout: (name: (typeof LAYOUT_NAMES)[number]) => void;
  onOpenPalette: () => void;
  onBack: () => void;
}

export function CockpitTopBar({ onApplyLayout, onOpenPalette, onBack }: Props) {
  return (
    <header className="cockpit-topbar" data-testid="cockpit-topbar">
      <span className="cockpit-brand">AURA · Terminal</span>
      <nav className="cockpit-layouts">
        {LAYOUT_NAMES.map((name) => (
          <button key={name} data-testid={`layout-${name}`} onClick={() => onApplyLayout(name)}>
            {name[0].toUpperCase() + name.slice(1)}
          </button>
        ))}
      </nav>
      <div className="cockpit-actions">
        <button data-testid="open-palette" onClick={onOpenPalette}>⌘K</button>
        <button data-testid="back-to-app" onClick={onBack}>← Back to app</button>
      </div>
    </header>
  );
}
```

- [ ] **Step 4: Wire it into `TerminalWorkspace`** — render `<CockpitTopBar>` above `<DockviewReact>`, with `onApplyLayout={(n) => apiRef.current && (apiRef.current.clear(), DEFAULT_LAYOUTS[n](apiRef.current))}`, `onOpenPalette={() => setPaletteOpen(true)}` (add `const [paletteOpen, setPaletteOpen] = useState(false)`; palette itself lands in Task 12 — for now `onOpenPalette` can toggle the state), and `onBack={() => navigate('/app')}` (`import { useNavigate } from 'react-router-dom'`). Use `apiRef.current.clear()` before applying a default layout so panels don't duplicate (verify `clear()` exists on `DockviewApi`; if not, remove existing panels via `api.panels.forEach(p => api.removePanel(p))`).

- [ ] **Step 5: Run topbar test + workspace test + typecheck — expect PASS**

Run: `npx vitest run src/terminal && npx tsc --noEmit`

- [ ] **Step 6: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/CockpitTopBar.tsx src/terminal/__tests__/CockpitTopBar.test.tsx src/terminal/TerminalWorkspace.tsx
git commit -m "feat(terminal): cockpit top bar (layout switch · palette · back) (S46)"
```

---

## Task 8: `QueryPanel` (real) — chatService + reads activeDataset

**Files:**
- Modify: `src/terminal/panels/QueryPanel.tsx`
- Test: `src/terminal/__tests__/QueryPanel.test.tsx`

**Interfaces:**
- Consumes: `chatService.sendMessage(message, { uploadedFile? }) => Promise<QueryResponse>` from `../../services/api`; `useCockpit()` for `activeDataset`. `QueryResponse.final_query`, `QueryResponse.execution_result?.{columns?, rows?}`.

- [ ] **Step 1: Write the failing test** (mock the service + the cockpit hook)

`src/terminal/__tests__/QueryPanel.test.tsx`:
```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const sendMessage = vi.fn();
vi.mock('../../services/api', () => ({ chatService: { sendMessage: (...a: unknown[]) => sendMessage(...a) } }));
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: 'sales.csv', setActiveDataset: () => {} }) }));

import QueryPanel from '../panels/QueryPanel';

describe('QueryPanel', () => {
  it('sends the prompt scoped to the active dataset and renders the SQL + rows', async () => {
    sendMessage.mockResolvedValue({
      job_id: 'j1', status: 'Success', final_query: 'SELECT 1',
      execution_result: { success: true, columns: ['n'], rows: [[1]] },
    });
    render(<QueryPanel api={{} as never} params={{} as never} containerApi={{} as never} group={{} as never} />);
    fireEvent.change(screen.getByTestId('query-input'), { target: { value: 'total revenue' } });
    fireEvent.click(screen.getByTestId('query-run'));
    await waitFor(() => expect(sendMessage).toHaveBeenCalledWith('total revenue', { uploadedFile: 'sales.csv' }));
    expect(await screen.findByText('SELECT 1')).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `QueryPanel.tsx`**

```tsx
import { useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { chatService, type QueryResponse } from '../../services/api';
import { useCockpit } from '../CockpitProvider';

export default function QueryPanel(_props: IDockviewPanelProps) {
  const { activeDataset } = useCockpit();
  const [prompt, setPrompt] = useState('');
  const [result, setResult] = useState<QueryResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    if (!prompt.trim()) return;
    setBusy(true); setError(null);
    try {
      const res = await chatService.sendMessage(
        prompt,
        activeDataset ? { uploadedFile: activeDataset } : undefined,
      );
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Query failed');
    } finally {
      setBusy(false);
    }
  };

  const er = result?.execution_result;
  return (
    <div data-testid="query-panel" className="aura-panel query-panel">
      {activeDataset && <div className="panel-context">dataset: {activeDataset}</div>}
      <div className="query-bar">
        <input data-testid="query-input" value={prompt}
               onChange={(e) => setPrompt(e.target.value)}
               onKeyDown={(e) => { if (e.key === 'Enter') run(); }}
               placeholder="Ask a question…" />
        <button data-testid="query-run" onClick={run} disabled={busy}>{busy ? '…' : 'Run'}</button>
      </div>
      {error && <div className="panel-error-inline">{error}</div>}
      {result?.final_query && <pre className="query-sql">{result.final_query}</pre>}
      {er?.columns && er.rows && (
        <table className="query-table">
          <thead><tr>{er.columns.map((c) => <th key={c}>{c}</th>)}</tr></thead>
          <tbody>
            {er.rows.slice(0, 100).map((row, i) => (
              <tr key={i}>{row.map((v, j) => <td key={j}>{String(v)}</td>)}</tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS** (`npx vitest run src/terminal/__tests__/QueryPanel.test.tsx`)

- [ ] **Step 5: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/panels/QueryPanel.tsx src/terminal/__tests__/QueryPanel.test.tsx
git commit -m "feat(terminal): Query panel (chatService, scoped to active dataset) (S46)"
```

---

## Task 9: `DatasetsPanel` (real) — uploadService + sets activeDataset

**Files:**
- Modify: `src/terminal/panels/DatasetsPanel.tsx`
- Test: `src/terminal/__tests__/DatasetsPanel.test.tsx`

**Interfaces:**
- Consumes: `uploadService.getUploadedFiles(): Promise<Array<{ filename: string; size: number; modified: string }>>` (verified, `api.ts` ~line 898); `useCockpit().setActiveDataset`.

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/DatasetsPanel.test.tsx`:
```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const getUploadedFiles = vi.fn();
vi.mock('../../services/api', () => ({ uploadService: { getUploadedFiles: () => getUploadedFiles() } }));
const setActiveDataset = vi.fn();
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: null, setActiveDataset }) }));

import DatasetsPanel from '../panels/DatasetsPanel';

describe('DatasetsPanel', () => {
  it('lists datasets and sets the active dataset on row click', async () => {
    getUploadedFiles.mockResolvedValue([
      { filename: 'sales.csv', size: 10, modified: 'now' },
      { filename: 'orders.csv', size: 20, modified: 'now' },
    ]);
    render(<DatasetsPanel api={{} as never} params={{} as never} containerApi={{} as never} group={{} as never} />);
    await waitFor(() => expect(screen.getByText('sales.csv')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('dataset-row-sales.csv'));
    expect(setActiveDataset).toHaveBeenCalledWith('sales.csv');
  });
});
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Implement `DatasetsPanel.tsx`**

```tsx
import { useEffect, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { uploadService } from '../../services/api';
import { useCockpit } from '../CockpitProvider';

interface DatasetFile { filename: string; size: number; modified: string }

export default function DatasetsPanel(_props: IDockviewPanelProps) {
  const { activeDataset, setActiveDataset } = useCockpit();
  const [files, setFiles] = useState<DatasetFile[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    uploadService.getUploadedFiles()
      .then((f) => { if (alive) setFiles(f); })
      .catch((e) => { if (alive) setError(e instanceof Error ? e.message : 'Failed to load datasets'); });
    return () => { alive = false; };
  }, []);

  if (error) return <div data-testid="datasets-panel" className="aura-panel panel-error-inline">{error}</div>;
  return (
    <div data-testid="datasets-panel" className="aura-panel datasets-panel">
      <table className="datasets-table">
        <thead><tr><th>Dataset</th><th>Size</th></tr></thead>
        <tbody>
          {files.map((f) => (
            <tr key={f.filename}
                data-testid={`dataset-row-${f.filename}`}
                className={f.filename === activeDataset ? 'is-active' : ''}
                onClick={() => setActiveDataset(f.filename)}>
              <td>{f.filename}</td><td>{f.size}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
```

- [ ] **Step 5: Run — expect PASS**

- [ ] **Step 6: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/panels/DatasetsPanel.tsx src/terminal/__tests__/DatasetsPanel.test.tsx
git commit -m "feat(terminal): Datasets panel (sets active dataset = cross-filter source) (S46)"
```

---

## Task 10: `FindingsPanel` (real) — run sample audit → findings, filtered by activeDataset

**Files:**
- Create: `src/audit/sampleAuditBatch.ts` (extracted shared sample ledger)
- Modify: `src/components/HITL/ExceptionQueue.tsx` (import the extracted batch — behavior identical), `src/terminal/panels/FindingsPanel.tsx`
- Test: `src/terminal/__tests__/FindingsPanel.test.tsx`

**Interfaces (verified):**
- There is **no list-findings endpoint.** Findings are produced by running an audit:
  `financialAuditService.ensureAuditorToken(): Promise<void>` then
  `financialAuditService.runAudit(payload): Promise<FinancialAuditReport>` where
  `FinancialAuditReport = { record_hash, signature_status, n_findings, findings: AuditFinding[], … }`.
  This is exactly what `src/components/HITL/ExceptionQueue.tsx` does (its `SAMPLE_BATCH` + `runAudit`).
- `AuditFinding = { finding_id, pcaob_standard, risk_level, description, evidence_payload, requires_human_review }`. `useCockpit().activeDataset`.
- Cross-filter: when `activeDataset` is set, show only findings whose `description` or
  `JSON.stringify(evidence_payload)` contains the filename (case-insensitive) — the documented v1
  best-effort link (AuditFinding has no dataset FK).
- The panel renders a "Run sample audit" button (mirrors the workbench's run action); on click it
  loads `report.findings`. No auto-run on mount (an audit is a real call).

- [ ] **Step 1: Extract the sample batch.** In `src/components/HITL/ExceptionQueue.tsx`, the local
  `const SAMPLE_BATCH = { … }` (the investor-demo canned ledger, ~lines 25–62) is moved verbatim into
  a new `src/audit/sampleAuditBatch.ts` as `export const SAMPLE_AUDIT_BATCH = { … };`, and
  `ExceptionQueue.tsx` imports it (`import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';`)
  and uses `SAMPLE_AUDIT_BATCH` where it used `SAMPLE_BATCH`. Behavior is identical — verify the
  existing ExceptionQueue test (if any) still passes: `npx vitest run src/components/HITL`.

- [ ] **Step 2: Write the failing test**

`src/terminal/__tests__/FindingsPanel.test.tsx`:
```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const ensureAuditorToken = vi.fn().mockResolvedValue(undefined);
const runAudit = vi.fn();
vi.mock('../../services/api', () => ({
  financialAuditService: { ensureAuditorToken: () => ensureAuditorToken(), runAudit: () => runAudit() },
}));
vi.mock('../../audit/sampleAuditBatch', () => ({ SAMPLE_AUDIT_BATCH: { tenant_id: 'demo' } }));
let active: string | null = null;
vi.mock('../CockpitProvider', () => ({ useCockpit: () => ({ activeDataset: active, setActiveDataset: () => {} }) }));

import FindingsPanel from '../panels/FindingsPanel';

const REPORT = {
  record_hash: 'abc', signature_status: 'signed', n_findings: 2,
  findings: [
    { finding_id: 'f1', pcaob_standard: 'AS-2401', risk_level: 'High', description: 'anomaly in sales.csv', evidence_payload: {}, requires_human_review: true },
    { finding_id: 'f2', pcaob_standard: 'AS-2201', risk_level: 'Low', description: 'orders mismatch', evidence_payload: {}, requires_human_review: false },
  ],
};
const props = { api: {}, params: {}, containerApi: {}, group: {} } as never;

describe('FindingsPanel', () => {
  it('runs a sample audit, lists findings, and filters by active dataset', async () => {
    active = null;
    runAudit.mockResolvedValue(REPORT);
    const { rerender } = render(<FindingsPanel {...props} />);
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument());
    expect(screen.getByText(/orders mismatch/)).toBeInTheDocument();

    active = 'sales.csv';
    rerender(<FindingsPanel {...props} />);
    await waitFor(() => expect(screen.queryByText(/orders mismatch/)).not.toBeInTheDocument());
    expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 3: Run — expect FAIL**

- [ ] **Step 4: Implement `FindingsPanel.tsx`**

```tsx
import { useMemo, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { financialAuditService, type AuditFinding } from '../../services/api';
import { SAMPLE_AUDIT_BATCH } from '../../audit/sampleAuditBatch';
import { useCockpit } from '../CockpitProvider';

export default function FindingsPanel(_props: IDockviewPanelProps) {
  const { activeDataset } = useCockpit();
  const [findings, setFindings] = useState<AuditFinding[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const run = async () => {
    setBusy(true); setError(null);
    try {
      await financialAuditService.ensureAuditorToken();
      const report = await financialAuditService.runAudit(SAMPLE_AUDIT_BATCH);
      setFindings(report.findings ?? []);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Audit failed');
    } finally {
      setBusy(false);
    }
  };

  const shown = useMemo(() => {
    if (!activeDataset) return findings;
    const needle = activeDataset.toLowerCase();
    return findings.filter((f) =>
      f.description.toLowerCase().includes(needle) ||
      JSON.stringify(f.evidence_payload).toLowerCase().includes(needle),
    );
  }, [findings, activeDataset]);

  return (
    <div data-testid="findings-panel" className="aura-panel findings-panel">
      <div className="findings-bar">
        <button data-testid="findings-run" onClick={run} disabled={busy}>
          {busy ? 'Running…' : 'Run sample audit'}
        </button>
        {activeDataset && <span className="panel-context">filtered: {activeDataset}</span>}
      </div>
      {error && <div className="panel-error-inline">{error}</div>}
      <ul className="findings-list">
        {shown.map((f) => (
          <li key={f.finding_id} className={`finding risk-${String(f.risk_level).toLowerCase()}`}>
            <span className="finding-std">{f.pcaob_standard}</span>
            <span className="finding-risk">{f.risk_level}</span>
            <span className="finding-desc">{f.description}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```
Note: `runAudit(SAMPLE_AUDIT_BATCH)` takes the batch payload; the test mock ignores the arg, which is fine.

- [ ] **Step 5: Run — expect PASS** (`npx vitest run src/terminal/__tests__/FindingsPanel.test.tsx`)

- [ ] **Step 6: Lint + commit**

```bash
npx eslint src/terminal src/components/HITL/ExceptionQueue.tsx src/audit/sampleAuditBatch.ts --max-warnings 0
git add src/audit/sampleAuditBatch.ts src/components/HITL/ExceptionQueue.tsx src/terminal/panels/FindingsPanel.tsx src/terminal/__tests__/FindingsPanel.test.tsx
git commit -m "feat(terminal): Findings panel (run sample audit → findings, cross-filtered) (S46)"
```

---

## Task 11: `LiveFeedPanel` (real) — useSSE feed

**Files:**
- Modify: `src/terminal/panels/LiveFeedPanel.tsx`
- Test: `src/terminal/__tests__/LiveFeedPanel.test.tsx`

**Interfaces:**
- Consumes: `useSSE({ topic, onEvent? }) => { lastEvent, connected, error }` from `../../hooks/useSSE`; `SSEEvent = { id, type, topic, payload, timestamp }`. The panel accumulates events into a newest-first feed.

- [ ] **Step 1: Write the failing test** (mock `useSSE` to drive an event via `onEvent`)

`src/terminal/__tests__/LiveFeedPanel.test.tsx`:
```tsx
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

let capturedOnEvent: ((e: { id: string; type: string; topic: string; payload: unknown; timestamp: string }) => void) | null = null;
vi.mock('../../hooks/useSSE', () => ({
  useSSE: (opts: { onEvent?: (e: unknown) => void }) => {
    capturedOnEvent = opts.onEvent ?? null;
    return { lastEvent: null, connected: true, error: null };
  },
}));

import LiveFeedPanel from '../panels/LiveFeedPanel';

describe('LiveFeedPanel', () => {
  it('shows a connected feed and renders incoming events newest-first', () => {
    render(<LiveFeedPanel api={{} as never} params={{} as never} containerApi={{} as never} group={{} as never} />);
    expect(screen.getByTestId('livefeed-panel')).toBeInTheDocument();
    capturedOnEvent?.({ id: '1', type: 'progress', topic: 'system:health', payload: { msg: 'healthy' }, timestamp: 't1' });
    expect(screen.getByText(/system:health/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `LiveFeedPanel.tsx`**

```tsx
import { useCallback, useState } from 'react';
import type { IDockviewPanelProps } from 'dockview-react';
import { useSSE, type SSEEvent } from '../../hooks/useSSE';

const FEED_LIMIT = 200;

export default function LiveFeedPanel(_props: IDockviewPanelProps) {
  const [events, setEvents] = useState<SSEEvent[]>([]);
  const onEvent = useCallback((e: SSEEvent) => {
    setEvents((prev) => [e, ...prev].slice(0, FEED_LIMIT));
  }, []);
  const { connected, error } = useSSE({ topic: 'system:health', onEvent });

  return (
    <div data-testid="livefeed-panel" className="aura-panel livefeed-panel">
      <div className={`feed-status ${connected ? 'is-on' : 'is-off'}`}>
        {connected ? '● live' : '○ offline'}{error ? ' · error' : ''}
      </div>
      <ul className="feed-list">
        {events.map((e) => (
          <li key={e.id} className={`feed-item type-${e.type}`}>
            <span className="feed-ts">{e.timestamp}</span>
            <span className="feed-topic">{e.topic}</span>
            <span className="feed-payload">{JSON.stringify(e.payload)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
```

- [ ] **Step 4: Run — expect PASS**

- [ ] **Step 5: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/panels/LiveFeedPanel.tsx src/terminal/__tests__/LiveFeedPanel.test.tsx
git commit -m "feat(terminal): Live monitoring feed panel (useSSE) (S46)"
```

---

## Task 12: `TerminalCommandPalette` + command registry

**Files:**
- Create: `src/terminal/commands.ts`, `src/terminal/TerminalCommandPalette.tsx`
- Modify: `src/terminal/TerminalWorkspace.tsx` (render the palette; pass real handlers)
- Test: `src/terminal/__tests__/TerminalCommandPalette.test.tsx`

**Interfaces:**
- Produces:
  - `interface TerminalCommand { id: string; label: string; group: 'panel'|'layout'|'action'; run: () => void }`
  - `function buildTerminalCommands(handlers: { openPanel: (id: PanelId) => void; applyLayout: (n: 'analyst'|'auditor'|'ops') => void; resetLayout: () => void; back: () => void }): TerminalCommand[]`
  - `function TerminalCommandPalette({ open, onClose, commands }: { open: boolean; onClose: () => void; commands: TerminalCommand[] }): JSX.Element | null`
- Consumes: `fuzzyScore` from `../utils/fuzzyScore`; `PANEL_REGISTRY`/`PanelId`; `LAYOUT_NAMES`.

- [ ] **Step 1: Write the failing test**

`src/terminal/__tests__/TerminalCommandPalette.test.tsx`:
```tsx
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { TerminalCommandPalette } from '../TerminalCommandPalette';
import { buildTerminalCommands } from '../commands';

describe('TerminalCommandPalette', () => {
  it('fuzzy-filters and runs the top command on Enter', () => {
    const openPanel = vi.fn();
    const commands = buildTerminalCommands({ openPanel, applyLayout: vi.fn(), resetLayout: vi.fn(), back: vi.fn() });
    render(<TerminalCommandPalette open={true} onClose={() => {}} commands={commands} />);
    fireEvent.change(screen.getByTestId('palette-input'), { target: { value: 'findings' } });
    fireEvent.keyDown(screen.getByTestId('palette-input'), { key: 'Enter' });
    expect(openPanel).toHaveBeenCalledWith('findings');
  });

  it('renders nothing when closed', () => {
    const commands = buildTerminalCommands({ openPanel: vi.fn(), applyLayout: vi.fn(), resetLayout: vi.fn(), back: vi.fn() });
    const { container } = render(<TerminalCommandPalette open={false} onClose={() => {}} commands={commands} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run — expect FAIL**

- [ ] **Step 3: Implement `commands.ts`**

```ts
import { PANEL_REGISTRY, type PanelId } from './panels/registry';
import { LAYOUT_NAMES } from './layoutStore';

export interface TerminalCommand { id: string; label: string; group: 'panel' | 'layout' | 'action'; run: () => void }

export function buildTerminalCommands(handlers: {
  openPanel: (id: PanelId) => void;
  applyLayout: (n: (typeof LAYOUT_NAMES)[number]) => void;
  resetLayout: () => void;
  back: () => void;
}): TerminalCommand[] {
  const panelCmds: TerminalCommand[] = (Object.keys(PANEL_REGISTRY) as PanelId[]).map((id) => ({
    id: `open-${id}`, label: `Open ${PANEL_REGISTRY[id].title}`, group: 'panel', run: () => handlers.openPanel(id),
  }));
  const layoutCmds: TerminalCommand[] = LAYOUT_NAMES.map((n) => ({
    id: `layout-${n}`, label: `Layout: ${n}`, group: 'layout', run: () => handlers.applyLayout(n),
  }));
  return [
    ...panelCmds,
    ...layoutCmds,
    { id: 'reset-layout', label: 'Reset layout', group: 'action', run: handlers.resetLayout },
    { id: 'back', label: 'Back to app', group: 'action', run: handlers.back },
  ];
}
```

- [ ] **Step 4: Implement `TerminalCommandPalette.tsx`**

```tsx
import { useEffect, useMemo, useRef, useState } from 'react';
import { fuzzyScore } from '../utils/fuzzyScore';
import type { TerminalCommand } from './commands';

interface Props { open: boolean; onClose: () => void; commands: TerminalCommand[] }

export function TerminalCommandPalette({ open, onClose, commands }: Props) {
  const [query, setQuery] = useState('');
  const [active, setActive] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => { if (open) { setQuery(''); setActive(0); inputRef.current?.focus(); } }, [open]);

  const ranked = useMemo(() => {
    return commands
      .map((c) => ({ c, score: fuzzyScore(c.label, query) }))
      .filter((x) => x.score > 0)
      .sort((a, b) => b.score - a.score)
      .map((x) => x.c);
  }, [commands, query]);

  if (!open) return null;

  const run = (cmd?: TerminalCommand) => { cmd?.run(); onClose(); };

  return (
    <div className="palette-overlay" data-testid="terminal-palette" onClick={onClose}>
      <div className="palette" onClick={(e) => e.stopPropagation()}>
        <input
          ref={inputRef}
          data-testid="palette-input"
          value={query}
          placeholder="Type a command…"
          onChange={(e) => { setQuery(e.target.value); setActive(0); }}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((i) => Math.min(i + 1, ranked.length - 1)); }
            else if (e.key === 'ArrowUp') { e.preventDefault(); setActive((i) => Math.max(i - 1, 0)); }
            else if (e.key === 'Enter') { run(ranked[active]); }
            else if (e.key === 'Escape') { onClose(); }
          }}
        />
        <ul className="palette-list">
          {ranked.map((c, i) => (
            <li key={c.id} className={i === active ? 'is-active' : ''} onClick={() => run(c)}>
              <span className="palette-group">{c.group}</span> {c.label}
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Wire into `TerminalWorkspace`** — build commands with real handlers (`openPanel: (id) => apiRef.current?.addPanel({ id, component: id, title: PANEL_REGISTRY[id].title })` guarded so it focuses an existing panel via `apiRef.current?.getPanel(id)?.api.setActive()` if already open; `applyLayout` / `resetLayout` clear+rebuild as in Task 7; `back` navigates `/app`). Render `<TerminalCommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />`. Add a global `⌘/Ctrl-K` listener that `setPaletteOpen(true)` (mirror the existing `CommandPalette.tsx` shortcut handler).

- [ ] **Step 6: Run palette tests + workspace test + typecheck — expect PASS**

Run: `npx vitest run src/terminal && npx tsc --noEmit`

- [ ] **Step 7: Lint + commit**

```bash
npx eslint src/terminal --max-warnings 0
git add src/terminal/commands.ts src/terminal/TerminalCommandPalette.tsx src/terminal/__tests__/TerminalCommandPalette.test.tsx src/terminal/TerminalWorkspace.tsx
git commit -m "feat(terminal): cockpit command palette (⌘K) + command registry (S46)"
```

---

## Task 13: High-density terminal theme + cross-filter integration test

**Files:**
- Create: `src/terminal/terminal.css`
- Test: `src/terminal/__tests__/crossfilter.integration.test.tsx`

**Interfaces:** none new — this task adds the skin and proves the end-to-end cross-filter through real components.

- [ ] **Step 1: Write the cross-filter integration test** (Datasets + Findings under ONE real `CockpitProvider`; selecting a dataset filters Findings — no mock of `useCockpit`, only the services)

`src/terminal/__tests__/crossfilter.integration.test.tsx`:
```tsx
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

const getUploadedFiles = vi.fn();
const ensureAuditorToken = vi.fn().mockResolvedValue(undefined);
const runAudit = vi.fn();
vi.mock('../../services/api', () => ({
  uploadService: { getUploadedFiles: () => getUploadedFiles() },
  financialAuditService: { ensureAuditorToken: () => ensureAuditorToken(), runAudit: () => runAudit() },
}));
vi.mock('../../audit/sampleAuditBatch', () => ({ SAMPLE_AUDIT_BATCH: { tenant_id: 'demo' } }));

import { CockpitProvider } from '../CockpitProvider';
import DatasetsPanel from '../panels/DatasetsPanel';
import FindingsPanel from '../panels/FindingsPanel';

const panelProps = { api: {}, params: {}, containerApi: {}, group: {} } as never;

describe('cross-filter integration', () => {
  it('selecting a dataset in Datasets filters the Findings panel', async () => {
    getUploadedFiles.mockResolvedValue([{ filename: 'sales.csv', size: 1, modified: 'now' }]);
    runAudit.mockResolvedValue({
      record_hash: 'abc', signature_status: 'signed', n_findings: 2,
      findings: [
        { finding_id: 'f1', pcaob_standard: 'AS-2401', risk_level: 'High', description: 'anomaly in sales.csv', evidence_payload: {}, requires_human_review: true },
        { finding_id: 'f2', pcaob_standard: 'AS-2201', risk_level: 'Low', description: 'orders mismatch', evidence_payload: {}, requires_human_review: false },
      ],
    });
    render(
      <CockpitProvider>
        <DatasetsPanel {...panelProps} />
        <FindingsPanel {...panelProps} />
      </CockpitProvider>,
    );
    // load findings (panel does not auto-run an audit)
    fireEvent.click(screen.getByTestId('findings-run'));
    await waitFor(() => expect(screen.getByText(/orders mismatch/)).toBeInTheDocument());
    // cross-filter: pick a dataset → Findings narrows to matches
    fireEvent.click(screen.getByTestId('dataset-row-sales.csv'));
    await waitFor(() => expect(screen.queryByText(/orders mismatch/)).not.toBeInTheDocument());
    expect(screen.getByText(/anomaly in sales.csv/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run — expect PASS already** (the panels were built in Tasks 9–10; this proves they compose). If it fails, fix the panel that breaks the contract.

- [ ] **Step 3: Add `terminal.css`** — the high-density overlay, scoped to `.aura-terminal`. Include: full-viewport layout (`.aura-terminal { position: fixed; inset: 0; display: flex; flex-direction: column; background: var(--bg-base); }`), the top bar, dense tables (`.query-table, .datasets-table { font-family: 'JetBrains Mono', monospace; font-size: 12px; }`), tabular-nums, panel padding, the palette overlay, the feed list, error-card styling, and skin the dockview theme variables to AURA tokens (override `--dv-*` vars under `.aura-terminal` to map onto `--bg-elevated`, `--border-default`, `--accent`). Keep all rules under `.aura-terminal` so nothing leaks globally. Import is already added in `TerminalWorkspace.tsx` (Task 6).

- [ ] **Step 4: Visually sanity-check** — run `npm run dev`, log in, navigate to `/app/terminal`: the four panels render, are draggable/splittable, the layout switcher + ⌘K palette work, selecting a dataset filters Findings, the live feed shows the connection state. (Manual; not a blocking automated step.)

- [ ] **Step 5: Full verification + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run
git add src/terminal/terminal.css src/terminal/__tests__/crossfilter.integration.test.tsx
git commit -m "feat(terminal): high-density theme overlay + cross-filter integration test (S46)"
```

---

## Final verification (before PR)

- [ ] `cd frontend && npx tsc --noEmit` — clean.
- [ ] `npx eslint src --max-warnings 0` — clean.
- [ ] `npx vitest run` — whole suite green (new terminal specs + all existing tests).
- [ ] `npm run build` — production build succeeds (this is what CI's frontend-typecheck runs).
- [ ] Manual: `/app/terminal` works end-to-end; the existing `/app` pages are unchanged.
- [ ] Open the PR (controller, not a subagent), closes #120; note this is Phase 1 and link the spec.
