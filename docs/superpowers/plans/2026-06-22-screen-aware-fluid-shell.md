# S49 Screen-Aware Fluid Shell — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the S48 1600px content cap with a screen-aware fluid shell — one `ViewportProvider` anchor drives a fluid grid layout and a page-aware right rail that reclaims the dead gap on wide screens.

**Architecture:** A `ViewportProvider` context (the "anchor") derives a `ScreenClass` from viewport width and exposes `hasRail`/`sidebarMode`. `AppLayout` becomes a CSS grid keyed off `data-screen`/`data-rail` attributes; the formerly-capped width becomes a `<ContextRail>` column whose contents are page-aware via a registry. Content grids pack fluidly (capped `auto-fit`) instead of stretching.

**Tech Stack:** React 19, TypeScript, Vite, Vitest + @testing-library/react, plain CSS with design-system.css tokens.

## Global Constraints

- Branch `feature/s49-screen-aware-shell`; additive — existing app + all tests stay green.
- Frontend gates before every commit that touches `src/`: `npx tsc --noEmit`, `npx eslint src --max-warnings 0`, `npx vitest run`, `npm run build` (the real CI gate).
- `useViewport()` returns a safe default (`standard`, width 1280) when called outside a provider OR when `window` is absent — never throws (prevents breaking isolated component tests / SSR).
- Breakpoints (verbatim): `cozy: 768, standard: 1200, wide: 1600, ultrawide: 2200`. `hasRail` = screen is `wide` or `ultrawide`.
- Co-author trailer on every commit: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- New code lives under `frontend/src/shell/`. Do NOT delete the S48 `frontend/src/terminal/useMediaQuery.ts` or `terminal-mobile.css` — they remain.
- Rail data comes only from existing services/stores — no backend changes.

---

### Task 1: ViewportProvider anchor + mount

**Files:**
- Create: `frontend/src/shell/ViewportProvider.tsx`
- Create: `frontend/src/shell/ViewportProvider.test.tsx`
- Modify: `frontend/src/main.tsx` (wrap `<AppRoutes/>`)

**Interfaces:**
- Produces: `ScreenClass`, `BREAKPOINTS`, `Viewport` interface, `ViewportProvider` component, `useViewport(): Viewport`.

- [ ] **Step 1: Write the failing test** — `frontend/src/shell/ViewportProvider.test.tsx`

```tsx
import { render, screen, act } from '@testing-library/react';
import { describe, it, expect, beforeEach } from 'vitest';
import { ViewportProvider, useViewport, classForWidth } from './ViewportProvider';

function Probe() {
  const v = useViewport();
  return <span data-testid="v">{`${v.screen}|${v.hasRail}|${v.sidebarMode}`}</span>;
}

function setWidth(w: number) {
  (window as unknown as { innerWidth: number }).innerWidth = w;
  act(() => { window.dispatchEvent(new Event('resize')); });
}

describe('classForWidth', () => {
  it.each([
    [500, 'compact'], [767, 'compact'], [768, 'cozy'], [1199, 'cozy'],
    [1200, 'standard'], [1599, 'standard'], [1600, 'wide'], [2199, 'wide'],
    [2200, 'ultrawide'], [3440, 'ultrawide'],
  ])('width %i -> %s', (w, cls) => {
    expect(classForWidth(w as number)).toBe(cls);
  });
});

describe('useViewport', () => {
  beforeEach(() => { (window as unknown as { innerWidth: number }).innerWidth = 1280; });

  it('exposes screen, hasRail and sidebarMode', () => {
    (window as unknown as { innerWidth: number }).innerWidth = 1700;
    render(<ViewportProvider><Probe /></ViewportProvider>);
    expect(screen.getByTestId('v').textContent).toBe('wide|true|full');
  });

  it('updates on resize', () => {
    render(<ViewportProvider><Probe /></ViewportProvider>);
    expect(screen.getByTestId('v').textContent).toBe('standard|false|full');
    setWidth(2400);
    expect(screen.getByTestId('v').textContent).toBe('ultrawide|true|full');
    setWidth(700);
    expect(screen.getByTestId('v').textContent).toBe('compact|false|drawer');
  });

  it('returns a safe default outside a provider', () => {
    render(<Probe />);
    expect(screen.getByTestId('v').textContent).toBe('standard|false|full');
  });
});
```

- [ ] **Step 2: Run test to verify it fails** — `cd frontend && npx vitest run src/shell/ViewportProvider.test.tsx` → FAIL (module not found).

- [ ] **Step 3: Implement** — `frontend/src/shell/ViewportProvider.tsx`

