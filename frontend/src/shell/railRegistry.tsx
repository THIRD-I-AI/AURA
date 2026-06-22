import { lazy } from 'react';
import type { PageType } from '../components/Layout/AppLayout';

// Page-aware rail content. Pages absent here fall back to DefaultRail (the rail
// reframes around whatever the user is doing). All slots read existing
// services/stores — no new backend.
export const RAIL_CONTENT: Partial<Record<PageType, React.LazyExoticComponent<React.FC>>> = {
  dashboard: lazy(() => import('./rails/DashboardRail')),
  lineage: lazy(() => import('./rails/LineageRail')),
  queries: lazy(() => import('./rails/HistoryRail')),
  library: lazy(() => import('./rails/HistoryRail')),
};

const TITLES: Partial<Record<PageType, string>> = {
  dashboard: 'Overview',
  lineage: 'Inspector',
  queries: 'Recent',
  library: 'Saved & recent',
};

export function railTitleFor(page: PageType): string {
  return TITLES[page] ?? 'Context';
}
