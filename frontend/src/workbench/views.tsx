/* One app: the classic pages mount INSIDE the Workbench shell. Each view runs
   under the same providers App.tsx used (AuraProvider + ToastProvider) and
   behind an error boundary, so one incompatible page degrades to an honest
   fallback instead of taking down the cockpit. */
import { Component, Suspense, lazy, type ComponentType, type ReactNode } from 'react';
import { AuraProvider } from '../store';
import { ToastProvider } from '../contexts/ToastContext';
import ToastContainer from '../components/ui/Toast';

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

type SetPage = (page: string) => void;
type ViewEntry = { component: ComponentType<Record<string, unknown>>; needsSetPage?: boolean };

/* Workbench nav name → classic page component. Views absent here fall back
   to the stub card in Workbench.tsx. */
export const VIEW_REGISTRY: Record<string, ViewEntry> = {
  'Ask AURA': { component: ChatInterface as ViewEntry['component'] },
  'Dashboards': { component: Dashboards as ViewEntry['component'] },
  'Library': { component: Library as ViewEntry['component'], needsSetPage: true },
  'Query History': { component: QueryHistory as ViewEntry['component'], needsSetPage: true },
  'Audit Workbench': { component: AuditService as ViewEntry['component'] },
  'Counterfactuals': { component: Counterfactual as ViewEntry['component'] },
  'Exception Queue': { component: ExceptionQueue as ViewEntry['component'] },
  'Pipelines': { component: PipelinesPanel as ViewEntry['component'], needsSetPage: true },
  'Streaming': { component: StreamingPanel as ViewEntry['component'], needsSetPage: true },
  'Healing Queue': { component: HealingQueue as ViewEntry['component'] },
  'Webhooks': { component: WebhooksPanel as ViewEntry['component'], needsSetPage: true },
  'Cost': { component: Cost as ViewEntry['component'] },
  'Connectors': { component: AgentPanel as ViewEntry['component'], needsSetPage: true },
  'Files & Data': { component: FilesAndData as ViewEntry['component'], needsSetPage: true },
  'Lineage': { component: Lineage as ViewEntry['component'] },
};

/* Classic pages navigate via setCurrentPage(pageId) — translate back to nav names. */
const PAGE_ID_TO_NAV: Record<string, string> = {
  files: 'Files & Data', chat: 'Ask AURA', queries: 'Query History', library: 'Library',
  dashboards: 'Dashboards', lineage: 'Lineage', cost: 'Cost', agent: 'Connectors',
  pipelines: 'Pipelines', streaming: 'Streaming', webhooks: 'Webhooks',
  counterfactual: 'Counterfactuals', 'audit-service': 'Audit Workbench',
  'audit-hitl': 'Exception Queue', 'healing-queue': 'Healing Queue',
};

class ViewBoundary extends Component<{ nav: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidUpdate(prev: { nav: string }) {
    if (prev.nav !== this.props.nav && this.state.failed) this.setState({ failed: false });
  }
  render() {
    if (this.state.failed) {
      return (
        <div style={{ background: 'var(--surface)', border: '1px dashed var(--danger)', borderRadius: 10, padding: 28, textAlign: 'center', fontSize: 12.5, color: 'var(--text2)', lineHeight: 1.7 }}>
          The {this.props.nav} module failed to render inside the workbench shell.
          <br />This is a bug — report it. The module itself is unaffected.
        </div>
      );
    }
    return this.props.children;
  }
}

export function ViewHost({ nav, onNavigate }: { nav: string; onNavigate: (nav: string) => void }) {
  const entry = VIEW_REGISTRY[nav];
  if (!entry) return null;
  const V = entry.component;
  const setCurrentPage: SetPage = (page) => { const n = PAGE_ID_TO_NAV[page]; if (n) onNavigate(n); };
  return (
    <div data-testid="wb-view" style={{ minHeight: 0 }}>
      <ViewBoundary nav={nav}>
        <AuraProvider>
          <ToastProvider>
            <Suspense fallback={<div style={{ padding: 24, fontSize: 12.5, color: 'var(--text3)' }}>Loading {nav}…</div>}>
              {entry.needsSetPage ? <V setCurrentPage={setCurrentPage} /> : <V />}
            </Suspense>
            <ToastContainer />
          </ToastProvider>
        </AuraProvider>
      </ViewBoundary>
    </div>
  );
}