```tsx
import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';

export type ScreenClass = 'compact' | 'cozy' | 'standard' | 'wide' | 'ultrawide';

export const BREAKPOINTS = { cozy: 768, standard: 1200, wide: 1600, ultrawide: 2200 } as const;

const ORDER: ScreenClass[] = ['compact', 'cozy', 'standard', 'wide', 'ultrawide'];

export function classForWidth(w: number): ScreenClass {
  if (w >= BREAKPOINTS.ultrawide) return 'ultrawide';
  if (w >= BREAKPOINTS.wide) return 'wide';
  if (w >= BREAKPOINTS.standard) return 'standard';
  if (w >= BREAKPOINTS.cozy) return 'cozy';
  return 'compact';
}

export interface Viewport {
  width: number;
  height: number;
  screen: ScreenClass;
  hasRail: boolean;
  sidebarMode: 'drawer' | 'rail' | 'full';
  atLeast: (c: ScreenClass) => boolean;
}

function deriveViewport(width: number, height: number): Viewport {
  const screen = classForWidth(width);
  const sidebarMode = screen === 'compact' ? 'drawer' : screen === 'cozy' ? 'rail' : 'full';
  return {
    width, height, screen,
    hasRail: screen === 'wide' || screen === 'ultrawide',
    sidebarMode,
    atLeast: (c) => ORDER.indexOf(screen) >= ORDER.indexOf(c),
  };
}

// Safe default: outside a provider or with no window, report a neutral desktop.
const DEFAULT: Viewport = deriveViewport(1280, 800);

const ViewportContext = createContext<Viewport>(DEFAULT);

export function ViewportProvider({ children }: { children: ReactNode }) {
  const [size, setSize] = useState(() =>
    typeof window === 'undefined'
      ? { w: 1280, h: 800 }
      : { w: window.innerWidth, h: window.innerHeight },
  );

  useEffect(() => {
    if (typeof window === 'undefined') return;
    let frame = 0;
    const onResize = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => setSize({ w: window.innerWidth, h: window.innerHeight }));
    };
    onResize();
    window.addEventListener('resize', onResize);
    return () => { cancelAnimationFrame(frame); window.removeEventListener('resize', onResize); };
  }, []);

  const value = useMemo(() => deriveViewport(size.w, size.h), [size.w, size.h]);
  return <ViewportContext.Provider value={value}>{children}</ViewportContext.Provider>;
}

export function useViewport(): Viewport {
  return useContext(ViewportContext);
}
```

- [ ] **Step 4: Mount in `frontend/src/main.tsx`** — add import and wrap `<AppRoutes/>`.

```tsx
// add with the other imports:
import { ViewportProvider } from './shell/ViewportProvider'
// change the AuthProvider body from <AppRoutes /> to:
<AuthProvider>
  <ViewportProvider>
    <AppRoutes />
  </ViewportProvider>
</AuthProvider>
```

- [ ] **Step 5: Run tests + gates** — `npx vitest run src/shell/ViewportProvider.test.tsx` PASS; then `npx tsc --noEmit && npx eslint src --max-warnings 0`. Commit.

```bash
git add frontend/src/shell/ViewportProvider.tsx frontend/src/shell/ViewportProvider.test.tsx frontend/src/main.tsx
git commit -m "feat(shell): ViewportProvider anchor — screen-class awareness (S49)"
```

---

### Task 2: ContextRail chrome + registry + DefaultRail

**Files:**
- Create: `frontend/src/shell/ContextRail.tsx`, `frontend/src/shell/ContextRail.css`
- Create: `frontend/src/shell/railRegistry.tsx`
- Create: `frontend/src/shell/rails/DefaultRail.tsx`
- Create: `frontend/src/shell/ContextRail.test.tsx`

**Interfaces:**
- Consumes: `useViewport` (Task 1); `PageType` from `../components/Layout/AppLayout`.
- Produces: `ContextRail` (props `{ page: PageType }`), `RAIL_CONTENT: Partial<Record<PageType, React.LazyExoticComponent<React.FC>>>`, `railTitleFor(page): string`.

- [ ] **Step 1: Write the failing test** — `frontend/src/shell/ContextRail.test.tsx`

