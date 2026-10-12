import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { authService, authTokenSecondsLeft, subscribeAuthChanged, subscribeSessionExpired, type AuthUser } from '../services/api';

/**
 * App-wide authentication state. Seeds from any token already in storage so a
 * returning user stays logged in across reloads; clears on logout. The token
 * is the source of truth — this context only mirrors its claims for the UI.
 */
interface AuthContextValue {
  user: AuthUser | null;
  isAuthenticated: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (name: string, email: string, password: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

const SESSION_CHECK_INTERVAL_MS = 60_000;
const SESSION_RENEW_BELOW_SECONDS = 10 * 60;
const SESSION_ACTIVITY_WINDOW_MS = 15 * 60_000;

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => authService.currentUser());

  // BUG-105: isAuthenticated was only ever set at mount and by explicit
  // login/register/logout -- a token that expires mid-session never flips it
  // false. Subscribing to the api client's session-expired signal (fired on
  // a real 401 from an authenticated request, see BUG-104) re-evaluates it
  // reactively, so ProtectedRoute redirects to /login as soon as the app
  // actually discovers the session is dead.
  useEffect(() => subscribeSessionExpired(() => setUser(null)), []);
  // BUG-392: another tab signed out or switched account -- mirror it here.
  useEffect(() => subscribeAuthChanged(() => setUser(authService.currentUser())), []);

  // BUG-397: renew the token while the user is actually using the app, so a long
  // working session is not cut off at the 30-minute token lifetime. An idle tab is
  // left to expire, and the server caps the whole session's age.
  useEffect(() => {
    if (!user) return undefined;
    let lastActivity = Date.now();
    const touch = () => { lastActivity = Date.now(); };
    window.addEventListener('pointerdown', touch);
    window.addEventListener('keydown', touch);
    const timer = window.setInterval(() => {
      const left = authTokenSecondsLeft();
      const active = Date.now() - lastActivity < SESSION_ACTIVITY_WINDOW_MS;
      if (left !== null && left > 0 && left < SESSION_RENEW_BELOW_SECONDS && active) {
        void authService.refresh();
      }
    }, SESSION_CHECK_INTERVAL_MS);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener('pointerdown', touch);
      window.removeEventListener('keydown', touch);
    };
  }, [user]);

  const value: AuthContextValue = {
    user,
    isAuthenticated: user !== null,
    login: async (email, password) => {
      setUser(await authService.login(email, password));
    },
    register: async (name, email, password) => {
      setUser(await authService.register(name, email, password));
    },
    logout: () => {
      authService.logout();
      setUser(null);
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

// Graceful logged-out default when no provider is mounted. Auth-aware
// components (AuthNav, ProtectedRoute) can then render safely anywhere —
// including in isolation tests — instead of crashing the tree. The mutating
// actions still fail loudly if invoked without a provider, so a genuine
// misuse (trying to log in with no AuthProvider) surfaces clearly.
const NO_PROVIDER: AuthContextValue = {
  user: null,
  isAuthenticated: false,
  login: async () => { throw new Error('Cannot log in: <AuthProvider> is not mounted'); },
  register: async () => { throw new Error('Cannot register: <AuthProvider> is not mounted'); },
  logout: () => {},
};

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  return useContext(AuthContext) ?? NO_PROVIDER;
}
