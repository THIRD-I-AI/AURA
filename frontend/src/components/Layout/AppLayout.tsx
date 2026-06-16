import React, { useState } from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';
import './AppLayout.css';
import Sidebar from './Sidebar';
import Header from './Header';
import Button from '../ui/Button';
import { useSystemHealth } from '../../hooks/useSystemHealth';
import { NAV_ITEMS } from './nav';

export type PageType =
  | 'dashboard'
  | 'chat'
  | 'files'
  | 'queries'
  | 'library'
  | 'dashboards'
  | 'lineage'
  | 'cost'
  | 'settings'
  | 'agent'
  | 'pipelines'
  | 'streaming'
  | 'webhooks'
  | 'counterfactual'
  | 'audit-hitl';

interface AppLayoutProps {
  children: React.ReactNode;
  currentPage: PageType;
  onPageChange: (page: PageType) => void;
}

const PAGE_META: Record<PageType, { title: string; subtitle: string }> = {
  dashboard:  { title: 'Engagements',        subtitle: 'Audit runs, signed certificates & live platform health' },
  chat:       { title: 'Chat',               subtitle: 'Ask questions about your data' },
  files:      { title: 'Files & Data',       subtitle: 'Manage uploaded files and data sources' },
  queries:    { title: 'Query History',      subtitle: 'View and replay previous SQL runs' },
  library:    { title: 'Library',            subtitle: 'Saved queries — star, rename, and reopen in chat' },
  dashboards: { title: 'Dashboards',         subtitle: 'Compose saved queries into reusable, refreshable views' },
  lineage:    { title: 'Lineage',            subtitle: 'Tables → queries → dashboards dependency graph' },
  cost:       { title: 'LLM Cost',           subtitle: 'Token usage by provider, model, and kind' },
  settings:   { title: 'Settings',           subtitle: 'Preferences and configuration' },
  agent:      { title: 'Agent',              subtitle: 'Agentic data engineering — one prompt does it all' },
  pipelines:  { title: 'ETL Pipelines',      subtitle: 'Build, run, and manage data transformation pipelines' },
  streaming:  { title: 'Streaming',          subtitle: 'Real-time data streaming with live metrics' },
  webhooks:   { title: 'Webhooks',           subtitle: 'Outbound subscriptions & inbound HTTP triggers' },
  counterfactual: { title: 'Counterfactual',     subtitle: "What would have happened if X had been different — causal estimate, refutation tests, adversarial review, hash-sealed audit" },
  'audit-hitl': { title: 'Audit Workbench',   subtitle: 'PCAOB AS 1215 exception review — signed AI findings, human decisions, WORM-chained' },
};

const AppLayout: React.FC<AppLayoutProps> = ({ children, currentPage, onPageChange }) => {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false);
  const systemHealth = useSystemHealth();

  const { title, subtitle } = PAGE_META[currentPage] ?? PAGE_META.dashboard;

  const handleNavClick = (id: string) => {
    onPageChange(id as PageType);
    setMobileSidebarOpen(false);
  };

  return (
    <div className="app-shell">
      {/* Mobile scrim */}
      <div
        className={`sidebar-scrim${mobileSidebarOpen ? ' sidebar-scrim--visible' : ''}`}
        onClick={() => setMobileSidebarOpen(false)}
        aria-hidden="true"
      />

      <Sidebar
        items={NAV_ITEMS}
        activeItem={currentPage}
        onItemClick={handleNavClick}
        collapsed={sidebarCollapsed}
        onCollapsedChange={setSidebarCollapsed}
        onSettingsClick={() => { onPageChange('settings'); setMobileSidebarOpen(false); }}
        mobileOpen={mobileSidebarOpen}
      />

      <div className="app-shell__content">
        <Header
          title={title}
          subtitle={subtitle}
          breadcrumbs={[{ label: 'AURA' }, { label: title }]}
          searchable
          isOnline={systemHealth.isOnline}
          onMobileMenuClick={() => setMobileSidebarOpen((v) => !v)}
          onSettingsClick={() => onPageChange('settings')}
          actions={
            <Button
              size="sm"
              variant="primary"
              leftIcon={
                <svg width="12" height="12" viewBox="0 0 12 12" fill="none">
                  <path d="M6 1v10M1 6h10" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
                </svg>
              }
              onClick={() => onPageChange('files')}
            >
              New
            </Button>
          }
        />

        <main className="app-shell__main">
          <div className="app-shell__main-inner">
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};

export default AppLayout;
