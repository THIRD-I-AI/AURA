import React from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';
import './AppLayout.css';
import './Header.css';
import Sidebar from './Sidebar';
import Header from './Header';
import Button from '../ui/Button';
import { useSystemHealth } from '../../hooks/useSystemHealth';

export type PageType = 'dashboard' | 'chat' | 'files' | 'queries' | 'settings' | 'agent' | 'pipelines';

interface SidebarItem {
  id: string;
  label: string;
  icon: React.ReactNode;
  href: string;
}

interface AppLayoutProps {
  children: React.ReactNode;
  currentPage: PageType;
  onPageChange: (page: PageType) => void;
}

/**
 * Enterprise Application Shell
 * Main layout container with professional design
 */
const AppLayout: React.FC<AppLayoutProps> = ({ children, currentPage, onPageChange }) => {
  const [sidebarCollapsed] = React.useState(false);
  const systemHealth = useSystemHealth();

  const notificationCount = 0; // Real notifications not yet implemented

  const sidebarItems: SidebarItem[] = [
    {
      id: 'dashboard',
      label: 'Dashboard',
      icon: '📊',
      href: '#',
    },
    {
      id: 'chat',
      label: 'Chat',
      icon: '💬',
      href: '#',
    },
    {
      id: 'files',
      label: 'Files & Data',
      icon: '📁',
      href: '#',
    },
    {
      id: 'queries',
      label: 'Query History',
      icon: '📋',
      href: '#',
    },
    {
      id: 'agent',
      label: 'Agent',
      icon: '🤖',
      href: '#',
    },
    {
      id: 'pipelines',
      label: 'ETL Pipelines',
      icon: '⚙️',
      href: '#',
    },
  ];

  const getPageTitle = (): { title: string; subtitle: string } => {
    const titles: Record<PageType, { title: string; subtitle: string }> = {
      dashboard: {
        title: 'Dashboard',
        subtitle: 'Welcome back! Here\'s your analytics overview.',
      },
      chat: {
        title: 'Chat',
        subtitle: 'Ask questions about your data',
      },
      files: {
        title: 'Files & Data',
        subtitle: 'Manage your uploaded files and data sources',
      },
      queries: {
        title: 'Query History',
        subtitle: 'View and manage your previous queries',
      },
      settings: {
        title: 'Settings',
        subtitle: 'Manage your preferences and configurations',
      },
      agent: {
        title: 'Agent',
        subtitle: 'Agentic data engineering — one prompt does it all',
      },
      pipelines: {
        title: 'ETL Pipelines',
        subtitle: 'Build, run, and manage data transformation pipelines',
      },
    };
    return titles[currentPage as PageType];
  };

  const pageInfo = getPageTitle();
  const systemStatus = systemHealth.isOnline ? 'All systems online' : 'Offline';

  return (
    <div className="app-shell">
      {/* Sidebar */}
      <Sidebar
        items={sidebarItems}
        activeItem={currentPage}
        onItemClick={(id) => onPageChange(id as PageType)}
        collapsed={sidebarCollapsed}
        onSettingsClick={() => onPageChange('settings')}
      />

      {/* Main Content Area */}
      <div className="app-shell__content">
        {/* Header */}
        <Header
          title={pageInfo.title}
          subtitle={pageInfo.subtitle}
          breadcrumbs={[
            { label: 'Home' },
            { label: pageInfo.title },
          ]}
          searchable={currentPage === 'chat' || currentPage === 'files'}
          onSearch={() => { /* search handled at page level */ }}
          notificationCount={notificationCount}
          actions={
            <>
              <span className="app-header__status-badge">
                System status: {systemStatus}
              </span>
              <Button
                size="md"
                variant="primary"
                leftIcon="+"
                onClick={() => onPageChange('files')}
              >
                New
              </Button>
            </>
          }
        />

        {/* Page Content */}
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
