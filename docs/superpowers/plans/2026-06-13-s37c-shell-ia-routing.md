# S37c — Shell + Auditor-Workbench IA + Real Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Replace the internal app's `useState` page-switching with real, deep-linkable React Router routes under `/app`, and regroup the 14-item flat sidebar into the six auditor-workbench sections — without breaking the `setCurrentPage` contract the 8 panels rely on, and keeping all 208 tests green.

**Architecture:** The big risk is the `setCurrentPage(id)` coupling. We keep that exact callback signature, but back it with `navigate()`. `App.tsx` stops owning `currentPage` in `useState` and instead **derives it from the URL** (`useLocation`) and exposes `setCurrentPage = (id) => navigate(pageToPath(id))`. `AppRoutes` keeps its single `/app/*` → `Dashboard` route, so routing nests *inside* `App`. The sidebar gains presentational section grouping (six headers) over the same page ids; URLs stay flat (`/app/chat`, `/app/engagements`) — deep-linkable and back-button-correct without a fragile nested-path scheme. Panels are untouched.

**Tech Stack:** React 18 + TS, react-router-dom v6 (already used by the public surface), Vitest. Branch: `feature/s37-terminal-authority` (continues; no PR).

**Parent spec:** `docs/superpowers/specs/2026-06-11-s37-frontend-redesign-design.md` §4. New compositions (Engagements home content, Findings page) + identity chip are **s37d**, not here — this phase is shell + routing + IA grouping only; the dashboard home keeps its current content but moves to `/app/engagements`.

**Invariants (every task):**
- `setCurrentPage(id: PageType)` keeps working for all 8 panels (no panel file changes).
- Deep links work: visiting `/app/chat` directly renders the chat page; `/app` redirects to `/app/engagements`.
- Sidebar test's icon-per-item invariant stays green (every nav id has an icon).
- Full suite + `tsc` + `eslint --max-warnings 0` green before each commit; co-author `Claude Opus 4.8`.

---

### Task 1: Route helpers (TDD)

**Files:**
- Create: `frontend/src/app/routing.ts`
- Test: `frontend/src/app/__tests__/routing.test.ts`

- [ ] **Step 1: Failing test**

```ts
import { describe, expect, it } from 'vitest';
import { pageToPath, pathToPage, PAGE_IDS } from '../routing';

describe('app routing helpers', () => {
  it('maps the home page to /app/engagements and back', () => {
    expect(pageToPath('dashboard')).toBe('/app/engagements');
    expect(pathToPage('/app/engagements')).toBe('dashboard');
  });
  it('round-trips every page id through a path', () => {
    for (const id of PAGE_IDS) {
      expect(pathToPage(pageToPath(id))).toBe(id);
    }
  });
  it('falls back to dashboard for unknown or bare /app paths', () => {
    expect(pathToPage('/app')).toBe('dashboard');
    expect(pathToPage('/app/nonsense')).toBe('dashboard');
  });
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/app/__tests__/routing.test.ts`

- [ ] **Step 3: Implement `src/app/routing.ts`**

```ts
import type { PageType } from '../components/Layout/AppLayout';

/** Every navigable page id in the internal app. */
export const PAGE_IDS: PageType[] = [
  'dashboard', 'chat', 'files', 'queries', 'library', 'dashboards',
  'lineage', 'cost', 'settings', 'agent', 'pipelines', 'streaming',
  'webhooks', 'counterfactual', 'audit-hitl',
];

/** URL segment per page id. The dashboard home lives at the auditor-centric
 * /app/engagements; every other page is /app/<id>. */
const SEGMENT: Partial<Record<PageType, string>> = { dashboard: 'engagements' };
const segmentFor = (id: PageType): string => SEGMENT[id] ?? id;

const BY_SEGMENT: Record<string, PageType> = Object.fromEntries(
  PAGE_IDS.map((id) => [segmentFor(id), id]),
) as Record<string, PageType>;

export function pageToPath(id: PageType): string {
  return `/app/${segmentFor(id)}`;
}

export function pathToPage(pathname: string): PageType {
  const seg = pathname.replace(/^\/app\/?/, '').split('/')[0];
  return BY_SEGMENT[seg] ?? 'dashboard';
}
```

