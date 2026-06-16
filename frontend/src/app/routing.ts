import type { PageType } from '../components/Layout/AppLayout';

/** Every navigable page id in the internal app. */
export const PAGE_IDS: PageType[] = [
  'dashboard', 'chat', 'files', 'queries', 'library', 'dashboards',
  'lineage', 'cost', 'settings', 'agent', 'pipelines', 'streaming',
  'webhooks', 'counterfactual', 'audit-hitl', 'audit-service',
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
