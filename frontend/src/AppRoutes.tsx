import { lazy, Suspense } from 'react';
import { Routes, Route, Navigate } from 'react-router-dom';

import { PublicShell } from './audit/PublicShell';
import { LandingPage } from './landing/LandingPage';
import { AuditProgress } from './audit/AuditProgress';
import { CertificatePage } from './audit/CertificatePage';
import { VerifyPage } from './audit/VerifyPage';
import { AuditWizard } from './audit/AuditWizard';
import { AuthForm } from './auth/AuthForm';
import { SsoCallback } from './auth/SsoCallback';
import { ProtectedRoute } from './auth/ProtectedRoute';

const TerminalWorkspace = lazy(() => import('./terminal/TerminalWorkspace'));
const Workbench = lazy(() => import('./workbench/Workbench'));

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage />} />
      <Route path="/login" element={<AuthForm mode="login" />} />
      <Route path="/signup" element={<AuthForm mode="signup" />} />
      {/* OIDC fragment handoff — gateway redirects here after SSO */}
      <Route path="/auth/sso" element={<SsoCallback />} />
      <Route path="/audit/new" element={<PublicShell><AuditWizard /></PublicShell>} />
      <Route path="/audit/:jobId" element={<PublicShell><AuditProgress /></PublicShell>} />
      <Route path="/certificate/:hash" element={<PublicShell><CertificatePage /></PublicShell>} />
      <Route path="/verify/:hash" element={<PublicShell><VerifyPage /></PublicShell>} />
      {/* The Workbench is the one authenticated app — a full-viewport cockpit.
          Auth is the real /login (ProtectedRoute); it boots straight in. */}
      <Route path="/workbench" element={
        <ProtectedRoute>
          <Suspense fallback={<div>Loading…</div>}><Workbench /></Suspense>
        </ProtectedRoute>
      } />
      {/* Full-viewport terminal cockpit — sibling of the workbench. Must appear
          before /app/* to prevent shadowing. */}
      <Route path="/app/terminal/*" element={
        <ProtectedRoute>
          <Suspense fallback={<div>Loading…</div>}><TerminalWorkspace /></Suspense>
        </ProtectedRoute>
      } />
      {/* Classic /app shell removed — legacy deep links stay auth-gated, then
          redirect to the Workbench (the single app). */}
      <Route path="/app/*" element={<ProtectedRoute><Navigate to="/workbench" replace /></ProtectedRoute>} />
    </Routes>
  );
}
