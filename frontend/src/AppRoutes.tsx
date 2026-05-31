import { lazy, Suspense } from 'react';
import { Routes, Route } from 'react-router-dom';

import { PublicShell } from './audit/PublicShell';
import { AuditFrontDoor } from './audit/AuditFrontDoor';
import { AuditProgress } from './audit/AuditProgress';
import { CertificatePage } from './audit/CertificatePage';
import { VerifyPage } from './audit/VerifyPage';
import { AuditWizard } from './audit/AuditWizard';

const Dashboard = lazy(() => import('./App'));

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<PublicShell><AuditFrontDoor /></PublicShell>} />
      <Route path="/audit/new" element={<PublicShell><AuditWizard /></PublicShell>} />
      <Route path="/audit/:jobId" element={<PublicShell><AuditProgress /></PublicShell>} />
      <Route path="/certificate/:hash" element={<PublicShell><CertificatePage /></PublicShell>} />
      <Route path="/verify/:hash" element={<PublicShell><VerifyPage /></PublicShell>} />
      <Route path="/app/*" element={<Suspense fallback={<div>Loading…</div>}><Dashboard /></Suspense>} />
    </Routes>
  );
}
