import type { ReactNode } from 'react';
import './audit.css';

/**
 * Chrome-free wrapper for the public audit-service surfaces. Deliberately
 * mounts NONE of the dashboard's auth/data hooks — an outside regulator
 * hitting /verify must reach only the verification endpoint.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div data-testid="public-shell" className="aud-shell">
      <header className="aud-shell__header">
        <span className="aud-shell__brand">AURA</span>
        <span className="aud-shell__tag">Audit Service</span>
      </header>
      <main className="aud-shell__main">{children}</main>
      <footer className="aud-shell__footer">
        <span aria-hidden="true">⬢</span>
        Cryptographically-verifiable compliance audits · ED25519 signed
      </footer>
    </div>
  );
}