- [ ] **Step 4: PASS + commit**

```bash
npx vitest run src/app/__tests__/routing.test.ts
git add frontend/src/app/routing.ts frontend/src/app/__tests__/routing.test.ts
git commit -m "feat(s37c): URL<->page routing helpers (dashboard home = /app/engagements)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: App.tsx — derive currentPage from the URL

**Files:**
- Modify: `frontend/src/App.tsx` (the `AppInner` state + the 30s `fetchStats` effect that reads `currentPage`)

- [ ] **Step 1: Edit `AppInner`** — replace the `useState` page state with URL-derived state. At the top of `AppInner`:

Replace:
```tsx
  const [currentPage, setCurrentPage] = useState<PageType>('dashboard');
```
with:
```tsx
  const location = useLocation();
  const navigate = useNavigate();
  const currentPage = pathToPage(location.pathname);
  const setCurrentPage = useCallback(
    (id: PageType) => navigate(pageToPath(id)),
    [navigate],
  );
```

Add imports at the top of `App.tsx`:
```tsx
import { useCallback } from 'react';            // merge into the existing 'react' import
import { useLocation, useNavigate } from 'react-router-dom';
import { pageToPath, pathToPage } from './app/routing';
```
(Combine `useCallback` into the existing `import { useState, useEffect, lazy, Suspense, memo } from 'react'` line — `useState` is still used by `healthStatus`.)

- [ ] **Step 2: The `currentPage`-dependent effect** (the 30s stats poll) already reads `currentPage` — it now reads the derived value, no change needed. Confirm `currentPage` is still referenced for the `'dashboard'` poll guard; it is.

- [ ] **Step 3: Verify** the app builds and existing suite is green (no test asserts page-switching, but routing must not crash render):

```bash
npx tsc --noEmit && npx vitest run 2>&1 | tail -2 && npx eslint src --max-warnings 0
```
Expected: 211 passed (208 + 3 routing), clean.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.tsx
git commit -m "feat(s37c): derive currentPage from the URL (deep-linkable /app routes)

setCurrentPage now navigates instead of mutating useState — panels keep
the same callback contract; /app/<page> is deep-linkable and back-button
correct.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: AppRoutes — nested /app routes + index redirect

**Files:**
- Modify: `frontend/src/AppRoutes.tsx`
- Test: `frontend/src/audit/__tests__/AppRoutes.test.tsx` (add a deep-link + redirect assertion)

- [ ] **Step 1: Failing test** — append to `AppRoutes.test.tsx` (mirror its existing render harness; it renders `<MemoryRouter initialEntries={[path]}><AppRoutes/></MemoryRouter>`):

```tsx
it('deep-links into an internal app page', async () => {
  render(<MemoryRouter initialEntries={['/app/chat']}><AppRoutes /></MemoryRouter>);
  // App is lazy — the chrome (sidebar brand) appears once loaded.
  expect(await screen.findByText('AURA', {}, { timeout: 3000 })).toBeInTheDocument();
});
```
(If the file lacks `screen`/`findByText` imports, add them from `@testing-library/react`. Keep it light — asserting the lazy chunk mounts is enough; deeper page assertions live in panel tests.)

- [ ] **Step 2: Run — likely PASS already** for `/app/chat` (the `/app/*` wildcard catches it) but FAIL for the redirect we add next. If it passes, proceed; the real change is the index redirect.

- [ ] **Step 3: Edit `AppRoutes.tsx`** — keep `/app/*` → `Dashboard`, and confirm `/app` (bare) still mounts `Dashboard` (it does, via the wildcard; `pathToPage('/app')` → dashboard → renders engagements home). No structural route change is required because routing is internal to `App`. Add a clarifying comment:

```tsx
      {/* /app/* mounts the internal app; routing within (page selection,
          deep links, redirects) is handled inside App via app/routing.ts. */}
      <Route path="/app/*" element={<Suspense fallback={<div>Loading…</div>}><Dashboard /></Suspense>} />
```

- [ ] **Step 4: PASS + commit**

```bash
npx vitest run src/audit/__tests__/AppRoutes.test.tsx
git add frontend/src/AppRoutes.tsx frontend/src/audit/__tests__/AppRoutes.test.tsx
git commit -m "test(s37c): cover deep-link into /app internal pages

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Six-section IA in the sidebar (TDD)

