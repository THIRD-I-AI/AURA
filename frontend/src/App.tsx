import { useState, useEffect, lazy, Suspense, memo } from 'react';
import './styles/design-system.css';
import './styles/components.css';
import './components/Layout/AppLayout.css';
import AppLayout, { type PageType } from './components/Layout/AppLayout';
import ChatInterface from './components/ChatInterface';
import FileUpload from './components/FileUploadPro';
import CommandPalette from './components/CommandPalette';
import Card, { CardHeader, CardBody } from './components/ui/Card';
import Button from './components/ui/Button';
import ErrorBoundary from './components/ui/ErrorBoundary';
import { KPISkeleton } from './components/ui/Skeleton';
import ToastContainer from './components/ui/Toast';
import { ToastProvider, useToast } from './contexts/ToastContext';
import { healthService, type HealthStatus } from './services/api';
import { useSystemHealth } from './hooks/useSystemHealth';
import { AuraProvider, useAuraStore } from './store';

const FilesAndData   = lazy(() => import('./pages/FilesAndData'));
const QueryHistory   = lazy(() => import('./pages/QueryHistory'));
const Library        = lazy(() => import('./pages/Library'));
const Dashboards     = lazy(() => import('./pages/Dashboards'));
const Lineage        = lazy(() => import('./pages/Lineage'));
const Cost           = lazy(() => import('./pages/Cost'));
const Settings       = lazy(() => import('./pages/Settings'));
const AgentPanel     = lazy(() => import('./pages/AgentPanel'));
const PipelinesPanel = lazy(() => import('./pages/PipelinesPanel'));
const StreamingPanel = lazy(() => import('./pages/StreamingPanel'));
const WebhooksPanel  = lazy(() => import('./pages/WebhooksPanel'));
const Counterfactual = lazy(() => import('./pages/Counterfactual'));
const ExceptionQueue = lazy(() => import('./components/HITL/ExceptionQueue'));
const LiveDashboard  = lazy(() => import('./components/LiveDashboard'));

const PageFallback = memo(function PageFallback() {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 'var(--space-16)', color: 'var(--text-tertiary)', fontSize: 'var(--font-sm)' }}>
      Loading…
    </div>
  );
});

/* ── KPI card ─────────────────────────────────────────────────────── */
const KPICard = memo(function KPICard({
  label,
  value,
  hint,
  trend,
  accentColor = 'var(--accent)',
}: {
  label: string;
  value: string;
  hint?: string;
  trend?: { direction: 'up' | 'down' | 'flat'; label: string };
  accentColor?: string;
}) {
  return (
    <div style={{
      background: 'var(--bg-surface)',
      border: '1px solid var(--border-default)',
      borderRadius: 'var(--radius-lg)',
      padding: 'var(--space-5)',
      display: 'flex',
      flexDirection: 'column',
      gap: 'var(--space-2)',
      transition: 'border-color var(--dur-fast)',
    }}
      onMouseEnter={e => (e.currentTarget.style.borderColor = 'var(--border-strong)')}
      onMouseLeave={e => (e.currentTarget.style.borderColor = 'var(--border-default)')}
    >
      <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', fontWeight: 500, letterSpacing: '0.06em', textTransform: 'uppercase' }}>
        {label}
      </span>
      <span style={{ fontSize: 'var(--font-3xl)', fontWeight: 700, color: 'var(--text-primary)', letterSpacing: '-0.03em', lineHeight: 1 }}>
        {value}
      </span>
      <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)' }}>
        {trend && (
          <span style={{
            fontSize: 'var(--font-xs)',
            fontWeight: 600,
            color: trend.direction === 'up' ? 'var(--green)' : trend.direction === 'down' ? 'var(--red)' : 'var(--text-tertiary)',
          }}>
            {trend.direction === 'up' ? '↑' : trend.direction === 'down' ? '↓' : '—'} {trend.label}
          </span>
        )}
        {hint && <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>{hint}</span>}
      </div>
      {/* Accent bar */}
      <div style={{ height: 2, background: accentColor, borderRadius: 1, opacity: 0.6, marginTop: 'var(--space-1)' }} />
    </div>
  );
});

