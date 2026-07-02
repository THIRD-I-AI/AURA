import { lazy, Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';

import { PublicShell } from './audit/PublicShell';
import { AuditFrontDoor } from './audit/AuditFrontDoor';
import { AuditProgress } from './audit/AuditProgress';
import { CertificatePage } from './audit/CertificatePage';
import { VerifyPage } from './audit/VerifyPage';
import { AuditWizard } from './audit/AuditWizard';
import { AuthForm } from './auth/AuthForm';
import { ProtectedRoute } from './auth/ProtectedRoute';

const Dashboard = lazy(() => import('./App'));
const TerminalWorkspace = lazy(() => import('./terminal/TerminalWorkspace'));
const Workbench = lazy(() => import('./workbench/Workbench'));

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<PublicShell><AuditFrontDoor /></PublicShell>} />
      <Route path="/login" element={<PublicShell><AuthForm mode="login" /></PublicShell>} />
      <Route path="/signup" element={<PublicShell><AuthForm mode="signup" /></PublicShell>} />
      <Route path="/audit/new" element={<PublicShell><AuditWizard /></PublicShell>} />
      <Route path="/audit/:jobId" element={<PublicShell><AuditProgress /></PublicShell>} />
      <Route path="/certificate/:hash" element={<PublicShell><CertificatePage /></PublicShell>} />
      <Route path="/verify/:hash" element={<PublicShell><VerifyPage /></PublicShell>} />
      {/* Workbench redesign (Claude Design port) — additive full-viewport
          shell with its own login/boot prototype flow; classic /app untouched. */}
      <Route path="/workbench" element={<Suspense fallback={<div>Loading…</div>}><Workbench /></Suspense>} />
      {/* Full-viewport terminal cockpit — sibling of /app, NOT nested inside
          the sidebar shell. Must appear before /app/* to prevent shadowing. */}
      <Route path="/app/terminal/*" element={
        <ProtectedRoute>
          <Suspense fallback={<div>Loading…</div>}><TerminalWorkspace /></Suspense>
        </ProtectedRoute>
      } />
      {/* /app/* is auth-gated (ProtectedRoute); routing within (page
          selection, deep links, /app → engagements default) is handled
          inside App via app/routing.ts deriving the page from the URL. */}
      <Route path="/app/*" element={<ProtectedRoute><Suspense fallback={<div>Loading…</div>}><Dashboard /></Suspense></ProtectedRoute>} />
    </Routes>
  );
}
