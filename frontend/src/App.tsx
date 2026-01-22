import { useState, useEffect } from 'react';
import './styles/design-system.css';
import './styles/components.css';
import AppLayout from './components/Layout/AppLayout';
import ChatInterface from './components/ChatInterface';
import FileUpload from './components/FileUploadPro';
import Card, { CardHeader, CardBody } from './components/ui/Card';
import Alert from './components/ui/Alert';
import Button from './components/ui/Button';
import { analyticsService, healthService, type DashboardStats, type HealthStatus } from './services/api';
import { useSystemHealth, type SystemHealthState } from './hooks/useSystemHealth';

/**
 * AURA - Enterprise Data Analytics Application
 * Professional UI with modern design system
 */
function App() {
  const [showAlert, setShowAlert] = useState(true);
  const [stats, setStats] = useState<DashboardStats | null>(null);
  const [healthStatus, setHealthStatus] = useState<HealthStatus | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showReconnecting, setShowReconnecting] = useState(false);

  // System health monitoring
  const systemHealth = useSystemHealth((status: SystemHealthState) => {
    if (!status.isOnline && !showReconnecting) {
      setShowReconnecting(true);
    } else if (status.isOnline && showReconnecting) {
      setShowReconnecting(false);
      // Auto-reload dashboard stats when reconnected
      fetchStatsInternal();
    }
  });

  // Fetch dashboard metrics on mount
  const fetchStatsInternal = async () => {
    try {
      setIsLoading(true);
      const data = await analyticsService.getDashboardStats();
      setStats(data);
      setError(null);
    } catch (err) {
      console.error('Failed to fetch dashboard stats:', err);
      setError('Backend services offline');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchStatsInternal();
  }, []);

  // Health monitoring
  useEffect(() => {
    const handleHealthUpdate = (status: HealthStatus) => {
      setHealthStatus(status);
    };

    healthService.startMonitoring(handleHealthUpdate);

    return () => {
      healthService.stopMonitoring(handleHealthUpdate);
    };
  }, []);

  const formatNumber = (num: number): string => {
    if (num >= 1_000_000) return `${(num / 1_000_000).toFixed(1)}M`;
    if (num >= 1_000) return `${(num / 1_000).toFixed(1)}K`;
    return num.toString();
  };

  const kpis = isLoading
    ? [
        { label: 'Total rows', value: '...', hint: 'Loading...' },
        { label: 'Active sources', value: '...', hint: 'Loading...' },
        { label: 'Queries run', value: '...', hint: 'Loading...' },
        { label: 'System health', value: '...', hint: 'Loading...' },
      ]
    : [
        {
          label: 'Total rows',
          value: stats?.total_rows ? formatNumber(stats.total_rows) : '—',
          hint: stats?.total_rows ? 'Across all sources' : 'Populates after first ingest',
        },
        {
          label: 'Active sources',
          value: stats?.active_sources.toString() || '0',
          hint: stats?.active_sources ? 'Connected and synced' : 'Connect a source to begin',
        },
        {
          label: 'Queries run',
          value: stats?.queries_run ? formatNumber(stats.queries_run) : '—',
          hint: stats?.queries_run ? 'Last 30 days' : 'Runs appear after execution',
        },
        {
          label: 'System health',
          value: healthStatus?.status === 'healthy' ? '✓' : healthStatus?.status === 'degraded' ? '⚠' : '✗',
          hint: healthStatus ? `${healthStatus.status.toUpperCase()}` : 'Checking...',
        },
      ];

  return (
    <AppLayout>
      {/* System Offline Alert */}
      {!systemHealth.isOnline && (
        <div style={{ marginBottom: 'var(--space-4)' }}>
          <Alert
            type="error"
            title="System Offline"
            message={showReconnecting ? 'Reconnecting...' : 'Backend services are not responding. Please check your connection.'}
            onClose={() => {}}
          />
        </div>
      )}

      {showAlert && (
        <div style={{ marginBottom: 'var(--space-4)' }}>
          <Alert
            type="info"
            title="Welcome to AURA"
            message="Connect a source or open chat to begin."
            action={{ label: 'Get Started', onClick: () => setShowAlert(false) }}
            onClose={() => setShowAlert(false)}
          />
        </div>
      )}

      {error && !systemHealth.isOnline && (
        <div style={{ marginBottom: 'var(--space-4)' }}>
          <Alert
            type="error"
            title="System Offline"
            message={error}
            onClose={() => setError(null)}
          />
        </div>
      )}

      {/* KPI Sparks */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
          gap: 'var(--space-4)',
          marginBottom: 'var(--space-5)',
          opacity: !systemHealth.isOnline ? 0.6 : 1,
          pointerEvents: !systemHealth.isOnline ? 'none' : 'auto',
          transition: 'all 300ms ease',
        }}
      >
        {kpis.map((kpi) => (
          <div key={kpi.label} style={{ gridColumn: 'span 3' }}>
            <Card>
              <CardBody>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                  <span style={{ color: 'var(--text-tertiary)', fontSize: 'var(--font-xs)' }}>{kpi.label}</span>
                  <span style={{ fontSize: 'var(--font-xl)', fontWeight: 'var(--weight-semibold)' }}>{kpi.value}</span>
                  <span style={{ color: 'var(--text-secondary)', fontSize: 'var(--font-xs)' }}>{kpi.hint}</span>
                </div>
              </CardBody>
            </Card>
          </div>
        ))}
      </div>

      {/* Overview + Activity */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
          gap: 'var(--space-4)',
          marginBottom: 'var(--space-5)',
          opacity: !systemHealth.isOnline ? 0.6 : 1,
          pointerEvents: !systemHealth.isOnline ? 'none' : 'auto',
          transition: 'all 300ms ease',
        }}
      >
        <div style={{ gridColumn: 'span 6' }}>
          <Card>
            <CardHeader title="Data sources" subtitle="Step through to connect your first source" />
            <CardBody>
              <ol style={{ margin: 0, paddingLeft: '1.25rem', color: 'var(--text-secondary)', display: 'flex', gap: 'var(--space-4)', flexWrap: 'wrap' }}>
                {['Choose source type', 'Provide credentials', 'Validate access', 'Run first sync'].map((step) => (
                  <li key={step} style={{ minWidth: '12rem' }}>{step}</li>
                ))}
              </ol>
              <div style={{ marginTop: 'var(--space-4)', display: 'flex', gap: 'var(--space-3)' }}>
                <Button variant="primary" size="sm">Start setup</Button>
                <Button variant="ghost" size="sm">View docs</Button>
              </div>
            </CardBody>
          </Card>
        </div>

        <div style={{ gridColumn: 'span 6' }}>
          <Card>
            <CardHeader title="Query activity" subtitle="Recent runs will appear here" />
            <CardBody>
              <ul style={{ margin: 0, paddingLeft: '1rem', color: 'var(--text-secondary)', display: 'flex', flexDirection: 'column', gap: 'var(--space-2)' }}>
                <li>No queries have been executed yet.</li>
                <li>Submit a query from chat to see history.</li>
                <li>Retention: last 30 runs will be listed.</li>
              </ul>
              <div style={{ marginTop: 'var(--space-4)' }}>
                <Button variant="secondary" size="sm">Open chat workspace</Button>
              </div>
            </CardBody>
          </Card>
        </div>
      </div>

      {/* Workspace Section */}
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: 'repeat(12, minmax(0, 1fr))',
          gap: 'var(--space-4)',
          alignItems: 'stretch',
          opacity: !systemHealth.isOnline ? 0.6 : 1,
          pointerEvents: !systemHealth.isOnline ? 'none' : 'auto',
          transition: 'all 300ms ease',
        }}
      >
        <div style={{ gridColumn: 'span 7', minWidth: 0 }}>
          <ChatInterface />
        </div>

        <div style={{ gridColumn: 'span 5', minWidth: 0 }}>
          <FileUpload
            onFileUploaded={(response) => {
              console.log('File uploaded successfully:', response);
              // Optionally refresh dashboard stats
            }}
          />
        </div>
      </div>
    </AppLayout>
  );
}

export default App;
