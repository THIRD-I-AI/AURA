import React from 'react';
import './Sidebar.css';

/* ── SVG Icon set ─────────────────────────────────────────────────── */

const Icons = {
  dashboard: (
    <svg width="var(--icon-lg)" height="var(--icon-lg)" viewBox="0 0 18 18" fill="none">
      <rect x="1" y="1" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="11" y="1" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="1" y="11" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
      <rect x="11" y="11" width="6" height="6" rx="1.5" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  ),
  chat: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M15.5 10.5a1.5 1.5 0 01-1.5 1.5H5L2 15V3a1.5 1.5 0 011.5-1.5h10A1.5 1.5 0 0115.5 3v7.5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M5.5 6.5h7M5.5 9.5h4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  files: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M10 1.5H4A1.5 1.5 0 002.5 3v12A1.5 1.5 0 004 16.5h10a1.5 1.5 0 001.5-1.5V7L10 1.5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M10 1.5V7h5.5" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round"/>
      <path d="M5.5 10.5h7M5.5 13h4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  queries: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <circle cx="9" cy="9" r="7" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M9 5v4l2.5 2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  agent: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <rect x="2" y="5" width="14" height="10" rx="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M6 5V4a3 3 0 016 0v1" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="6.5" cy="10" r="1" fill="currentColor"/>
      <circle cx="11.5" cy="10" r="1" fill="currentColor"/>
      <path d="M7 13h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  pipelines: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <circle cx="3" cy="9" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="15" cy="4.5" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="15" cy="13.5" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M5 9h4M9 9l4-4.5M9 9l4 4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  streaming: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M1.5 11.5c1.5-4 3-6 4.5-6s3 4 4.5 4 3-6 4.5-6" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  settings: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <circle cx="9" cy="9" r="2.5" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M9 1.5v2M9 14.5v2M1.5 9h2M14.5 9h2M3.52 3.52l1.41 1.41M13.07 13.07l1.41 1.41M14.48 3.52l-1.41 1.41M4.93 13.07l-1.41 1.41" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  collapse: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M9 3L5 7l4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  expand: (
    <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
      <path d="M5 3l4 4-4 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
};

const NAV_ICON_MAP: Record<string, React.ReactNode> = {
  dashboard: Icons.dashboard,
  chat:      Icons.chat,
  files:     Icons.files,
  queries:   Icons.queries,
  agent:     Icons.agent,
  pipelines: Icons.pipelines,
  streaming: Icons.streaming,
};

/* ── Types ────────────────────────────────────────────────────────── */

interface SidebarItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  href: string;
  badge?: number;
}

interface SidebarProps {
  items: SidebarItem[];
  activeItem: string;
  onItemClick: (id: string) => void;
  collapsed?: boolean;
  onCollapsedChange?: (v: boolean) => void;
  onSettingsClick?: () => void;
  mobileOpen?: boolean;
}

/* ── Sidebar ──────────────────────────────────────────────────────── */

export const Sidebar: React.FC<SidebarProps> = ({
  items,
  activeItem,
  onItemClick,
  collapsed = false,
  onCollapsedChange,
  onSettingsClick,
  mobileOpen = false,
}) => {
  return (
    <aside className={[
      'app-shell__sidebar',
      collapsed && 'app-shell__sidebar--collapsed',
      mobileOpen && 'app-shell__sidebar--mobile-open',
    ].filter(Boolean).join(' ')}>

      {/* ── Logo ──────────────────────────────────────────────── */}
      <div className={`sidebar-logo${collapsed ? ' sidebar-logo--collapsed' : ''}`}>
        <div className="sidebar-logo__mark">A</div>
        {!collapsed && (
          <div className="sidebar-logo__text">
            <span className="sidebar-logo__name">AURA</span>
            <span className="sidebar-logo__sub">Analytics</span>
          </div>
        )}
      </div>

      {/* ── Nav ───────────────────────────────────────────────── */}
      <nav className="sidebar-nav">
        {items.map((item) => {
          const isActive = activeItem === item.id;
          const icon = item.icon ?? NAV_ICON_MAP[item.id];
          return (
            <button
              key={item.id}
              onClick={() => onItemClick(item.id)}
              title={collapsed ? item.label : undefined}
              aria-current={isActive ? 'page' : undefined}
              className={[
                'sidebar-nav-item',
                isActive && 'sidebar-nav-item--active',
                collapsed && 'sidebar-nav-item--collapsed',
              ].filter(Boolean).join(' ')}
            >
              <span className="sidebar-nav-item__icon">{icon}</span>
              {!collapsed && (
                <span className="sidebar-nav-item__label">{item.label}</span>
              )}
              {!collapsed && item.badge != null && item.badge > 0 && (
                <span className="sidebar-nav-item__badge">{item.badge}</span>
              )}
            </button>
          );
        })}
      </nav>

      {/* ── Footer ───────────────────────────────────────────── */}
      <div className={`sidebar-footer${collapsed ? ' sidebar-footer--collapsed' : ''}`}>
        <button
          onClick={() => onSettingsClick?.()}
          title="Settings"
          aria-label="Settings"
          className={[
            'sidebar-icon-btn',
            activeItem === 'settings' && 'sidebar-icon-btn--active',
          ].filter(Boolean).join(' ')}
        >
          {Icons.settings}
        </button>

        {onCollapsedChange && (
          <button
            onClick={() => onCollapsedChange(!collapsed)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            className="sidebar-icon-btn"
          >
            {collapsed ? Icons.expand : Icons.collapse}
          </button>
        )}
      </div>
    </aside>
  );
};

export default Sidebar;