```tsx
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, beforeEach, vi } from 'vitest';

vi.mock('./railRegistry', () => ({
  RAIL_CONTENT: { dashboard: () => <div>dash rail</div> },
  railTitleFor: (p: string) => (p === 'dashboard' ? 'Overview' : 'Context'),
}));

import { ContextRail } from './ContextRail';

describe('ContextRail', () => {
  beforeEach(() => localStorage.clear());

  it('renders the registry slot for a known page', () => {
    render(<ContextRail page="dashboard" />);
    expect(screen.getByText('dash rail')).toBeInTheDocument();
    expect(screen.getByText('Overview')).toBeInTheDocument();
  });

  it('falls back to DefaultRail content for an unknown page', () => {
    render(<ContextRail page="settings" />);
    // default rail renders a "Quick actions" heading
    expect(screen.getByText(/Quick actions/i)).toBeInTheDocument();
  });

  it('collapses and persists the collapsed state', () => {
    render(<ContextRail page="dashboard" />);
    fireEvent.click(screen.getByRole('button', { name: /collapse/i }));
    expect(localStorage.getItem('aura.rail.collapsed')).toBe('true');
    expect(screen.queryByText('dash rail')).not.toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/shell/ContextRail.test.tsx` → FAIL.

- [ ] **Step 3a: Implement `frontend/src/shell/rails/DefaultRail.tsx`**

```tsx
import { useSystemHealth } from '../../hooks/useSystemHealth';

// Fallback rail: quick actions + system pulse. Pages without a specific slot
// still get something useful in the reclaimed width.
export default function DefaultRail() {
  const health = useSystemHealth();
  return (
    <div className="rail-section">
      <h4 className="rail-section__title">Quick actions</h4>
      <div className="rail-quick">
        <a className="rail-quick__btn" href="/app/chat">Ask about your data</a>
        <a className="rail-quick__btn" href="/app/files">Upload a file</a>
        <a className="rail-quick__btn" href="/app/terminal">Open the Terminal</a>
      </div>
      <h4 className="rail-section__title">System pulse</h4>
      <div className={`rail-pulse rail-pulse--${health.isOnline ? 'on' : 'off'}`}>
        <span className="rail-pulse__dot" />
        {health.isOnline ? 'Gateway healthy' : 'Gateway unreachable'}
      </div>
    </div>
  );
}
```

- [ ] **Step 3b: Implement `frontend/src/shell/railRegistry.tsx`**

```tsx
import { lazy } from 'react';
import type { PageType } from '../components/Layout/AppLayout';

// Page-aware rail content. Pages absent here fall back to DefaultRail.
export const RAIL_CONTENT: Partial<Record<PageType, React.LazyExoticComponent<React.FC>>> = {
  dashboard: lazy(() => import('./rails/DashboardRail')),
  lineage:   lazy(() => import('./rails/LineageRail')),
  queries:   lazy(() => import('./rails/HistoryRail')),
  library:   lazy(() => import('./rails/HistoryRail')),
};

const TITLES: Partial<Record<PageType, string>> = {
  dashboard: 'Overview', lineage: 'Inspector', queries: 'Recent', library: 'Saved & recent',
};

export function railTitleFor(page: PageType): string {
  return TITLES[page] ?? 'Context';
}
```

- [ ] **Step 3c: Implement `frontend/src/shell/ContextRail.tsx`**

```tsx
import { Suspense, useState } from 'react';
import type { PageType } from '../components/Layout/AppLayout';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { RAIL_CONTENT, railTitleFor } from './railRegistry';
import DefaultRail from './rails/DefaultRail';
import './ContextRail.css';

const KEY = 'aura.rail.collapsed';

export function ContextRail({ page }: { page: PageType }) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return localStorage.getItem(KEY) === 'true'; } catch { return false; }
  });
  const toggle = () => setCollapsed((c) => {
    const next = !c;
    try { localStorage.setItem(KEY, String(next)); } catch { /* ignore */ }
    return next;
  });

  const Slot = RAIL_CONTENT[page] ?? DefaultRail;

  return (
    <aside className="context-rail" data-collapsed={collapsed} data-testid="context-rail">
      <header className="context-rail__head">
        {!collapsed && <span className="context-rail__title">{railTitleFor(page)}</span>}
        <button
          type="button"
          className="context-rail__toggle"
          aria-label={collapsed ? 'Expand context panel' : 'Collapse context panel'}
          onClick={toggle}
        >
          {collapsed ? '⟨' : '⟩'}
        </button>
      </header>
      {!collapsed && (
        <div className="context-rail__body">
          <ErrorBoundary resetLabel="Reload panel">
            <Suspense fallback={<div className="rail-loading">Loading…</div>}>
              <Slot />
            </Suspense>
          </ErrorBoundary>
        </div>
      )}
    </aside>
  );
}
```

- [ ] **Step 3d: Implement `frontend/src/shell/ContextRail.css`** — scoped rail chrome using tokens.

