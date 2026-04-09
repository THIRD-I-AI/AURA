import React from 'react';

/* ── SVG Icon set ─────────────────────────────────────────────────── */

const Icons = {
  dashboard: (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
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
}

/* ── Sidebar ──────────────────────────────────────────────────────── */

export const Sidebar: React.FC<SidebarProps> = ({
  items,
  activeItem,
  onItemClick,
  collapsed = false,
  onCollapsedChange,
  onSettingsClick,
}) => {
  return (
    <aside
      className={`app-shell__sidebar${collapsed ? ' app-shell__sidebar--collapsed' : ''}`}
      style={{ userSelect: 'none' }}
    >
      {/* ── Logo ──────────────────────────────────────────────── */}
      <div style={{
        height: 'var(--header-height)',
        display: 'flex',
        alignItems: 'center',
        padding: collapsed ? '0' : '0 var(--space-4)',
        justifyContent: collapsed ? 'center' : 'flex-start',
        borderBottom: '1px solid var(--border-subtle)',
        flexShrink: 0,
        gap: 'var(--space-3)',
      }}>
        {/* Logo mark */}
        <div style={{
          width: 28, height: 28,
          borderRadius: 'var(--radius-md)',
          background: 'linear-gradient(135deg, var(--accent) 0%, #7c3aed 100%)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontWeight: 700,
          fontSize: '13px',
          color: '#fff',
          flexShrink: 0,
          boxShadow: '0 0 12px rgba(59,130,246,0.4)',
          letterSpacing: '-0.5px',
        }}>
          A
        </div>
        {!collapsed && (
          <div>
            <div style={{
              fontSize: 'var(--font-md)',
              fontWeight: 700,
              letterSpacing: '-0.03em',
              background: 'linear-gradient(90deg, #e8eaf0 0%, var(--accent) 100%)',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
              lineHeight: 1.1,
            }}>
              AURA
            </div>
            <div style={{
              fontSize: '9px',
              color: 'var(--text-tertiary)',
              textTransform: 'uppercase',
              letterSpacing: '0.12em',
              marginTop: '1px',
            }}>
              Analytics
            </div>
          </div>
        )}
      </div>

      {/* ── Nav items ────────────────────────────────────────── */}
      <nav style={{
        flex: 1,
        padding: 'var(--space-2) 0',
        display: 'flex',
        flexDirection: 'column',
        overflowY: 'auto',
        overflowX: 'hidden',
      }}>
        {items.map((item) => {
          const isActive = activeItem === item.id;
          const icon = item.icon ?? NAV_ICON_MAP[item.id];

          return (
            <button
              key={item.id}
              onClick={() => onItemClick(item.id)}
              title={collapsed ? item.label : undefined}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: 'var(--space-3)',
                width: '100%',
                padding: collapsed
                  ? 'var(--space-2-5) 0'
                  : 'var(--space-2-5) var(--space-4)',
                justifyContent: collapsed ? 'center' : 'flex-start',
                background: isActive ? 'var(--bg-selected)' : 'transparent',
                border: 'none',
                borderLeft: isActive
                  ? '2px solid var(--accent)'
                  : '2px solid transparent',
                cursor: 'pointer',
                color: isActive ? '#93c5fd' : 'var(--text-tertiary)',
                fontFamily: 'var(--font-sans)',
                fontSize: 'var(--font-sm)',
                fontWeight: isActive ? 'var(--weight-medium)' : 'var(--weight-regular)',
                transition: 'all var(--dur-fast) var(--ease-out)',
                textAlign: 'left',
                borderRadius: isActive ? '0 var(--radius-md) var(--radius-md) 0' : 0,
                marginLeft: 0,
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)';
                  (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
                  (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
                }
              }}
            >
              <span style={{
                flexShrink: 0,
                display: 'flex',
                alignItems: 'center',
                opacity: isActive ? 1 : 0.7,
              }}>
                {icon}
              </span>

              {!collapsed && (
                <span style={{
                  flex: 1,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}>
                  {item.label}
                </span>
              )}

              {!collapsed && item.badge != null && item.badge > 0 && (
                <span style={{
                  background: 'var(--red)',
                  color: '#fff',
                  borderRadius: 'var(--radius-full)',
                  fontSize: '10px',
                  fontWeight: 700,
                  padding: '1px 5px',
                  flexShrink: 0,
                }}>
                  {item.badge}
                </span>
              )}
            </button>
          );
        })}
      </nav>

      {/* ── Footer ───────────────────────────────────────────── */}
      <div style={{
        padding: 'var(--space-3) var(--space-3)',
        borderTop: '1px solid var(--border-subtle)',
        display: 'flex',
        flexDirection: collapsed ? 'column' : 'row',
        alignItems: 'center',
        justifyContent: collapsed ? 'center' : 'space-between',
        gap: 'var(--space-2)',
      }}>
        {/* Settings */}
        <button
          onClick={() => onSettingsClick?.()}
          title="Settings"
          style={{
            width: 30, height: 30,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            background: activeItem === 'settings' ? 'var(--bg-selected)' : 'transparent',
            border: '1px solid transparent',
            borderRadius: 'var(--radius-md)',
            cursor: 'pointer',
            color: activeItem === 'settings' ? '#93c5fd' : 'var(--text-tertiary)',
            transition: 'all var(--dur-fast) var(--ease-out)',
          }}
          onMouseEnter={(e) => {
            (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)';
            (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
          }}
          onMouseLeave={(e) => {
            if (activeItem !== 'settings') {
              (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
              (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
            }
          }}
        >
          {Icons.settings}
        </button>

        {/* Collapse toggle */}
        {onCollapsedChange && (
          <button
            onClick={() => onCollapsedChange(!collapsed)}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            style={{
              width: 30, height: 30,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              background: 'transparent',
              border: '1px solid transparent',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              color: 'var(--text-tertiary)',
              transition: 'all var(--dur-fast) var(--ease-out)',
            }}
            onMouseEnter={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = 'var(--bg-hover)';
              (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-secondary)';
            }}
            onMouseLeave={(e) => {
              (e.currentTarget as HTMLButtonElement).style.background = 'transparent';
              (e.currentTarget as HTMLButtonElement).style.color = 'var(--text-tertiary)';
            }}
          >
            {collapsed ? Icons.expand : Icons.collapse}
          </button>
        )}
      </div>
    </aside>
  );
};

export default Sidebar;
