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

// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error('useAuth must be used within an <AuthProvider>');
  }
  return ctx;
}