```css
.context-rail {
  display: flex; flex-direction: column; min-height: 0;
  height: 100vh; overflow: hidden;
  background: var(--bg-canvas); border-left: 1px solid var(--border-subtle);
}
.context-rail[data-collapsed='true'] { width: 48px; }
.context-rail__head {
  display: flex; align-items: center; justify-content: space-between;
  height: var(--header-height); padding: 0 var(--space-3);
  border-bottom: 1px solid var(--border-subtle); flex-shrink: 0;
}
.context-rail__title { font-size: var(--font-xs); font-weight: var(--weight-semibold);
  text-transform: uppercase; letter-spacing: var(--tracking-wide); color: var(--text-tertiary); }
.context-rail__toggle { width: 26px; height: 26px; border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md); background: var(--bg-surface); color: var(--text-secondary);
  cursor: pointer; }
.context-rail__toggle:hover { color: var(--text-primary); border-color: var(--border-default); }
.context-rail__body { flex: 1; min-height: 0; overflow-y: auto; padding: var(--space-4);
  display: flex; flex-direction: column; gap: var(--space-5); }
.rail-section__title { margin: 0 0 var(--space-2); font-size: var(--font-xs); font-weight: 600;
  text-transform: uppercase; letter-spacing: var(--tracking-wide); color: var(--text-tertiary); }
.rail-quick { display: flex; flex-direction: column; gap: var(--space-2); }
.rail-quick__btn { padding: var(--space-2-5) var(--space-3); border: 1px solid var(--border-subtle);
  border-radius: var(--radius-md); background: var(--bg-surface-2); color: var(--text-secondary);
  font-size: var(--font-sm); text-decoration: none; }
.rail-quick__btn:hover { color: var(--text-primary); border-color: var(--accent-border); }
.rail-pulse { display: flex; align-items: center; gap: var(--space-2); font-size: var(--font-sm);
  color: var(--text-secondary); }
.rail-pulse__dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); }
.rail-pulse--off .rail-pulse__dot { background: var(--red); }
.rail-loading { color: var(--text-tertiary); font-size: var(--font-sm); }
```

- [ ] **Step 4: Run tests** — `npx vitest run src/shell/ContextRail.test.tsx` PASS (3/3).

- [ ] **Step 5: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0
git add frontend/src/shell/ContextRail.tsx frontend/src/shell/ContextRail.css frontend/src/shell/railRegistry.tsx frontend/src/shell/rails/DefaultRail.tsx frontend/src/shell/ContextRail.test.tsx
git commit -m "feat(shell): ContextRail chrome + page-aware registry + DefaultRail (S49)"
```

---

### Task 3: Page rail slots — DashboardRail, LineageRail, HistoryRail

**Files:**
- Create: `frontend/src/shell/rails/DashboardRail.tsx`, `LineageRail.tsx`, `HistoryRail.tsx`
- Create: `frontend/src/shell/rails/rails.test.tsx`

**Interfaces:**
- Consumes: `useSystemHealth`, `healthHint` (`../../hooks/useSystemHealth`); `savedQueryService.list()` → `SavedQuery[]` (`../../services/api`); `useAuraStore()` → `{ state: { queryHistory }, actions: { fetchQueryHistory } }` (`../../store`); `lineageService.get()` (`../../services/api`).
- Produces: three default-export `React.FC` rail components registered in Task 2's `RAIL_CONTENT`.

- [ ] **Step 1: Write the failing test** — `frontend/src/shell/rails/rails.test.tsx`

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../services/api', () => ({
  savedQueryService: { list: vi.fn().mockResolvedValue([
    { id: '1', name: 'Revenue by region', sql: 'SELECT 1', starred: true, created_at: '', updated_at: '' },
  ]) },
  lineageService: { get: vi.fn().mockResolvedValue({ success: true, nodes: [], edges: [], summary: { tables: 2, queries: 3, dashboards: 0, edges: 4 } }) },
}));
vi.mock('../../hooks/useSystemHealth', () => ({
  useSystemHealth: () => ({ isOnline: true, status: 'healthy' }),
  healthHint: () => 'Gateway healthy',
}));
vi.mock('../../store', () => ({
  useAuraStore: () => ({
    state: { queryHistory: [{ id: 'q1', prompt: 'top vendors', sql: 'SELECT', status: 'success', rows: 5, executionTime: 12, timestamp: '' }] },
    actions: { fetchQueryHistory: vi.fn() },
  }),
}));

import DashboardRail from './DashboardRail';
import HistoryRail from './HistoryRail';
import LineageRail from './LineageRail';

describe('rail slots', () => {
  it('DashboardRail shows pulse + recent saved query', async () => {
    render(<DashboardRail />);
    expect(screen.getByText('Gateway healthy')).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText('Revenue by region')).toBeInTheDocument());
  });
  it('HistoryRail shows a recent prompt', () => {
    render(<HistoryRail />);
    expect(screen.getByText(/top vendors/)).toBeInTheDocument();
  });
  it('LineageRail shows the graph summary', async () => {
    render(<LineageRail />);
    await waitFor(() => expect(screen.getByText(/2 tables/)).toBeInTheDocument());
  });
});
```