/* ── Main App inner ───────────────────────────────────────────────── */
function AppInner() {
  const [currentPage, setCurrentPage] = useState<PageType>('dashboard');
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);

  const toast = useToast();
  const { state: { stats, statsLoading, statsError }, actions: { fetchStats, loadFilesFromStorage } } = useAuraStore();
  const systemHealth = useSystemHealth((status) => {
    if (!status.isOnline) {
      toast.warning('Backend offline', { message: 'Attempting to reconnect…', duration: 0 });
    } else {
      toast.success('Back online', { message: 'Connection restored.' });
      fetchStats();
    }
  });

  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { fetchStats(); loadFilesFromStorage(); }, []);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { if (statsError) toast.error('Stats unavailable', { message: statsError }); }, [statsError]);
  useEffect(() => {
    const id = setInterval(() => { if (currentPage === 'dashboard') fetchStats(); }, 30_000);
    return () => clearInterval(id);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentPage]);
  useEffect(() => {
    healthService.startMonitoring(setHealthStatus);
    return () => healthService.stopMonitoring(setHealthStatus);
  }, []);

  const fmt = (n: number) => n >= 1e6 ? `${(n/1e6).toFixed(1)}M` : n >= 1e3 ? `${(n/1e3).toFixed(1)}K` : String(n);

  const renderPage = () => {
    switch (currentPage) {
      case 'files':     return <Suspense fallback={<PageFallback />}><FilesAndData   setCurrentPage={setCurrentPage} /></Suspense>;
      case 'chat':      return <ChatInterface />;
      case 'queries':   return <Suspense fallback={<PageFallback />}><QueryHistory   setCurrentPage={setCurrentPage} /></Suspense>;
      case 'library':   return <Suspense fallback={<PageFallback />}><Library        setCurrentPage={setCurrentPage} /></Suspense>;
      case 'dashboards':return <Suspense fallback={<PageFallback />}><Dashboards /></Suspense>;
      case 'lineage':   return <Suspense fallback={<PageFallback />}><Lineage /></Suspense>;
      case 'cost':      return <Suspense fallback={<PageFallback />}><Cost /></Suspense>;
      case 'settings':  return <Suspense fallback={<PageFallback />}><Settings       setCurrentPage={setCurrentPage} /></Suspense>;
      case 'agent':     return <Suspense fallback={<PageFallback />}><AgentPanel     setCurrentPage={setCurrentPage} /></Suspense>;
      case 'pipelines': return <Suspense fallback={<PageFallback />}><PipelinesPanel setCurrentPage={setCurrentPage} /></Suspense>;
      case 'streaming': return <Suspense fallback={<PageFallback />}><StreamingPanel setCurrentPage={setCurrentPage} /></Suspense>;
      case 'webhooks':  return <Suspense fallback={<PageFallback />}><WebhooksPanel  setCurrentPage={setCurrentPage} /></Suspense>;
      case 'counterfactual': return <Suspense fallback={<PageFallback />}><Counterfactual /></Suspense>;
      case 'audit-hitl': return <Suspense fallback={<PageFallback />}><ExceptionQueue /></Suspense>;
      case 'dashboard':
      default:
        return (
          <>
            {/* ── KPI strip ─────────────────────────────────── */}
            {statsLoading ? (
              <div style={{ marginBottom: 'var(--space-6)' }}><KPISkeleton /></div>
            ) : (
              <div className="dashboard-grid--kpis" style={{ opacity: systemHealth.isOnline ? 1 : 0.65, transition: 'opacity 0.3s' }}>
                <KPICard
                  label="Total rows"
                  value={stats?.total_rows ? fmt(stats.total_rows) : '0'}
                  hint="Across all data sources"
                  trend={stats?.total_rows ? { direction: 'up', label: 'live' } : undefined}
                  accentColor="var(--accent)"
                />
                <KPICard
                  label="Active sources"
                  value={String(stats?.active_sources ?? 0)}
                  hint={`${stats?.file_sources ?? 0} files · ${((stats?.active_sources ?? 0) - (stats?.file_sources ?? 0))} connections`}
                  accentColor="var(--green)"
                />
                <KPICard
                  label="Queries run"
                  value={stats?.queries_run ? fmt(stats.queries_run) : '0'}
                  hint="This session"
                  trend={stats?.queries_run ? { direction: 'flat', label: 'session' } : undefined}
                  accentColor="var(--purple)"
                />
                <KPICard
                  label="System health"
                  value={
                    healthStatus?.status === 'healthy' ? 'Online'
                    : healthStatus?.status === 'degraded' ? 'Degraded'
                    : 'Offline'
                  }
                  hint={healthStatus?.status === 'healthy' ? 'All services operational' : 'Check services'}
                  accentColor={
                    healthStatus?.status === 'healthy' ? 'var(--green)'
                    : healthStatus?.status === 'degraded' ? 'var(--yellow)'
                    : 'var(--red)'
                  }
                />
              </div>
            )}

            {/* ── Live charts ───────────────────────────────── */}
            <div style={{ marginBottom: 'var(--space-6)' }}>
              <ErrorBoundary resetLabel="Reload charts">
                <Suspense fallback={<PageFallback />}>
                  <LiveDashboard />
                </Suspense>
              </ErrorBoundary>
            </div>

            {/* ── Quick start panels ────────────────────────── */}
            <div className="dashboard-grid--panels" style={{ marginBottom: 'var(--space-6)' }}>
              <Card>
                <CardHeader title="Data sources" subtitle="Connect files, databases, or APIs" />
                <CardBody>
                  <ol style={{ paddingLeft: 'var(--space-5)', color: 'var(--text-secondary)', fontSize: 'var(--font-sm)', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)', lineHeight: 'var(--line-relaxed)' }}>
                    <li>Choose source type (file, DB, API)</li>
                    <li>Provide credentials or upload file</li>
                    <li>Validate access and preview schema</li>
                    <li>Run first sync or query</li>
                  </ol>
                  <div style={{ marginTop: 'var(--space-4)', display: 'flex', gap: 'var(--space-2)' }}>
                    <Button variant="primary" size="sm" onClick={() => setCurrentPage('files')}>Get started</Button>
                    <Button variant="ghost" size="sm" onClick={() => window.open(import.meta.env.VITE_DOCS_URL || 'https://github.com/THIRD-I-AI/AURA#readme', '_blank')}>View docs</Button>
                  </div>
                </CardBody>
              </Card>
              <Card>
                <CardHeader title="Quick actions" subtitle="Jump to common workflows" />
                <CardBody>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                    {[
                      { label: 'Ask a question about your data', page: 'chat' as PageType, color: 'var(--accent)' },
                      { label: 'Run the AI Agent on a new task', page: 'agent' as PageType, color: 'var(--purple)' },
                      { label: 'Build an ETL transformation', page: 'pipelines' as PageType, color: 'var(--green)' },
                      { label: 'Monitor streaming pipelines', page: 'streaming' as PageType, color: 'var(--cyan)' },
                    ].map(({ label, page, color }) => (
                      <button
                        key={page}
                        onClick={() => setCurrentPage(page)}
                        style={{
                          display: 'flex',
                          alignItems: 'center',
                          gap: 'var(--space-3)',
                          padding: 'var(--space-2-5) var(--space-3)',
                          background: 'var(--bg-surface-2)',
                          border: '1px solid var(--border-subtle)',
                          borderRadius: 'var(--radius-md)',
                          cursor: 'pointer',
                          color: 'var(--text-secondary)',
                          fontSize: 'var(--font-sm)',
                          textAlign: 'left',
                          transition: 'all var(--dur-fast)',
                          fontFamily: 'var(--font-sans)',
                        }}
                        onMouseEnter={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = color; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-primary)'; }}
                        onMouseLeave={e => { (e.currentTarget as HTMLButtonElement).style.borderColor = 'var(--border-subtle)'; (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)'; }}
                      >
                        <span style={{ width: 6, height: 6, borderRadius: '50%', background: color, flexShrink: 0 }} />
                        {label}
                        <span style={{ marginLeft: 'auto', color: 'var(--text-disabled)' }}>→</span>
                      </button>
                    ))}
                  </div>
                </CardBody>
              </Card>
            </div>

            {/* ── Chat + Upload workspace ───────────────────── */}
            <div className="dashboard-grid--workspace">
              <div><ChatInterface /></div>
              <div>
                <FileUpload onFileUploaded={(r) => {
                  toast.success('File uploaded', { message: r.filename ?? 'Upload complete' });
                }} />
              </div>
            </div>
          </>
        );
    }
  };

  return (
    <AppLayout currentPage={currentPage} onPageChange={setCurrentPage}>
      {renderPage()}
      <ToastContainer />
      <CommandPalette onNavigate={setCurrentPage} />
    </AppLayout>
  );
}

function App() {
  return (
    <AuraProvider>
      <ToastProvider>
        <ErrorBoundary>
          <AppInner />
        </ErrorBoundary>
      </ToastProvider>
    </AuraProvider>
  );
}

export default App;
