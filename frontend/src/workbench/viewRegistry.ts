/* Workbench nav → classic page component registry (constants only — the
   ViewHost component lives in views.tsx to satisfy react-refresh). */
import { lazy, type ComponentType } from 'react';

const FilesAndData = lazy(() => import('../pages/FilesAndData'));
const QueryHistory = lazy(() => import('../pages/QueryHistory'));
const Library = lazy(() => import('../pages/Library'));
const Dashboards = lazy(() => import('../pages/Dashboards'));
const Lineage = lazy(() => import('../pages/Lineage'));
const Cost = lazy(() => import('../pages/Cost'));
const AgentPanel = lazy(() => import('../pages/AgentPanel'));
const PipelinesPanel = lazy(() => import('../pages/PipelinesPanel'));
const StreamingPanel = lazy(() => import('../pages/StreamingPanel'));
const WebhooksPanel = lazy(() => import('../pages/WebhooksPanel'));
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
  'Library': c(Library, true),
  'Query History': c(QueryHistory, true),
  'Audit Workbench': c(AuditService),
  'Counterfactuals': c(Counterfactual),
  'Exception Queue': c(ExceptionQueue),
  'Pipelines': c(PipelinesPanel, true),
  'Streaming': c(StreamingPanel, true),
  'Healing Queue': c(HealingQueue),
  'Webhooks': c(WebhooksPanel, true),
  'Cost': c(Cost),
  'Connectors': c(AgentPanel, true),
  'Files & Data': c(FilesAndData, true),
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
