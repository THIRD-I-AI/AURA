import { createContext, useContext, useState, type ReactNode } from 'react';
import { authService, type AuthUser } from '../services/api';

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
