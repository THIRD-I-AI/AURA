import { type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';
import { rememberReturnTo } from './returnTo';

/**
 * Gate for authenticated surfaces (the `/app` dashboard). An anonymous visitor
 * is bounced to /login, remembering where they were headed so we can send them
 * back after they sign in. Public surfaces (the audit front door, /verify) are
 * intentionally NOT wrapped — a regulator must reach verification without login.
 */
export function ProtectedRoute({ children }: { children: ReactNode }) {
  const { isAuthenticated } = useAuth();
  const location = useLocation();

  if (!isAuthenticated) {
    // BUG-553: pathname alone dropped ?panel=… and #hash from deep links.
    const from = location.pathname + location.search + location.hash;
    rememberReturnTo(from);
    return <Navigate to="/login" state={{ from }} replace />;
  }
  return <>{children}</>;
}
