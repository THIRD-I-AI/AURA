// BUG-553: the post-login return target must carry the query string and hash
// (/app/terminal?panel=audit), and must survive the OIDC round trip, which
// leaves the SPA, so router state alone cannot carry it to SsoCallback.
const RETURN_TO_KEY = 'aura.returnTo';

// Only a same-origin path: '//evil.example' and '/\evil.example' are
// protocol-relative to a browser, so a single leading '/' is required.
export function safeReturnTo(value: unknown): string | null {
  return typeof value === 'string' && /^\/(?![/\\])/.test(value) ? value : null;
}

export function rememberReturnTo(path: string): void {
  try {
    sessionStorage.setItem(RETURN_TO_KEY, path);
  } catch { /* storage blocked */ }
}

export function takeReturnTo(): string | null {
  try {
    const value = sessionStorage.getItem(RETURN_TO_KEY);
    sessionStorage.removeItem(RETURN_TO_KEY);
    return safeReturnTo(value);
  } catch {
    return null;
  }
}
