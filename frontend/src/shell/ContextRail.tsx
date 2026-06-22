import { Suspense, useState } from 'react';
import type { PageType } from '../components/Layout/AppLayout';
import { ErrorBoundary } from '../components/ui/ErrorBoundary';
import { RAIL_CONTENT, railTitleFor } from './railRegistry';
import DefaultRail from './rails/DefaultRail';
import './ContextRail.css';

const KEY = 'aura.rail.collapsed';

export function ContextRail({ page }: { page: PageType }) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(KEY) === 'true';
    } catch {
      return false;
    }
  });

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c;
      try {
        localStorage.setItem(KEY, String(next));
      } catch {
        /* private mode / no storage — ignore */
      }
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