- [ ] **Step 2: Run to verify it fails** — `npx vitest run src/shell/rails/rails.test.tsx` → FAIL.

- [ ] **Step 3a: `frontend/src/shell/rails/DashboardRail.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { useSystemHealth, healthHint } from '../../hooks/useSystemHealth';
import { savedQueryService, type SavedQuery } from '../../services/api';

export default function DashboardRail() {
  const health = useSystemHealth();
  const [recent, setRecent] = useState<SavedQuery[]>([]);
  useEffect(() => { savedQueryService.list().then((qs) => setRecent(qs.slice(0, 6))).catch(() => {}); }, []);

  return (
    <>
      <div className="rail-section">
        <h4 className="rail-section__title">System pulse</h4>
        <div className={`rail-pulse rail-pulse--${health.isOnline ? 'on' : 'off'}`}>
          <span className="rail-pulse__dot" />{healthHint(health.status)}
        </div>
      </div>
      <div className="rail-section">
        <h4 className="rail-section__title">Recent saved queries</h4>
        {recent.length === 0 ? (
          <p className="rail-empty">Nothing saved yet.</p>
        ) : (
          <ul className="rail-list">
            {recent.map((q) => (
              <li key={q.id}><a className="rail-list__item" href="/app/library">{q.starred ? '★ ' : ''}{q.name}</a></li>
            ))}
          </ul>
        )}
      </div>
      <div className="rail-section">
        <h4 className="rail-section__title">Ask</h4>
        <a className="rail-quick__btn" href="/app/chat">Ask about your data →</a>
      </div>
    </>
  );
}
```

- [ ] **Step 3b: `frontend/src/shell/rails/HistoryRail.tsx`**

```tsx
import { useEffect } from 'react';
import { useAuraStore } from '../../store';

interface QueryRecord { id: string; prompt: string; status: string; rows: number; timestamp: string; }

export default function HistoryRail() {
  const { state: { queryHistory }, actions: { fetchQueryHistory } } = useAuraStore();
  useEffect(() => { fetchQueryHistory(20); }, [fetchQueryHistory]);
  const items = (queryHistory as QueryRecord[]).slice(0, 12);
  return (
    <div className="rail-section">
      <h4 className="rail-section__title">Recent queries</h4>
      {items.length === 0 ? (
        <p className="rail-empty">No queries yet.</p>
      ) : (
        <ul className="rail-list">
          {items.map((q) => (
            <li key={q.id}>
              <span className="rail-list__item" title={q.prompt}>
                <span className={`rail-dot rail-dot--${q.status}`} />{q.prompt || '(query)'}
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
```

- [ ] **Step 3c: `frontend/src/shell/rails/LineageRail.tsx`**

```tsx
import { useEffect, useState } from 'react';
import { lineageService, type LineageGraph } from '../../services/api';

export default function LineageRail() {
  const [g, setG] = useState<LineageGraph | null>(null);
  useEffect(() => { lineageService.get().then(setG).catch(() => {}); }, []);
  if (!g) return <p className="rail-empty">Loading lineage…</p>;
  const s = g.summary;
  return (
    <div className="rail-section">
      <h4 className="rail-section__title">Graph</h4>
      <ul className="rail-stat">
        <li><b>{s.tables}</b> tables</li>
        <li><b>{s.queries}</b> queries</li>
        <li><b>{s.dashboards}</b> dashboards</li>
        <li><b>{s.edges}</b> edges</li>
      </ul>
      <p className="rail-hint">Select a node on the graph to inspect it here.</p>
    </div>
  );
}
```

- [ ] **Step 3d: Append rail list/stat styles to `frontend/src/shell/ContextRail.css`**

```css
.rail-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--space-1); }
.rail-list__item { display: flex; align-items: center; gap: var(--space-2); padding: var(--space-1) 0;
  font-size: var(--font-sm); color: var(--text-secondary); text-decoration: none;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rail-list__item:hover { color: var(--text-primary); }
.rail-dot { width: 6px; height: 6px; border-radius: 50%; flex-shrink: 0; background: var(--text-tertiary); }
.rail-dot--success { background: var(--green); } .rail-dot--error { background: var(--red); }
.rail-stat { list-style: none; margin: 0; padding: 0; display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-2); font-size: var(--font-sm); color: var(--text-secondary); }
.rail-stat b { color: var(--text-primary); font-family: var(--font-mono); }
.rail-empty, .rail-hint { font-size: var(--font-sm); color: var(--text-tertiary); margin: 0; }
```

