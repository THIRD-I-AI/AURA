/**
 * Sidebar navigation registry. Lives outside the component files so tests
 * (and the command palette) can import it without tripping react-refresh's
 * only-export-components rule. Every id here MUST have an entry in
 * Sidebar's NAV_ICON_MAP — the collapsed rail renders icons only, and an
 * icon-less item degrades to clipped label text (S35a).
 */
export const NAV_ITEMS = [
  { id: 'dashboard', label: 'Dashboard', href: '#' },
  { id: 'chat',      label: 'Chat',      href: '#' },
  { id: 'files',     label: 'Files & Data', href: '#' },
  { id: 'queries',   label: 'Query History', href: '#' },
  { id: 'library',   label: 'Library',   href: '#' },
  { id: 'dashboards',label: 'Dashboards', href: '#' },
  { id: 'lineage',   label: 'Lineage',   href: '#' },
  { id: 'cost',      label: 'LLM Cost',  href: '#' },
  { id: 'agent',     label: 'Agent',     href: '#' },
  { id: 'pipelines', label: 'ETL Pipelines', href: '#' },
  { id: 'streaming', label: 'Streaming', href: '#' },
  { id: 'webhooks',  label: 'Webhooks',  href: '#' },
  { id: 'counterfactual', label: 'Counterfactual', href: '#' },
];