**Files:**
- Modify: `frontend/src/components/Layout/nav.ts` (add `section` per item + `NAV_SECTIONS` order)
- Modify: `frontend/src/components/Layout/Sidebar.tsx` (render grouped with section headers)
- Test: `frontend/src/components/Layout/__tests__/Sidebar.test.tsx` (add section-grouping assertion; keep icon-per-item)

- [ ] **Step 1: Add the failing test** to `Sidebar.test.tsx`:

```tsx
it('groups nav items under the six auditor-workbench sections', () => {
  render(<Sidebar items={NAV_ITEMS} activeItem="dashboard" onItemClick={() => {}} />);
  for (const heading of ['Engagements', 'Evidence & Data', 'Findings', 'Certificates', 'Monitoring', 'Admin']) {
    expect(screen.getByText(heading)).toBeInTheDocument();
  }
});
```

- [ ] **Step 2: Run — FAIL.** `npx vitest run src/components/Layout/__tests__/Sidebar.test.tsx`

- [ ] **Step 3: Edit `nav.ts`** — add a `section` to each item and export the section order. Replace the `NAV_ITEMS` array:

```ts
export type NavSection =
  | 'Engagements' | 'Evidence & Data' | 'Findings'
  | 'Certificates' | 'Monitoring' | 'Admin';

export const NAV_SECTIONS: NavSection[] = [
  'Engagements', 'Evidence & Data', 'Findings', 'Certificates', 'Monitoring', 'Admin',
];

export interface NavItem { id: string; label: string; href: string; section: NavSection; }

export const NAV_ITEMS: NavItem[] = [
  { id: 'dashboard',      label: 'Engagements',   href: '#', section: 'Engagements' },
  { id: 'files',          label: 'Files & Data',  href: '#', section: 'Evidence & Data' },
  { id: 'chat',           label: 'Chat (NL→SQL)', href: '#', section: 'Evidence & Data' },
  { id: 'queries',        label: 'Query History', href: '#', section: 'Evidence & Data' },
  { id: 'library',        label: 'Library',       href: '#', section: 'Evidence & Data' },
  { id: 'pipelines',      label: 'ETL Pipelines', href: '#', section: 'Evidence & Data' },
  { id: 'streaming',      label: 'Streaming',     href: '#', section: 'Evidence & Data' },
  { id: 'lineage',        label: 'Lineage',       href: '#', section: 'Evidence & Data' },
  { id: 'audit-hitl',     label: 'Exception Queue', href: '#', section: 'Findings' },
  { id: 'counterfactual', label: 'Counterfactual', href: '#', section: 'Certificates' },
  { id: 'dashboards',     label: 'Dashboards',    href: '#', section: 'Certificates' },
  { id: 'cost',           label: 'LLM Cost',      href: '#', section: 'Monitoring' },
  { id: 'webhooks',       label: 'Webhooks',      href: '#', section: 'Admin' },
  { id: 'agent',          label: 'Agent',         href: '#', section: 'Admin' },
];
```
Note: the home label is now "Engagements" (id stays `dashboard`); Settings stays reachable via the sidebar's existing settings button (not in NAV_ITEMS today — leave that as-is).

- [ ] **Step 4: Edit `Sidebar.tsx`** — render grouped. Replace the `<nav>` body's `items.map(...)` with a section-grouped render. Keep each item button exactly as today (icon + label + active state) so the icon-per-item test still holds:

