/**
 * Sidebar navigation registry. Lives outside the component files so tests
 * (and the command palette) can import it without tripping react-refresh's
 * only-export-components rule. Every id here MUST have an entry in
 * Sidebar's NAV_ICON_MAP — the collapsed rail renders icons only, and an
 * icon-less item degrades to clipped label text (S35a).
 *
 * S37c: items are grouped into the six auditor-workbench sections.
 */
export type NavSection =
  | 'Engagements' | 'Evidence & Data' | 'Findings'
  | 'Certificates' | 'Monitoring' | 'Admin';

/** Render order of the sidebar section headers. */
export const NAV_SECTIONS: NavSection[] = [
  'Engagements', 'Evidence & Data', 'Findings', 'Certificates', 'Monitoring', 'Admin',
];

export interface NavItem {
  id: string;
  label: string;
  href: string;
  section: NavSection;
}

export const NAV_ITEMS: NavItem[] = [
  // Home: the dashboard becomes the auditor's engagements overview. Labeled
  // "Overview" (not "Engagements") so it doesn't collide with the section header.
  { id: 'dashboard',      label: 'Overview',       href: '#', section: 'Engagements' },
  { id: 'files',          label: 'Files & Data',   href: '#', section: 'Evidence & Data' },
  { id: 'chat',           label: 'Chat (NL→SQL)',  href: '#', section: 'Evidence & Data' },
  { id: 'queries',        label: 'Query History',  href: '#', section: 'Evidence & Data' },
  { id: 'library',        label: 'Library',        href: '#', section: 'Evidence & Data' },
  { id: 'pipelines',      label: 'ETL Pipelines',  href: '#', section: 'Evidence & Data' },
  { id: 'streaming',      label: 'Streaming',      href: '#', section: 'Evidence & Data' },
  { id: 'lineage',        label: 'Lineage',        href: '#', section: 'Evidence & Data' },
  { id: 'audit-hitl',     label: 'Exception Queue', href: '#', section: 'Findings' },
  { id: 'counterfactual', label: 'Counterfactual', href: '#', section: 'Certificates' },
  { id: 'dashboards',     label: 'Dashboards',     href: '#', section: 'Certificates' },
  { id: 'cost',           label: 'LLM Cost',       href: '#', section: 'Monitoring' },
  { id: 'webhooks',       label: 'Webhooks',       href: '#', section: 'Admin' },
  { id: 'agent',          label: 'Agent',          href: '#', section: 'Admin' },
];
