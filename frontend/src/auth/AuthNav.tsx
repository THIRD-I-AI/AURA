import { Link, useNavigate } from 'react-router-dom';
import { useAuth } from './AuthContext';

const linkStyle: React.CSSProperties = {
  color: 'var(--text-secondary)',
  textDecoration: 'none',
  fontSize: 'var(--font-sm)',
};

/**
 * Auth-aware entry/exit for public surfaces. Logged out: a way in (Sign in /
 * Sign up). Logged in: who you are, a clear path to your workspace, and — the
 * piece that was missing — a way to sign out. Keeps the SaaS easy to operate:
 * the user is never stranded without an obvious next action.
 */
export function AuthNav() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();

  return (
    <div
      data-testid="auth-nav"
      style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 'var(--space-4)', marginBottom: 'var(--space-6)' }}
    >
      {user ? (
        <>
          <span data-testid="auth-nav-user" style={{ fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
            {user.name || user.email || 'Signed in'}
          </span>
          <Link data-testid="auth-nav-workspace" to="/app" style={{ ...linkStyle, color: 'var(--accent)', fontWeight: 600 }}>
            Open workspace →
          </Link>
          <button
            data-testid="auth-nav-logout"
            onClick={() => { logout(); navigate('/'); }}
            style={{ ...linkStyle, background: 'none', border: 'none', cursor: 'pointer', padding: 0 }}
          >
            Sign out
          </button>
        </>
      ) : (
        <>
          <Link data-testid="auth-nav-login" to="/login" style={linkStyle}>Sign in</Link>
          <Link data-testid="auth-nav-signup" to="/signup" style={{ ...linkStyle, color: 'var(--accent)', fontWeight: 600 }}>
            Sign up
          </Link>
        </>
      )}
    </div>
  );
}
