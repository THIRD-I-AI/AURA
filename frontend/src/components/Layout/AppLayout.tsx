import React from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';
import './Header.css';
import Sidebar from './Sidebar';
import Header from './Header';
import Button from '../ui/Button';

export type PageType = 'dashboard' | 'chat' | 'files' | 'queries' | 'settings' | 'agent';

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
  const [notificationCount] = React.useState(3);

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
    };
    return titles[currentPage as PageType];
  };

  const pageInfo = getPageTitle();
  const systemStatus = 'Waiting for checks';

  return (
    <div
      style={{
        display: 'flex',
        height: '100vh',
        backgroundColor: 'var(--bg-secondary)',
        overflow: 'hidden',
      }}
    >
      {/* Sidebar */}
      <Sidebar
        items={sidebarItems}
        activeItem={currentPage}
        onItemClick={(id) => onPageChange(id as PageType)}
        collapsed={sidebarCollapsed}
        onSettingsClick={() => onPageChange('settings')}
      />

      {/* Main Content Area */}
      <div
        style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          overflow: 'auto',
        }}
      >
        {/* Header */}
        <Header
          title={pageInfo.title}
          subtitle={pageInfo.subtitle}
          breadcrumbs={[
            { label: 'Home', href: '#' },
            { label: pageInfo.title },
          ]}
          searchable={currentPage === 'chat' || currentPage === 'files'}
          onSearch={(query) => console.log('Search:', query)}
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
        <main
          style={{
            flex: 1,
            padding: 'var(--space-6)',
            overflow: 'auto',
          }}
        >
          <div style={{ maxWidth: '100%', margin: 0, width: '100%' }}>
            {children}
          </div>
        </main>
      </div>
    </div>
  );
};

export default AppLayout;
