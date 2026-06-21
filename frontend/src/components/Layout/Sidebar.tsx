import React from 'react';
import './Sidebar.css';
import { NAV_SECTIONS } from './nav';

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
  library: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M3.5 2.5h8A1.5 1.5 0 0113 4v12.5l-3.5-2.5L6 16.5V4a1.5 1.5 0 00-1.5-1.5z" stroke="currentColor" strokeWidth="1.5" strokeLinejoin="round" transform="translate(1.5 -0.5)"/>
      <path d="M4 3.5v12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  dashboards: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <rect x="1.5" y="1.5" width="15" height="15" rx="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M1.5 6.5h15M7 6.5v10" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  ),
  lineage: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <circle cx="4" cy="4" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="14" cy="9" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <circle cx="4" cy="14" r="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M6 4.5l6 3.5M6 13.5l6-3.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  cost: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <circle cx="9" cy="9" r="7.5" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M11.5 6.5c-.5-.8-1.4-1.2-2.5-1.2-1.5 0-2.5.8-2.5 1.9 0 2.6 5 1.3 5 3.8 0 1.1-1 1.9-2.5 1.9-1.1 0-2-.4-2.5-1.2M9 4v1.3M9 12.9v1.3" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
    </svg>
  ),
  webhooks: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M7.5 4.5a2.5 2.5 0 115 0c0 1.2-.8 2-1.5 3l-2.5 4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M5 9.5a3.5 3.5 0 103.5 4h5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <circle cx="14" cy="13.5" r="2" stroke="currentColor" strokeWidth="1.5"/>
    </svg>
  ),
  counterfactual: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M2 9h4l2.5-7 3 14L14 9h2.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  'audit-hitl': (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      {/* Scales of justice — human decision over signed findings */}
      <path d="M9 2v12M5 14.5h8" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M4.5 4.5h9" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
      <path d="M4.5 4.5L2.5 9a2 2 0 004 0L4.5 4.5zM13.5 4.5L11.5 9a2 2 0 004 0l-2-4.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    </svg>
  ),
  'audit-service': (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      {/* Shield + check — a signed, verifiable audit certificate */}
      <path d="M9 1.5l5.5 2.2v3.8c0 3.3-2.4 5.7-5.5 6.5-3.1-.8-5.5-3.2-5.5-6.5V3.7L9 1.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
      <path d="M6.4 8.8l1.7 1.7L11.8 6.7" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  'healing-queue': (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      {/* Heart — supervised self-healing */}
      <path d="M9 15.3C9 15.3 2.6 11.7 2.6 7.1A3.1 3.1 0 019 4.7a3.1 3.1 0 016.4 2.4c0 4.6-6.4 8.2-6.4 8.2Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round"/>
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
  terminal: (
    <svg width="var(--icon-lg)" height="var(--icon-lg)" viewBox="0 0 18 18" fill="none">
      <rect x="1.5" y="2.5" width="15" height="13" rx="2" stroke="currentColor" strokeWidth="1.5"/>
      <path d="M4.5 7l2.4 2-2.4 2M9 11h4.5" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
    </svg>
  ),
  chat:      Icons.chat,
  files:     Icons.files,
  queries:   Icons.queries,
  library:   Icons.library,
  dashboards: Icons.dashboards,
  lineage:   Icons.lineage,
  cost:      Icons.cost,
  agent:     Icons.agent,
  pipelines: Icons.pipelines,
  streaming: Icons.streaming,
  webhooks:  Icons.webhooks,
  counterfactual: Icons.counterfactual,
  'audit-hitl': Icons['audit-hitl'],
  'audit-service': Icons['audit-service'],
  'healing-queue': Icons['healing-queue'],
};

/* ── Types ────────────────────────────────────────────────────────── */

interface SidebarItem {
  id: string;
  label: string;
  icon?: React.ReactNode;
  href: string;
  badge?: number;
  /** Auditor-workbench section header this item sits under (S37c). */
  section?: string;
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

      {/* ── Nav (grouped into the six auditor-workbench sections) ── */}
      <nav className="sidebar-nav">
        {NAV_SECTIONS.map((section) => {
          const inSection = items.filter((it) => it.section === section);
          if (inSection.length === 0) return null;
          return (
            <div key={section} className="sidebar-nav__group">
              {!collapsed && <div className="sidebar-nav__heading">{section}</div>}
              {inSection.map((item) => {
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
            </div>
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