- [ ] **Step 4: Run tests** — `npx vitest run src/shell/rails/rails.test.tsx` PASS (3/3).

- [ ] **Step 5: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0
git add frontend/src/shell/rails/ frontend/src/shell/ContextRail.css
git commit -m "feat(shell): page-aware rail slots — dashboard, history, lineage (S49)"
```

---

### Task 4: Fluid AppLayout grid shell (delete S48 cap, mount rail)

**Files:**
- Modify: `frontend/src/components/Layout/AppLayout.tsx`
- Modify: `frontend/src/components/Layout/AppLayout.css`
- Create: `frontend/src/components/Layout/AppLayout.rail.test.tsx`

**Interfaces:**
- Consumes: `useViewport` (Task 1), `ContextRail` (Task 2). `currentPage: PageType` already a prop.

- [ ] **Step 1: Write the failing test** — `frontend/src/components/Layout/AppLayout.rail.test.tsx`

```tsx
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { describe, it, expect, vi } from 'vitest';

vi.mock('../../shell/ViewportProvider', async (orig) => {
  const real = await orig() as Record<string, unknown>;
  return { ...real, useViewport: () => ({ width: 1900, height: 900, screen: 'wide', hasRail: true, sidebarMode: 'full', atLeast: () => true }) };
});
vi.mock('./Sidebar', () => ({ default: () => <div data-testid="sb" /> }));
vi.mock('./Header', () => ({ default: () => <div data-testid="hd" /> }));
vi.mock('../../shell/ContextRail', () => ({ ContextRail: ({ page }: { page: string }) => <div data-testid="rail">{page}</div> }));

import AppLayout from './AppLayout';

it('mounts the ContextRail and sets data-rail when hasRail', () => {
  render(<MemoryRouter><AppLayout currentPage="dashboard" onPageChange={() => {}}><div>body</div></AppLayout></MemoryRouter>);
  expect(screen.getByTestId('rail')).toHaveTextContent('dashboard');
  expect(document.querySelector('.app-shell')?.getAttribute('data-rail')).toBe('true');
  expect(document.querySelector('.app-shell')?.getAttribute('data-screen')).toBe('wide');
});
```

- [ ] **Step 2: Run to verify it fails** — FAIL (no rail, no data-rail attr).

- [ ] **Step 3a: Modify `AppLayout.tsx`** — import the anchor + rail, set attrs, render rail column.

```tsx
// add imports:
import { useViewport } from '../../shell/ViewportProvider';
import { ContextRail } from '../../shell/ContextRail';
// inside the component, after systemHealth:
const vp = useViewport();
// change the root <div className="app-shell"> opening tag to:
<div className="app-shell" data-screen={vp.screen} data-rail={vp.hasRail ? 'true' : undefined}>
// after the closing </div> of .app-shell__content, before .app-shell closes, add:
{vp.hasRail && <ContextRail page={currentPage} />}
```

- [ ] **Step 3b: Modify `AppLayout.css`** — grid shell; DELETE the S48 cap block; tier padding.

Replace the `.app-shell` rule and DELETE the entire `@media (min-width: 1700px)` and `@media (min-width: 2600px)` blocks added in S48. New `.app-shell`:

```css
.app-shell {
  display: grid;
  grid-template-columns: auto minmax(0, 1fr);
  height: 100vh; width: 100vw;
  background: var(--bg-base);
  overflow: hidden;
}
.app-shell[data-rail='true'] {
  --rail-w: clamp(300px, 22vw, 460px);
  grid-template-columns: auto minmax(0, 1fr) var(--rail-w);
}
/* fluid gutters by tier (replaces the old fixed cap) */
.app-shell[data-screen='wide'] .app-shell__main-inner,
.app-shell[data-screen='ultrawide'] .app-shell__main-inner { padding: var(--space-8); }
```

Also DELETE the `width: 100%; max-width: 1600px; margin-inline: auto;` lines (the whole S49-superseded S48 block). Keep the existing `<=1024/768/520` down-rules and the mobile-drawer rules.

- [ ] **Step 4: Run tests** — `npx vitest run src/components/Layout/AppLayout.rail.test.tsx` PASS; run existing `npx vitest run src/components/Layout` to confirm no regressions.

- [ ] **Step 5: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0 && npm run build
git add frontend/src/components/Layout/AppLayout.tsx frontend/src/components/Layout/AppLayout.css frontend/src/components/Layout/AppLayout.rail.test.tsx
git commit -m "feat(shell): fluid AppLayout grid + mount ContextRail; remove S48 cap (S49)"
```

