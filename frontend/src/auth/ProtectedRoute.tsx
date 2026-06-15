import { type ReactNode } from 'react';
import { Navigate, useLocation } from 'react-router-dom';
import { useAuth } from './AuthContext';

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
    return <Navigate to="/login" state={{ from: location.pathname }} replace />;
  }
  return <>{children}</>;
}
