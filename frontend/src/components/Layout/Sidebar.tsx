import React, { useState } from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';

interface SidebarItem {
  id: string;
  label: string;
  icon: React.ReactNode;
  href: string;
  badge?: number;
  children?: SidebarItem[];
}

interface SidebarProps {
  items: SidebarItem[];
  activeItem: string;
  onItemClick: (id: string) => void;
  collapsed?: boolean;
  onSettingsClick?: () => void;
}

/**
 * Enterprise Sidebar Navigation
 */
export const Sidebar: React.FC<SidebarProps> = ({
  items,
  activeItem,
  onItemClick,
  collapsed = false,
  onSettingsClick,
}) => {
  const [expandedItems, setExpandedItems] = useState<Set<string>>(new Set());

  const toggleExpand = (id: string) => {
    const newExpanded = new Set(expandedItems);
    if (newExpanded.has(id)) {
      newExpanded.delete(id);
    } else {
      newExpanded.add(id);
    }
    setExpandedItems(newExpanded);
  };

  return (
    <aside
      className={`app-shell__sidebar${collapsed ? ' app-shell__sidebar--collapsed' : ''}`}
    >
      {/* Sidebar Header */}
      <div
        style={{
          padding: 'var(--space-6) var(--space-4)',
          borderBottom: '1px solid var(--border-default)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
          {/* Glassmorphic CSS-only logo */}
          <div
            style={{
              width: '2rem',
              height: '2rem',
              borderRadius: 'var(--radius-md)',
              background: 'linear-gradient(135deg, rgba(255,255,255,0.9), rgba(255,255,255,0.1))',
              border: '1px solid rgba(255,255,255,0.2)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--color-primary-600)',
              fontWeight: '900',
              fontSize: '1.25rem',
              boxShadow: '0 0 15px rgba(59, 130, 246, 0.5)',
            }}
          >
            A
          </div>
          {!collapsed && (
            <div>
              <h1
                style={{
                  margin: 0,
                  fontSize: 'var(--font-lg)',
                  fontWeight: 'var(--weight-extrabold)',
                  letterSpacing: 'var(--letter-tighter)',
                  background: 'linear-gradient(90deg, #ffffff, #3b82f6)',
                  WebkitBackgroundClip: 'text',
                  WebkitTextFillColor: 'transparent',
                  backgroundClip: 'text',
                }}
              >
                AURA
              </h1>
              <p
                style={{
                  margin: '0.25rem 0 0 0',
                  fontSize: '0.625rem',
                  color: 'var(--text-tertiary)',
                  textTransform: 'uppercase',
                  letterSpacing: 'var(--letter-widest)',
                  fontWeight: 'var(--weight-medium)',
                }}
              >
                Analytics
              </p>
            </div>
          )}
        </div>
      </div>

      {/* Navigation Items */}
      <nav style={{ flex: 1, padding: 'var(--space-4) 0', display: 'flex', flexDirection: 'column' }}>
        {items.map((item) => (
          <SidebarItem
            key={item.id}
            item={item}
            isActive={activeItem === item.id}
            isExpanded={expandedItems.has(item.id)}
            onExpand={() => toggleExpand(item.id)}
            onClick={() => onItemClick(item.id)}
            collapsed={collapsed}
          />
        ))}
      </nav>

      {/* Sidebar Footer */}
      <div
        style={{
          padding: 'var(--space-4)',
          borderTop: '1px solid var(--border-default)',
          display: 'flex',
          justifyContent: 'center',
        }}
      >
        <button
          style={{
            width: '2.5rem',
            height: '2.5rem',
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-tertiary)',
            border: 'none',
            cursor: 'pointer',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
            transition: 'all var(--transition-fast)',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-primary)';
            e.currentTarget.style.color = 'var(--text-primary)';
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)';
            e.currentTarget.style.color = 'var(--text-secondary)';
          }}
          title="Settings"
          onClick={() => onSettingsClick?.()}
        >
          ⚙️
        </button>
      </div>
    </aside>
  );
};

interface SidebarItemProps {
  item: SidebarItem;
  isActive: boolean;
  isExpanded: boolean;
  onExpand: () => void;
  onClick: () => void;
  collapsed: boolean;
}

const SidebarItem: React.FC<SidebarItemProps> = ({
  item,
  isActive,
  isExpanded,
  onExpand,
  onClick,
  collapsed,
}) => {
  const hasChildren = item.children && item.children.length > 0;

  return (
    <div>
      <button
        onClick={() => {
          onClick();
          if (hasChildren) onExpand();
        }}
        style={{
          width: '100%',
          padding: 'var(--space-3) var(--space-4)',
          backgroundColor: isActive ? 'var(--color-primary-50)' : 'transparent',
          border: 'none',
          borderLeft: isActive ? '3px solid var(--color-primary-500)' : '3px solid transparent',
          cursor: 'pointer',
          display: 'flex',
          alignItems: 'center',
          gap: 'var(--space-3)',
          color: isActive ? 'var(--color-primary-600)' : 'var(--text-secondary)',
          transition: 'all var(--transition-fast)',
          fontSize: 'var(--font-sm)',
          fontWeight: isActive ? 'var(--weight-semibold)' : 'var(--weight-regular)',
        }}
        onMouseEnter={(e) => {
          if (!isActive) {
            e.currentTarget.style.backgroundColor = 'var(--bg-secondary)';
          }
        }}
        onMouseLeave={(e) => {
          if (!isActive) {
            e.currentTarget.style.backgroundColor = 'transparent';
          }
        }}
      >
        <span style={{ fontSize: 'var(--size-icon-md)' }}>{item.icon}</span>
        {!collapsed && (
          <>
            <span style={{ flex: 1, textAlign: 'left' }}>{item.label}</span>
            {item.badge && (
              <span
                style={{
                  backgroundColor: 'var(--color-error-500)',
                  color: 'white',
                  borderRadius: 'var(--radius-full)',
                  padding: '0 0.375rem',
                  fontSize: 'var(--font-xs)',
                  fontWeight: 'var(--weight-bold)',
                  minWidth: '1.25rem',
                  textAlign: 'center',
                }}
              >
                {item.badge}
              </span>
            )}
            {hasChildren && (
              <span
                style={{
                  transform: isExpanded ? 'rotate(180deg)' : 'rotate(0deg)',
                  transition: 'transform var(--transition-fast)',
                }}
              >
                ▼
              </span>
            )}
          </>
        )}
      </button>

      {/* Sub-items */}
      {hasChildren && isExpanded && !collapsed && (
        <div style={{ paddingLeft: 'var(--space-4)' }}>
          {item.children?.map((child) => (
            <button
              key={child.id}
              onClick={() => onClick()}
              style={{
                width: '100%',
                padding: 'var(--space-2) var(--space-4)',
                backgroundColor: isActive ? 'var(--color-primary-50)' : 'transparent',
                border: 'none',
                borderLeft: isActive ? '2px solid var(--color-primary-500)' : '2px solid transparent',
                cursor: 'pointer',
                textAlign: 'left',
                color: isActive ? 'var(--color-primary-600)' : 'var(--text-tertiary)',
                fontSize: 'var(--font-xs)',
                transition: 'all var(--transition-fast)',
              }}
              onMouseEnter={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = 'var(--text-secondary)';
                }
              }}
              onMouseLeave={(e) => {
                if (!isActive) {
                  e.currentTarget.style.color = 'var(--text-tertiary)';
                }
              }}
            >
              {child.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
};

export default Sidebar;
