import React, { useState } from 'react';
import '../../styles/design-system.css';
import '../../styles/components.css';
import './Header.css';

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
 * Uses CSS classes for responsive scaling at all viewport widths.
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
    <header className="app-header">
      <div className="app-header__inner">
        {/* Left Section – Title & Breadcrumbs (shrinks gracefully) */}
        <div className="app-header__left">
          {breadcrumbs && breadcrumbs.length > 0 && (
            <div className="app-header__breadcrumbs">
              {breadcrumbs.map((item, index) => (
                <React.Fragment key={index}>
                  {index > 0 && <span>/</span>}
                  {item.href ? (
                    <a href={item.href}>{item.label}</a>
                  ) : (
                    <span>{item.label}</span>
                  )}
                </React.Fragment>
              ))}
            </div>
          )}
          <h1 className="app-header__title">{title}</h1>
          {subtitle && <p className="app-header__subtitle">{subtitle}</p>}
        </div>

        {/* Middle Section – Search (fixed width, collapses on small screens) */}
        {searchable && (
          <div className="app-header__search">
            {searchOpen ? (
              <input
                autoFocus
                type="text"
                className="app-header__search-input"
                placeholder="Search..."
                value={searchQuery}
                onChange={(e) => handleSearch(e.target.value)}
                onBlur={() => !searchQuery && setSearchOpen(false)}
              />
            ) : (
              <button
                className="app-header__search-btn"
                onClick={() => setSearchOpen(true)}
                title="Search"
              >
                🔍
              </button>
            )}
          </div>
        )}

        {/* Right Section – Notifications + Actions (never shrinks) */}
        <div className="app-header__right">
          <button className="app-header__notification-btn" title="Notifications">
            🔔
            {notificationCount != null && notificationCount > 0 && (
              <span className="app-header__notification-badge">{notificationCount}</span>
            )}
          </button>

          {actions && <div className="app-header__actions">{actions}</div>}

          {userMenu && userMenu}
        </div>
      </div>
    </header>
  );
};

export default Header;