```tsx
      <nav className="sidebar-nav">
        {NAV_SECTIONS.map((section) => {
          const inSection = items.filter((it) => (it as { section?: string }).section === section);
          if (inSection.length === 0) return null;
          return (
            <div key={section} className="sidebar-nav__group">
              {!collapsed && <div className="sidebar-nav__heading">{section}</div>}
              {inSection.map((item) => {
                const isActive = activeItem === item.id;
                const icon = item.icon ?? NAV_ICON_MAP[item.id];
                return (
                  <button
                    key={item.id}
                    onClick={() => onItemClick(item.id)}
                    title={collapsed ? item.label : undefined}
                    aria-current={isActive ? 'page' : undefined}
                    className={['sidebar-nav-item', isActive && 'sidebar-nav-item--active', collapsed && 'sidebar-nav-item--collapsed'].filter(Boolean).join(' ')}
                  >
                    <span className="sidebar-nav-item__icon">{icon}</span>
                    {!collapsed && <span className="sidebar-nav-item__label">{item.label}</span>}
                    {!collapsed && item.badge != null && item.badge > 0 && (
                      <span className="sidebar-nav-item__badge">{item.badge}</span>
                    )}
                  </button>
                );
              })}
            </div>
          );
        })}
      </nav>
```
Import `NAV_SECTIONS` from `./nav` at the top of Sidebar.tsx. Add the `section` field to the local `SidebarItem` interface as optional (`section?: string`).

- [ ] **Step 5: Add section-heading styles** to `Sidebar.css` (find the file; append):

```css
.sidebar-nav__group { display: flex; flex-direction: column; gap: 2px; margin-bottom: var(--space-3); }
.sidebar-nav__heading {
  font-family: var(--font-mono); font-size: var(--font-2xs); text-transform: uppercase;
  letter-spacing: 0.1em; color: var(--text-tertiary);
  padding: var(--space-2) var(--space-3) var(--space-1);
}
```

- [ ] **Step 6: Update `AppLayout.tsx` PAGE_META** for the relabeled home + exception-queue (titles shown in the header breadcrumb). Change the `dashboard` meta title to `'Engagements'` and `audit-hitl` to keep its workbench subtitle. Verify `PAGE_META` still has an entry per id.

- [ ] **Step 7: Full gate + commit**

```bash
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
git add frontend/src/components/Layout/nav.ts frontend/src/components/Layout/Sidebar.tsx frontend/src/components/Layout/Sidebar.css frontend/src/components/Layout/AppLayout.tsx frontend/src/components/Layout/__tests__/Sidebar.test.tsx
git commit -m "feat(s37c): six-section auditor-workbench sidebar IA

Engagements / Evidence & Data / Findings / Certificates / Monitoring /
Admin group headers over the existing page ids. Home relabeled
Engagements; ExceptionQueue surfaced under Findings.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Verify + visual checkpoint + push

- [ ] **Step 1: Full gate**

```bash
cd ~/Downloads/AURA/frontend
npx vitest run 2>&1 | tail -2 && npx tsc --noEmit && npx eslint src --max-warnings 0
```
Expected: ≥212 passed, clean.

- [ ] **Step 2: Visual + behaviour checkpoint** (dev server :5173):
  - Visit `/app` → redirects/renders Engagements home; sidebar shows six section headers.
  - Click **Chat** → URL becomes `/app/chat`, chat renders; browser **Back** returns to engagements (proves real history).
  - Hard-reload `/app/cost` → LLM Cost renders directly (deep link).
  - Click a dashboard Quick-action (uses `setCurrentPage`) → navigates correctly.
  - Console clean under CSP. Screenshot the grouped sidebar.

- [ ] **Step 3: Push**

```bash
cd ~/Downloads/AURA
git push origin feature/s37-terminal-authority
```
No PR — continues into s37d (Engagements/Findings compositions + identity chip + 401 surface).

---

## Self-Review

- **Spec §4 coverage:** real deep-linkable routes → Tasks 1–3; six-section IA → Task 4; new Engagements/Findings *compositions* + identity chip explicitly deferred to s37d (called out in Architecture). Settings reachability preserved via the existing sidebar settings button.
- **Risk control:** `setCurrentPage` contract preserved exactly → zero panel-file changes, zero panel-test churn; only `Sidebar.test.tsx` + `AppRoutes.test.tsx` get additive assertions. The single behavioural change (state → URL) has no existing test asserting the old behaviour (confirmed: no App/AppLayout test).
- **Placeholder scan:** none — full code in every step.
- **Type consistency:** `PageType` imported from `AppLayout` into `routing.ts`; `PAGE_IDS` matches the `PageType` union (15 ids incl. `audit-hitl`); `pageToPath`/`pathToPage` names used identically in App.tsx and tests; `NAV_SECTIONS`/`NavItem.section` consistent between nav.ts, Sidebar.tsx, and the Sidebar test.
