import { useState, type ReactNode } from 'react';

import { fetchProtectedFile } from '@/services/api';

/**
 * A link to a file behind the JWT gate.
 *
 * A plain `<a href target="_blank">` (or window.open) is a browser navigation,
 * and a navigation cannot carry the bearer token — so with auth on the new tab
 * showed `{"error":"AUTHENTICATION_REQUIRED"}` instead of the file. This
 * fetches the file with the token and hands the browser the result.
 *
 * The href is kept so the link still reads and copies as a link; the click is
 * what is intercepted.
 */
export function AuthenticatedLink({
  href,
  downloadAs,
  className,
  children,
  'data-testid': testId,
}: {
  href: string;
  /** Save under this name instead of opening in a new tab. */
  downloadAs?: string;
  className?: string;
  children: ReactNode;
  'data-testid'?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState<string | null>(null);

  const open = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setFailed(null);
    try {
      await fetchProtectedFile(href, { downloadAs });
    } catch (err) {
      setFailed(err instanceof Error ? err.message : 'Could not open the file');
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <a
        href={href}
        onClick={open}
        aria-busy={busy}
        className={className}
        data-testid={testId}
      >
        {children}
      </a>
      {failed && (
        <span role="alert" className="ml-2 text-danger" data-testid={testId ? `${testId}-error` : undefined}>
          {failed}
        </span>
      )}
    </>
  );
}
