import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button } from '../components/ui/Button';
import { useViewport } from '../shell/ViewportProvider';
import { useAuth } from './AuthContext';
import './AuthForm.css';

type Mode = 'login' | 'signup';

const inputStyle: React.CSSProperties = {
  width: '100%',
  padding: 'var(--space-3)',
  fontSize: 'var(--font-md)',
  borderRadius: 'var(--radius-md)',
  border: '1px solid var(--border-strong)',
  background: 'var(--bg-base)',
  color: 'var(--text-primary)',
  marginTop: 'var(--space-1)',
};

const labelStyle: React.CSSProperties = {
  display: 'block',
  textAlign: 'left',
  marginBottom: 'var(--space-3)',
  fontSize: 'var(--font-sm)',
  color: 'var(--text-secondary)',
};

/**
 * One form for both login and signup (kept simple on purpose — the SaaS should
 * feel effortless). Signup also collects a name; on success both flows land the
 * user on the app (or wherever they were headed before being asked to log in).
 */
export function AuthForm({ mode }: { mode: Mode }) {
  const { login, register } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const wide = useViewport().atLeast('standard');
  const from = (location.state as { from?: string } | null)?.from ?? '/app';

  const [name, setName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === 'signup';

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (isSignup) {
        await register(name.trim(), email.trim(), password);
      } else {
        await login(email.trim(), password);
      }
      navigate(from, { replace: true });
    } catch (err) {
      setError(
        err instanceof Error
          ? (isSignup ? `Could not create your account: ${err.message}` : 'Wrong email or password.')
          : 'Something went wrong. Please try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`auth-pane${wide ? ' auth-pane--split' : ''}`}>
      {wide && (
        <div className="auth-pane__brand">
          <div className="auth-pane__brand-inner">
            <h2 className="auth-pane__brandmark">AURA</h2>
            <p className="auth-pane__valueprop">
              Ask your data in plain English. Get signed, verifiable answers.
            </p>
          </div>
        </div>
      )}
      <div className="auth-pane__form">
        <div data-testid="auth-form" style={{ maxWidth: 420, margin: '0 auto', width: '100%' }}>
      <h1 style={{ fontSize: 'var(--font-2xl)', textAlign: 'center', marginBottom: 'var(--space-2)' }}>
        {isSignup ? 'Create your account' : 'Welcome back'}
      </h1>
      <p style={{ textAlign: 'center', color: 'var(--text-secondary)', marginBottom: 'var(--space-6)' }}>
        {isSignup ? 'Check your data in under a minute.' : 'Sign in to your workspace.'}
      </p>

      <form onSubmit={onSubmit}>
        {isSignup && (
          <label style={labelStyle}>
            Name
            <input
              data-testid="auth-name"
              type="text"
              autoComplete="name"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              style={inputStyle}
            />
          </label>
        )}
        <label style={labelStyle}>
          Email
          <input
            data-testid="auth-email"
            type="email"
            autoComplete="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            style={inputStyle}
          />
        </label>
        <label style={labelStyle}>
          Password
          <input
            data-testid="auth-password"
            type="password"
            autoComplete={isSignup ? 'new-password' : 'current-password'}
            required
            minLength={8}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            style={inputStyle}
          />
        </label>

        {error && (
          <p data-testid="auth-error" role="alert" style={{ color: 'var(--red)', fontSize: 'var(--font-sm)', marginBottom: 'var(--space-3)' }}>
            {error}
          </p>
        )}

        <Button type="submit" variant="primary" size="lg" isLoading={busy} style={{ width: '100%' }}>
          {isSignup ? 'Create account' : 'Sign in'}
        </Button>
      </form>

      <p style={{ textAlign: 'center', marginTop: 'var(--space-5)', fontSize: 'var(--font-sm)', color: 'var(--text-tertiary)' }}>
        {isSignup ? (
          <>Already have an account? <Link data-testid="auth-switch" to="/login">Sign in</Link></>
        ) : (
          <>New here? <Link data-testid="auth-switch" to="/signup">Create an account</Link></>
        )}
      </p>
        </div>
      </div>
    </div>
  );
}
