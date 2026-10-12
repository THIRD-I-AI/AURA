import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { authService, financialAuditService, getAuthToken, setAuthToken } from '../api';

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function mockAuditorTokenMint() {
  const auditorToken = fakeToken({ sub: 'auditor-demo', role: 'auditor' });
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: true,
    status: 200,
    statusText: 'OK',
    json: async () => ({ access_token: auditorToken }),
  }) as unknown as typeof fetch;
  return auditorToken;
}

// BUG-112: ensureAuditorToken's old guard (`if (getAuthToken()) return;`)
// treated any ambient token as sufficient, so a real, already-logged-in
// non-auditor user's session token silently blocked minting the auditor
// token the docstring says these calls require.
describe('BUG-112: ensureAuditorToken checks role, not mere token presence', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => vi.restoreAllMocks());

  it('mints a fresh auditor token when no token is present', async () => {
    const auditorToken = mockAuditorTokenMint();

    await financialAuditService.ensureAuditorToken();

    expect(getAuthToken()).toBe(auditorToken);
  });

  it('mints a fresh auditor token when the ambient token has a non-auditor role', async () => {
    setAuthToken(fakeToken({ sub: 'u1', role: 'viewer' }));
    const auditorToken = mockAuditorTokenMint();

    await financialAuditService.ensureAuditorToken();

    expect(getAuthToken()).toBe(auditorToken);
  });

  it('does not mint a new token when already auditor', async () => {
    const existing = fakeToken({ sub: 'u1', role: 'auditor' });
    setAuthToken(existing);
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await financialAuditService.ensureAuditorToken();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getAuthToken()).toBe(existing);
  });

  it('does not mint a new token when already admin', async () => {
    const existing = fakeToken({ sub: 'u1', role: 'admin' });
    setAuthToken(existing);
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await financialAuditService.ensureAuditorToken();

    expect(fetchSpy).not.toHaveBeenCalled();
    expect(getAuthToken()).toBe(existing);
  });

  it('sanity: authService.currentUser reflects the role claim used above', () => {
    setAuthToken(fakeToken({ sub: 'u1', role: 'auditor' }));
    expect(authService.currentUser()?.role).toBe('auditor');
  });
});

// BUG-387: in password auth mode (production) /auth/token refuses the open-mode mint
// body, and the thrown 422 aborted every audit action for a signed-in 'user'.
describe('BUG-387: ensureAuditorToken in password mode', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => vi.restoreAllMocks());

  it("keeps the signed-in user's own token when minting is refused", async () => {
    const own = fakeToken({ sub: 'u1', role: 'user' });
    setAuthToken(own);
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ detail: 'email and password are required in password auth mode' }),
      text: async () => '{"detail":"email and password are required in password auth mode"}',
    }) as unknown as typeof fetch;

    await expect(financialAuditService.ensureAuditorToken()).resolves.toBeUndefined();
    expect(getAuthToken()).toBe(own);
  });

  it('still fails when nobody is signed in and minting is refused', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue({
      ok: false,
      status: 422,
      statusText: 'Unprocessable Entity',
      headers: new Headers({ 'content-type': 'application/json' }),
      json: async () => ({ detail: 'nope' }),
      text: async () => '{"detail":"nope"}',
    }) as unknown as typeof fetch;

    await expect(financialAuditService.ensureAuditorToken()).rejects.toBeTruthy();
  });
});
