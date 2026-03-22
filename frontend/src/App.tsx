import { useState, useEffect } from 'react';
import './styles/design-system.css';
import './styles/components.css';
import AppLayout, { type PageType } from './components/Layout/AppLayout';
import ChatInterface from './components/ChatInterface';
import FileUpload from './components/FileUploadPro';
import FilesAndData from './pages/FilesAndData';
import QueryHistory from './pages/QueryHistory';
import Settings from './pages/Settings';
import AgentPanel from './pages/AgentPanel';
import PipelinesPanel from './pages/PipelinesPanel';
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
  const [currentPage, setCurrentPage] = useState<PageType>('dashboard');
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
      fetchStatsInternal();
    }
  });

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
          value: stats?.total_rows ? formatNumber(stats.total_rows) : '',
          hint: stats?.total_rows ? 'Across all sources' : 'Populates after first ingest',
        },
        {
          label: 'Active sources',
          value: stats?.active_sources.toString() || '0',
          hint: stats?.active_sources ? 'Connected and synced' : 'Connect a source to begin',
        },
        {
          label: 'Queries run',
          value: stats?.queries_run ? formatNumber(stats.queries_run) : '',
          hint: stats?.queries_run ? 'Last 30 days' : 'Runs appear after execution',
        },
        {
          label: 'System health',
          value: healthStatus?.status === 'healthy' ? '' : healthStatus?.status === 'degraded' ? '' : '',
          hint: healthStatus?.status === 'healthy' ? 'All systems operational' : healthStatus?.status === 'degraded' ? 'Some issues detected' : 'System offline',
        },
      ];

  const renderPage = () => {
    switch (currentPage) {
      case 'files':
        return <FilesAndData setCurrentPage={setCurrentPage} />;
      case 'chat':
        return <ChatInterface />;
      case 'queries':
        return <QueryHistory setCurrentPage={setCurrentPage} />;
      case 'settings':
        return <Settings setCurrentPage={setCurrentPage} />;
      case 'agent':
        return <AgentPanel setCurrentPage={setCurrentPage} />;
      case 'pipelines':
        return <PipelinesPanel setCurrentPage={setCurrentPage} />;
      case 'dashboard':
      default:
        return (
          <>
            <div style={{display:'grid',gridTemplateColumns:'repeat(12,minmax(0,1fr))',gap:'var(--space-4)',marginBottom:'var(--space-5)',opacity:!systemHealth.isOnline?0.7:1,transition:'all 300ms ease'}}>
              {kpis.map((kpi)=>(<div key={kpi.label} style={{gridColumn:'span 3'}}><Card><CardBody><div style={{display:'flex',flexDirection:'column',gap:'var(--space-2)'}}><span style={{color:'var(--text-tertiary)',fontSize:'var(--font-xs)'}}>{kpi.label}</span><span style={{fontSize:'var(--font-xl)',fontWeight:'var(--weight-semibold)'}}>{kpi.value}</span><span style={{color:'var(--text-secondary)',fontSize:'var(--font-xs)'}}>{kpi.hint}</span></div></CardBody></Card></div>))}
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(12,minmax(0,1fr))',gap:'var(--space-4)',marginBottom:'var(--space-5)',transition:'all 300ms ease'}}>
              <div style={{gridColumn:'span 6'}}><Card><CardHeader title="Data sources" subtitle="Step through to connect your first source"/><CardBody><ol style={{margin:0,paddingLeft:'1.25rem',color:'var(--text-secondary)',display:'flex',gap:'var(--space-4)',flexWrap:'wrap'}}>{['Choose source type','Provide credentials','Validate access','Run first sync'].map((step)=>(<li key={step} style={{minWidth:'12rem'}}>{step}</li>))}</ol><div style={{marginTop:'var(--space-4)',display:'flex',gap:'var(--space-3)'}}><Button variant="primary" size="sm" onClick={() => setCurrentPage('files')}>Start setup</Button><Button variant="ghost" size="sm" onClick={() => window.open(import.meta.env.VITE_DOCS_URL || 'https://github.com/THIRD-I-AI/AURA#readme', '_blank')}>View docs</Button></div></CardBody></Card></div>
              <div style={{gridColumn:'span 6'}}><Card><CardHeader title="Query activity" subtitle="Recent runs will appear here"/><CardBody><ul style={{margin:0,paddingLeft:'1rem',color:'var(--text-secondary)',display:'flex',flexDirection:'column',gap:'var(--space-2)'}}><li>No queries have been executed yet.</li><li>Submit a query from chat to see history.</li><li>Retention: last 30 runs will be listed.</li></ul><div style={{marginTop:'var(--space-4)'}}><Button variant="secondary" size="sm" onClick={() => setCurrentPage('chat')}>Open chat workspace</Button><Button variant="primary" size="sm" onClick={() => setCurrentPage('agent')}>🤖 Launch Agent</Button></div></CardBody></Card></div>
            </div>
            <div style={{display:'grid',gridTemplateColumns:'repeat(12,minmax(0,1fr))',gap:'var(--space-4)',alignItems:'stretch',transition:'all 300ms ease'}}>
              <div style={{gridColumn:'span 7',minWidth:0}}><ChatInterface/></div>
              <div style={{gridColumn:'span 5',minWidth:0}}><FileUpload onFileUploaded={(response)=>{console.log('File uploaded successfully:',response);}}/></div>
            </div>
          </>
        );
    }
  };

  return (
    <AppLayout currentPage={currentPage} onPageChange={setCurrentPage}>
      {showAlert&&(<div style={{marginBottom:'var(--space-4)'}}><Alert type="info" title="System ready" message="Backend services are online. You can upload files and run queries." onClose={()=>setShowAlert(false)}/></div>)}
      {showReconnecting&&(<div style={{marginBottom:'var(--space-4)'}}><Alert type="warning" title="Reconnecting" message="Connection to backend services was lost. Attempting to reconnect..." onClose={()=>setShowReconnecting(false)}/></div>)}
      {error&&(<div style={{marginBottom:'var(--space-4)'}}><Alert type="error" title="System Offline" message={error} onClose={()=>setError(null)}/></div>)}
      {renderPage()}
    </AppLayout>
  );
}

export default App;