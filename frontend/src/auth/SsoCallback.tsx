/* OIDC handoff page. The gateway's /auth/oidc/callback redirects here with
   the AURA JWT in the URL FRAGMENT (fragments never reach servers/logs).
   Store it and enter the workbench. */
import { useEffect } from 'react';
import { setAuthToken } from '../services/api';

export function SsoCallback() {
  useEffect(() => {
    const m = window.location.hash.match(/#token=(.+)/);
    if (m) {
      setAuthToken(decodeURIComponent(m[1]));
      window.location.replace('/workbench');
    } else {
      window.location.replace('/login');
    }
  }, []);
  return <div style={{ padding: 40, fontFamily: 'monospace', color: '#9aa4b2' }}>Completing sign-in…</div>;
}
