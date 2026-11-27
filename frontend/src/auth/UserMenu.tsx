import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { Avatar } from '@/components/ui-kit/avatar';

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
        className="cursor-pointer rounded-full outline-none transition-transform hover:scale-105 focus-visible:ring-[3px] focus-visible:ring-ring/50"
        title={user ? (user.name || user.email || 'Account') : 'Sign in'}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((v) => !v)}
      >
        <Avatar name={user?.name} email={user?.email} seed={user?.sub} size="sm" />
      </button>

      {open && (
        <div
          data-testid="user-menu"
          role="menu"
          style={{
            position: 'absolute', right: 0, top: 'calc(100% + 8px)', minWidth: 220, zIndex: 50,
            background: 'var(--bg-surface)', border: '1px solid var(--border-strong)',
            borderRadius: 0, overflow: 'hidden',
          }}
        >
          <div style={{ padding: 'var(--space-3)', borderBottom: '1px solid var(--border-default)', display: 'flex', alignItems: 'center', gap: 'var(--space-3)' }}>
            <Avatar name={user?.name} email={user?.email} seed={user?.sub} size="md" />
            <div style={{ minWidth: 0 }}>
              <div data-testid="user-menu-name" style={{ fontWeight: 600, fontSize: 'var(--font-sm)' }}>
                {user?.name || 'Signed in'}
              </div>
              {user?.email && (
                <div style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>{user.email}</div>
              )}
            </div>
          </div>

          {onSettingsClick && (
            <button data-testid="user-menu-settings" role="menuitem" style={itemStyle}
              onClick={() => go(onSettingsClick)}>
              Settings
            </button>
          )}
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
