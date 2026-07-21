/* Workbench nav → native panels + (remaining) classic page components. Constants
   only — the ViewHost component lives in views.tsx to satisfy react-refresh.
   Native terminal-authority panels live in ./panels; the rest still embed the
   classic pages until they're rebuilt. */
import { lazy, type ComponentType } from 'react';

// Native terminal-authority panels (rebuilt to match the Cockpit).
const FilesAndData = lazy(() => import('./panels/FilesAndDataPanel'));
const QueryHistory = lazy(() => import('./panels/QueryHistoryPanel'));
const Library = lazy(() => import('./panels/LibraryPanel'));
const Connectors = lazy(() => import('./panels/ConnectorsPanel'));
const Cost = lazy(() => import('./panels/CostPanel'));
const WebhooksPanel = lazy(() => import('./panels/WebhooksPanel'));
const Dashboards = lazy(() => import('./panels/DashboardsPanel'));
const StreamingPanel = lazy(() => import('./panels/StreamingPanel'));
const Lineage = lazy(() => import('./panels/LineagePanel'));

// Still-embedded classic pages (pending native rebuild).
const PipelinesPanel = lazy(() => import('../pages/PipelinesPanel'));
const Counterfactual = lazy(() => import('../pages/Counterfactual'));
const AuditService = lazy(() => import('../pages/AuditService'));
const ExceptionQueue = lazy(() => import('../components/HITL/ExceptionQueue'));
const HealingQueue = lazy(() => import('../pages/HealingQueue'));
const ChatInterface = lazy(() => import('../components/ChatInterface'));

export type ViewEntry = { component: ComponentType<Record<string, unknown>>; needsSetPage?: boolean };
const c = (component: unknown, needsSetPage = false): ViewEntry =>
  ({ component: component as ViewEntry['component'], needsSetPage });

export const VIEW_REGISTRY: Record<string, ViewEntry> = {
  'Ask AURA': c(ChatInterface),
  'Dashboards': c(Dashboards),
  'Library': c(Library),
  'Query History': c(QueryHistory),
  'Audit Workbench': c(AuditService),
  'Counterfactuals': c(Counterfactual),
  'Exception Queue': c(ExceptionQueue),
  'Pipelines': c(PipelinesPanel, true),
  'Streaming': c(StreamingPanel),
  'Healing Queue': c(HealingQueue),
  'Webhooks': c(WebhooksPanel),
  'Cost': c(Cost),
  'Connectors': c(Connectors),
  'Files & Data': c(FilesAndData),
  'Lineage': c(Lineage),
};

/* Classic pages navigate via setCurrentPage(pageId) — translate to nav names. */
export const PAGE_ID_TO_NAV: Record<string, string> = {
  files: 'Files & Data', chat: 'Ask AURA', queries: 'Query History', library: 'Library',
  dashboards: 'Dashboards', lineage: 'Lineage', cost: 'Cost', agent: 'Connectors',
  pipelines: 'Pipelines', streaming: 'Streaming', webhooks: 'Webhooks',
  counterfactual: 'Counterfactuals', 'audit-service': 'Audit Workbench',
  'audit-hitl': 'Exception Queue', 'healing-queue': 'Healing Queue',
};
