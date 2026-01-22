import React, { useState } from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';

interface HeaderProps {
  title: string;
  subtitle?: string;
  breadcrumbs?: Array<{ label: string; href?: string }>;
  actions?: React.ReactNode;
  searchable?: boolean;
  onSearch?: (query: string) => void;
  notificationCount?: number;
  userMenu?: React.ReactNode;
}

/**
 * Enterprise Header/Top Navigation Bar
 */
export const Header: React.FC<HeaderProps> = ({
  title,
  subtitle,
  breadcrumbs,
  actions,
  searchable = false,
  onSearch,
  notificationCount,
  userMenu,
}) => {
  const [searchOpen, setSearchOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const handleSearch = (query: string) => {
    setSearchQuery(query);
    onSearch?.(query);
  };

  return (
    <header
      style={{
        backgroundColor: 'var(--bg-primary)',
        borderBottom: '1px solid var(--border-default)',
        position: 'sticky',
        top: 0,
        zIndex: 'var(--z-fixed)',
        backdropFilter: 'blur(10px)',
      }}
    >
      <div
        style={{
          padding: 'var(--space-4) var(--space-6)',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 'var(--space-6)',
        }}
      >
        {/* Left Section - Title and Breadcrumbs */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {breadcrumbs && breadcrumbs.length > 0 && (
            <div
              style={{
                display: 'flex',
                gap: 'var(--space-2)',
                fontSize: 'var(--font-sm)',
                color: 'var(--text-tertiary)',
                marginBottom: 'var(--space-2)',
                overflow: 'auto',
              }}
            >
              {breadcrumbs.map((item, index) => (
                <React.Fragment key={index}>
                  {index > 0 && <span>/</span>}
                  {item.href ? (
                    <a
                      href={item.href}
                      style={{
                        color: 'var(--text-secondary)',
                        textDecoration: 'none',
                        cursor: 'pointer',
                      }}
                    >
                      {item.label}
                    </a>
                  ) : (
                    <span>{item.label}</span>
                  )}
                </React.Fragment>
              ))}
            </div>
          )}
          <h1
            style={{
              margin: 0,
              fontSize: 'var(--font-2xl)',
              fontWeight: 'var(--weight-bold)',
              color: 'var(--text-primary)',
            }}
          >
            {title}
          </h1>
          {subtitle && (
            <p
              style={{
                margin: 'var(--space-1) 0 0 0',
                fontSize: 'var(--font-sm)',
                color: 'var(--text-secondary)',
              }}
            >
              {subtitle}
            </p>
          )}
        </div>

        {/* Middle Section - Search */}
        {searchable && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              minWidth: '300px',
            }}
          >
            {searchOpen ? (
              <input
                autoFocus
                type="text"
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                onBlur={() => !searchQuery && setSearchOpen(false)}
                style={{
                  width: '100%',
                  padding: '0.5rem var(--space-4)',
                  border: '1px solid var(--border-default)',
                  borderRadius: 'var(--radius-md)',
                  fontSize: 'var(--font-sm)',
                }}
              />
            ) : (
              <button
                onClick={() => setSearchOpen(true)}
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  fontSize: 'var(--size-icon-md)',
                  color: 'var(--text-secondary)',
                }}
                title="Search"
              >
                🔍
              </button>
            )}
          </div>
        )}

        {/* Right Section - Actions and User Menu */}
        <div
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: 'var(--space-4)',
          }}
        >
          {/* Notifications */}
          <button
            style={{
              position: 'relative',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              fontSize: 'var(--size-icon-lg)',
              color: 'var(--text-secondary)',
              transition: 'color var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-primary)';
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-secondary)';
            }}
            title="Notifications"
          >
            🔔
            {notificationCount && notificationCount > 0 && (
              <span
                style={{
                  position: 'absolute',
                  top: '-0.25rem',
                  right: '-0.25rem',
                  backgroundColor: 'var(--color-error-500)',
                  color: 'white',
                  borderRadius: 'var(--radius-full)',
                  width: '1.25rem',
                  height: '1.25rem',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  fontSize: 'var(--font-xs)',
                  fontWeight: 'var(--weight-bold)',
                }}
              >
                {notificationCount}
              </span>
            )}
          </button>

          {/* Custom Actions */}
          {actions && <div style={{ display: 'flex', gap: 'var(--space-3)' }}>{actions}</div>}

          {/* User Menu */}
          {userMenu && userMenu}
        </div>
      </div>
    </header>
  );
};

export default Header;
