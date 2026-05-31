import type { ReactNode } from 'react';

/**
 * Chrome-free wrapper for the public audit-service surfaces. Deliberately
 * mounts NONE of the dashboard's auth/data hooks — an outside regulator
 * hitting /verify must reach only the verification endpoint.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  return (
    <div data-testid="public-shell" style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      <header style={{ display: 'flex', alignItems: 'center', gap: 'var(--space-2)', padding: 'var(--space-4) var(--space-6)', borderBottom: '1px solid var(--border-default)' }}>
        <span style={{ fontWeight: 700, letterSpacing: '-0.02em' }}>AURA</span>
        <span style={{ fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)', textTransform: 'uppercase', letterSpacing: '0.08em' }}>Audit Service</span>
      </header>
      <main style={{ flex: 1, width: '100%', maxWidth: 1100, margin: '0 auto', padding: 'var(--space-8) var(--space-6)' }}>
        {children}
      </main>
      <footer style={{ padding: 'var(--space-4) var(--space-6)', borderTop: '1px solid var(--border-default)', fontSize: 'var(--font-xs)', color: 'var(--text-tertiary)' }}>
        Cryptographically-verifiable compliance audits · ED25519 signed
      </footer>
    </div>
  );
}
