/* OIDC handoff page. The gateway's /auth/oidc/callback redirects here with a
   SHORT-LIVED SINGLE-USE CODE in the URL fragment (never the JWT itself —
   Location headers can be logged by proxies). We redeem it via POST for the
   real token in the response body, then enter the workbench. */
import { useEffect, useState } from 'react';
import { API_BASE_URL, setAuthToken } from '../services/api';
import { takeReturnTo } from './returnTo';

export function SsoCallback() {
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    const m = window.location.hash.match(/#code=(.+)/);
    if (!m) {
      window.location.replace('/login');
      return;
    }
    fetch(`${API_BASE_URL}/auth/oidc/exchange`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code: decodeURIComponent(m[1]) }),
    })
      .then(async (r) => {
        if (!r.ok) throw new Error(`exchange failed (${r.status})`);
        const j = await r.json();
        setAuthToken(j.access_token);
        // BUG-553: return to the deep link ProtectedRoute saved before the
        // OIDC redirect left the SPA.
        window.location.replace(takeReturnTo() ?? '/workbench');
      })
      .catch((e) => setError(e instanceof Error ? e.message : 'exchange failed'));
  }, []);
  return (
    <div className="font-mono" style={{ padding: 40, color: error ? 'var(--danger)' : 'var(--text-tertiary)' }}>
      {error ? `Sign-in failed: ${error} — return to /login and retry.` : 'Completing sign-in…'}
    </div>
  );
}
