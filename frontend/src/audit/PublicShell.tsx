import type { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { ThemeToggle } from '@/ui/ThemeToggle';
import { useDocumentScroll } from '@/landing/useDocumentScroll';
import './audit.css';

/**
 * Chrome-free wrapper for the public audit-service surfaces. Deliberately
 * mounts NONE of the dashboard's auth/data hooks — an outside regulator
 * hitting /verify must reach only the verification endpoint.
 */
export function PublicShell({ children }: { children: ReactNode }) {
  useDocumentScroll();
  return (
    <div data-testid="public-shell" className="aud-shell">
      <header className="aud-shell__header">
        <Link to="/" className="aud-shell__brand">AURA</Link>
        <ThemeToggle />
      </header>
      <main className="aud-shell__main">{children}</main>
      <footer className="aud-shell__footer">
        Every certificate is signed with ED25519 and can be checked by anyone, without an account.
      </footer>
    </div>
  );
}
