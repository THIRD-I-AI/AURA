import React, { useState } from 'react';
import './Header.css';
import './AppLayout.css';
import WorkspacePicker from '../WorkspacePicker';
import { UserMenu } from '../../auth/UserMenu';

interface HeaderProps {
  title: string;
  subtitle?: string;
  breadcrumbs?: Array<{ label: string; href?: string }>;
  actions?: React.ReactNode;
  searchable?: boolean;
  onSearch?: (query: string) => void;
  notificationCount?: number;
  isOnline?: boolean;
  onMobileMenuClick?: () => void;
  onSettingsClick?: () => void;
}

const BellIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <path d="M8 1.5A4.5 4.5 0 003.5 6v3.5L2 11h12l-1.5-1.5V6A4.5 4.5 0 008 1.5z" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round"/>
    <path d="M6.5 13a1.5 1.5 0 003 0" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
  </svg>
);

const SearchIcon = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
    <circle cx="6" cy="6" r="4.5" stroke="currentColor" strokeWidth="1.3"/>
    <path d="M9.5 9.5l2.5 2.5" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round"/>
  </svg>
);

const HamburgerIcon = () => (
  <svg width="16" height="16" viewBox="0 0 16 16" fill="none">
    <path d="M2 4h12M2 8h12M2 12h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round"/>
  </svg>
);

export const Header: React.FC<HeaderProps> = ({
  title,
  breadcrumbs,
  actions,
  searchable = true,
  onSearch,
  notificationCount,
  isOnline = true,
  onMobileMenuClick,
  onSettingsClick,
}) => {
  const [searchQuery, setSearchQuery] = useState('');

  return (
    <header className="app-header">
      <div className="app-header__inner">

        {/* Mobile menu toggle */}
        {onMobileMenuClick && (
          <button
            className="sidebar-mobile-toggle"
            onClick={onMobileMenuClick}
            aria-label="Toggle navigation"
          >
            <HamburgerIcon />
          </button>
        )}

        {/* Left: breadcrumbs + title */}
        <div className="app-header__left">
          {breadcrumbs && breadcrumbs.length > 1 && (
            <div className="app-header__breadcrumbs">
              {breadcrumbs.map((item, i) => (
                <React.Fragment key={i}>
                  {i > 0 && <span>/</span>}
                  {item.href
                    ? <a href={item.href}>{item.label}</a>
                    : <span style={{ color: i === breadcrumbs.length - 1 ? 'var(--text-secondary)' : undefined }}>{item.label}</span>
                  }
                </React.Fragment>
              ))}
            </div>
          )}
          <h1 className="app-header__title">{title}</h1>
        </div>

        {/* Center: search */}
        {searchable && (
          <div className="app-header__search-wrapper">
            <span className="app-header__search-icon">
              <SearchIcon />
            </span>
            <input
              type="search"
              className="app-header__search-input"
              placeholder="Search…"
              value={searchQuery}
              onChange={(e) => {
                setSearchQuery(e.target.value);
                onSearch?.(e.target.value);
              }}
            />
          </div>
        )}

        {/* Right: workspace + status + notifications + actions + avatar */}
        <div className="app-header__right">
          <WorkspacePicker />

          <div className="app-header__status-badge">
            <span
              className={`status-dot ${isOnline ? 'status-dot--live' : 'status-dot--offline'}`}
              style={{ width: 6, height: 6 }}
            />
            <span>{isOnline ? 'Online' : 'Offline'}</span>
          </div>

          <button className="app-header__icon-btn" title="Notifications" aria-label="Notifications">
            <BellIcon />
            {notificationCount != null && notificationCount > 0 && (
              <span className="app-header__notification-badge">{notificationCount}</span>
            )}
          </button>

          {actions && (
            <div className="app-header__actions">{actions}</div>
          )}

          <UserMenu onSettingsClick={onSettingsClick} />
        </div>
      </div>
    </header>
  );
};

export default Header;
