import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';

function initials(name?: string, email?: string): string {
  const source = (name || email || '').trim();
  if (!source) return 'AU';
  const parts = source.split(/[\s@.]+/).filter(Boolean);
  const letters = (parts[0]?.[0] ?? '') + (parts[1]?.[0] ?? '');
  return (letters || source[0]).toUpperCase();
}

const itemStyle: React.CSSProperties = {
  display: 'block',
  width: '100%',
  textAlign: 'left',
  padding: 'var(--space-2) var(--space-3)',
  background: 'none',
  border: 'none',
  color: 'var(--text-primary)',
  fontSize: 'var(--font-sm)',
  cursor: 'pointer',
};

/**
 * The top-right account menu — the standard place users look for "who am I",
 * settings, and sign out. Also the home for the Audit Service link, since the
 * certificate flow lives outside the dashboard's in-app pages. Replaces the
 * old static "AU" avatar so the app is actually operable: identity is visible
 * and there's always an obvious way out.
 */
export function UserMenu({ onSettingsClick }: { onSettingsClick?: () => void }) {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onDoc = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [open]);

  const go = (fn: () => void) => { setOpen(false); fn(); };

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        data-testid="user-menu-trigger"
        className="app-header__avatar"
        title={user ? (user.name || user.email || 'Account') : 'Sign in'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
        style={{ cursor: 'pointer', border: 'none' }}
      >
        {initials(user?.name, user?.email)}
      </button>

      {open && (
        <div
          data-testid="user-menu"
          role="menu"
          style={{
            position: 'absolute', right: 0, top: 'calc(100% + 8px)', minWidth: 220, zIndex: 50,
            background: 'var(--bg-surface)', border: '1px solid var(--border-strong)',
            borderRadius: 'var(--radius-md)', boxShadow: '0 8px 28px rgba(0,0,0,0.35)', overflow: 'hidden',
          }}
        >
          <div style={{ padding: 'var(--space-3)', borderBottom: '1px solid var(--border-default)' }}>
            <div data-testid="user-menu-name" style={{ fontWeight: 600, fontSize: 'var(--font-sm)' }}>
              {user?.name || 'Signed in'}
            </div>
            {user?.email && (
              <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>{user.email}</div>
            )}
          </div>

          {onSettingsClick && (
            <button data-testid="user-menu-settings" role="menuitem" style={itemStyle}
              onClick={() => go(onSettingsClick)}>
              Settings
            </button>
          )}
          <button data-testid="user-menu-audit" role="menuitem" style={itemStyle}
            onClick={() => go(() => navigate('/'))}>
            Audit Service
          </button>
          <button data-testid="user-menu-logout" role="menuitem"
            style={{ ...itemStyle, color: 'var(--red)', borderTop: '1px solid var(--border-default)' }}
            onClick={() => go(() => { logout(); navigate('/'); })}>
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