---

### Task 5: Fluid content — kill the stretch

**Files:**
- Modify: `frontend/src/styles/design-system.css` (add `.fluid-cards`)
- Modify: `frontend/src/components/Layout/AppLayout.css` (`dashboard-grid--kpis`, `--panels`)

**Interfaces:** none new — CSS-only.

- [ ] **Step 1: Add `.fluid-cards` to `design-system.css`** (near the `.aura-split` block).

```css
/* Cards pack into more columns as width grows, capped so they never balloon. */
.fluid-cards { display: grid; gap: var(--space-4);
  grid-template-columns: repeat(auto-fit, minmax(min(240px, 100%), 1fr)); }
```

- [ ] **Step 2: Make the dashboard grids fluid in `AppLayout.css`.** Replace the fixed `grid-template-columns` so 4 KPI tiles stay ~240–300px and pack:

```css
.dashboard-grid--kpis {
  display: grid; gap: var(--space-4); margin-bottom: var(--space-6);
  grid-template-columns: repeat(auto-fit, minmax(min(220px, 100%), 1fr));
}
.dashboard-grid--panels {
  display: grid; gap: var(--space-4); margin-bottom: var(--space-6);
  grid-template-columns: repeat(auto-fit, minmax(min(340px, 100%), 1fr));
}
```

DELETE the now-redundant `@media (max-width: 1280px)/(1024px)/(640px)/(520px)` overrides that set `.dashboard-grid--kpis`/`--panels` column counts (auto-fit handles all widths). Keep the KPI `gap`/padding tweaks.

- [ ] **Step 3: Verify build** — `npm run build` (CSS-only; no unit test). Manual check deferred to Task 8 live pass.

- [ ] **Step 4: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0 && npm run build
git add frontend/src/styles/design-system.css frontend/src/components/Layout/AppLayout.css
git commit -m "feat(shell): fluid card grids — pack not stretch (S49)"
```

---

### Task 6: Lineage inspector → rail when hasRail

**Files:**
- Modify: `frontend/src/pages/Lineage.tsx`

**Interfaces:** Consumes `useViewport` (Task 1).

- [ ] **Step 1: Modify `Lineage.tsx`** — when the rail exists, the LineageRail shows the graph summary and the page hides its own right-hand inspector `<aside>` (avoids duplicate panels). Add:

```tsx
import { useViewport } from '../shell/ViewportProvider';
// inside component:
const { hasRail } = useViewport();
// change the wrapper className from "aura-split aura-split--detail" to:
<div className={hasRail ? '' : 'aura-split aura-split--detail'}>
// wrap the inspector <aside> so it only renders when there's no rail:
{!hasRail && (
  <aside style={{ /* existing inspector styles */ }}>
    {/* existing inspector content */}
  </aside>
)}
```

When `hasRail`, the graph canvas fills the full content column and the rail's `LineageRail` carries the summary. (Node click still sets `selectedId`; with the rail present the inline inspector is simply not shown — acceptable for v1, the rail shows graph stats + guidance.)

- [ ] **Step 2: Run existing Lineage-touching tests** — `npx vitest run src/shell/rails/rails.test.tsx src/components/Layout` (Lineage has no dedicated unit test; rely on build + Task 8 live check). `npx tsc --noEmit`.

- [ ] **Step 3: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0 && npm run build
git add frontend/src/pages/Lineage.tsx
git commit -m "feat(shell): Lineage canvas fills width when rail present (S49)"
```

---

### Task 7: TerminalWorkspace reads the anchor

**Files:**
- Modify: `frontend/src/terminal/TerminalWorkspace.tsx`

**Interfaces:** Consumes `useViewport` (Task 1). Keeps `MobileTerminalStack` + `terminal-mobile.css` from S48.

- [ ] **Step 1: Modify `TerminalWorkspace.tsx`** — replace the S48 media-query source with the anchor. `compact`/`cozy` → mobile stack.

```tsx
// remove: import { useMediaQuery } from './useMediaQuery';
import { useViewport } from '../shell/ViewportProvider';
// replace: const isMobile = useMediaQuery('(max-width: 860px)');
const isMobile = !useViewport().atLeast('standard');
```

The terminal route is a sibling of `/app` and is wrapped by `<ViewportProvider>` (mounted in `main.tsx` above `<AppRoutes/>`), so `useViewport()` resolves. Existing terminal tests render `TerminalWorkspace` without a provider → `useViewport` returns the safe default (`standard`, `atLeast('standard')===true`) → `isMobile===false` → dockview path, exactly as before. No terminal test changes needed.

- [ ] **Step 2: Run terminal tests** — `npx vitest run src/terminal` PASS (existing 29 stay green).

