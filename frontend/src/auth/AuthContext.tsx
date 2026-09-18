import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { authService, subscribeSessionExpired, type AuthUser } from '../services/api';

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

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(() => authService.currentUser());

  // BUG-105: isAuthenticated was only ever set at mount and by explicit
  // login/register/logout -- a token that expires mid-session never flips it
  // false. Subscribing to the api client's session-expired signal (fired on
  // a real 401 from an authenticated request, see BUG-104) re-evaluates it
  // reactively, so ProtectedRoute redirects to /login as soon as the app
  // actually discovers the session is dead.
  useEffect(() => subscribeSessionExpired(() => setUser(null)), []);

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
