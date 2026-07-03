/* One app: the classic pages mount INSIDE the Workbench shell. Each view runs
   under the same providers App.tsx used (AuraProvider + ToastProvider) and
   behind an error boundary, so one incompatible page degrades to an honest
   fallback instead of taking down the cockpit. Registry: viewRegistry.ts. */
import { Component, Suspense, type ReactNode } from 'react';
import { AuraProvider } from '../store';
import { ToastProvider } from '../contexts/ToastContext';
import ToastContainer from '../components/ui/Toast';
import { PAGE_ID_TO_NAV, VIEW_REGISTRY } from './viewRegistry';

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
  const setCurrentPage = (page: string) => { const n = PAGE_ID_TO_NAV[page]; if (n) onNavigate(n); };
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