- [ ] **Step 3: Gates + commit**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0
git add frontend/src/terminal/TerminalWorkspace.tsx
git commit -m "feat(shell): Terminal reads the viewport anchor (S49)"
```

---

### Task 8: Login two-pane + final live verification

**Files:**
- Modify: `frontend/src/auth/AuthForm.tsx`
- Create: `frontend/src/auth/AuthForm.css`

**Interfaces:** Consumes `useViewport` (Task 1).

- [ ] **Step 1: Modify `AuthForm.tsx`** — wrap the existing form in a two-pane on `atLeast('standard')`; brand panel hidden on narrow.

```tsx
import { useViewport } from '../shell/ViewportProvider';
import './AuthForm.css';
// inside AuthForm, compute:
const wide = useViewport().atLeast('standard');
// wrap the existing returned <div data-testid="auth-form">…</div> as the RIGHT pane:
return (
  <div className={`auth-pane${wide ? ' auth-pane--split' : ''}`}>
    {wide && (
      <div className="auth-pane__brand">
        <div className="auth-pane__brand-inner">
          <h2 className="auth-pane__brandmark">AURA</h2>
          <p className="auth-pane__valueprop">
            Ask your data in plain English. Get signed, verifiable answers.
          </p>
        </div>
      </div>
    )}
    <div className="auth-pane__form">
      {/* the existing <div data-testid="auth-form"> … </div> moves here unchanged */}
    </div>
  </div>
);
```

- [ ] **Step 2: Create `AuthForm.css`**

```css
.auth-pane { width: 100%; }
.auth-pane--split { display: grid; grid-template-columns: 1.1fr 1fr; gap: var(--space-8);
  align-items: center; min-height: 60vh; max-width: 980px; margin: 0 auto; }
.auth-pane__brand { display: flex; align-items: center; justify-content: center;
  padding: var(--space-8); border-right: 1px solid var(--border-subtle);
  background:
    radial-gradient(120% 120% at 0% 0%, rgba(34,197,94,0.10), transparent 60%),
    var(--bg-surface); border-radius: var(--radius-2xl); min-height: 50vh; }
.auth-pane__brandmark { font-size: var(--font-4xl); letter-spacing: var(--tracking-widest);
  margin: 0 0 var(--space-3); color: var(--text-primary); }
.auth-pane__valueprop { font-size: var(--font-lg); color: var(--text-secondary); line-height: var(--line-relaxed); max-width: 28ch; }
.auth-pane__form { display: flex; align-items: center; justify-content: center; }
```

- [ ] **Step 3: Run auth tests** — `npx vitest run src/auth` PASS (the `data-testid="auth-form"` markup is preserved, so existing tests still pass). Add no new unit test (visual change; covered by build + live check).

- [ ] **Step 4: Full suite + build**

```bash
npx tsc --noEmit && npx eslint src --max-warnings 0 && npx vitest run && npm run build
```

- [ ] **Step 5: Live Playwright verification** at 360 / 768 / 1366 / 1920 / 2560 / 3440:
  - dead-gap = 0 at every width ≥ 1200 (content + rail fills available width).
  - 0 document overflow at 360 / 768 / 1280.
  - rail present only at ≥ 1600; collapse persists across reload.
  - drag-resize across the 1600 boundary flips `data-rail` live.
  - login two-pane on desktop, centered on mobile.
  - dashboard cards pack (not stretched); Terminal + Constellation unaffected.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/auth/AuthForm.tsx frontend/src/auth/AuthForm.css
git commit -m "feat(shell): two-pane login on wide screens (S49)"
```

---

## Self-Review

**Spec coverage:** anchor (T1) ✓; fluid shell + delete cap (T4) ✓; fluid content (T5) ✓; page-aware rail + 4 slots (T2,T3) ✓; lineage inspector→rail (T6) ✓; terminal reads anchor (T7) ✓; login two-pane (T8) ✓. All spec sections mapped.

**Placeholder scan:** every code step has real code; commands have expected outcomes. No TBD/TODO.

**Type consistency:** `Viewport`/`ScreenClass`/`classForWidth`/`useViewport` consistent T1→T4,T6,T7; `RAIL_CONTENT`/`railTitleFor` consistent T2→T4; `SavedQuery`/`savedQueryService.list`/`useAuraStore`/`lineageService.get` match verified `api.ts`/`store` signatures. `PageType` imported from `AppLayout` throughout.

**Note:** `useViewport` returns a safe default outside a provider (Global Constraints) — this is what keeps isolated AppLayout/terminal/auth tests green without wrapping them, and is asserted by T1 step-1's third test.
