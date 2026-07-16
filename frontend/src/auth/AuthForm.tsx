import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Button } from '@/components/ui-kit/button';
import { useViewport } from '../shell/ViewportProvider';
import { useAuth } from './AuthContext';
import './AuthForm.css';

type Mode = 'login' | 'signup';

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
  // Workbench is the single front door; a deep-linked `from` still wins.
  const from = (location.state as { from?: string } | null)?.from ?? '/workbench';

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
        <div data-testid="auth-form" className="auth-form">
          <h1 className="auth-form__title">
            {isSignup ? 'Create your account' : 'Welcome back'}
          </h1>
          <p className="auth-form__sub">
            {isSignup ? 'Check your data in under a minute.' : 'Sign in to your workspace.'}
          </p>

          <form onSubmit={onSubmit}>
            {isSignup && (
              <label className="auth-form__label">
                Name
                <input
                  data-testid="auth-name"
                  className="auth-form__input"
                  type="text"
                  autoComplete="name"
                  required
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </label>
            )}
            <label className="auth-form__label">
              Email
              <input
                data-testid="auth-email"
                className="auth-form__input"
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
              />
            </label>
            <label className="auth-form__label">
              Password
              <input
                data-testid="auth-password"
                className="auth-form__input"
                type="password"
                autoComplete={isSignup ? 'new-password' : 'current-password'}
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </label>

            {error && (
              <p data-testid="auth-error" role="alert" className="auth-form__error">
                {error}
              </p>
            )}

            <Button type="submit" size="lg" disabled={busy} className="auth-form__submit">
              {busy ? (isSignup ? 'Creating…' : 'Signing in…') : isSignup ? 'Create account' : 'Sign in'}
            </Button>
          </form>

          <p className="auth-form__switch">
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
