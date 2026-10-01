import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { authFetch, getAuthToken, setAuthToken, subscribeSessionExpired } from '../api';
import { auditApi } from '../../audit/auditApi';

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function mockFetch(status: number) {
  const f = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300, status, statusText: String(status),
    json: async () => ({}), text: async () => '',
  });
  globalThis.fetch = f as unknown as typeof fetch;
  return f;
}

// BUG-252: the raw-fetch call sites (audit API, Workbench chips) sent the bearer
// but ignored a 401, so an expired session stayed "logged in" and read as an outage.
describe('BUG-252: authFetch routes a 401 into the session-expired flow', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => vi.restoreAllMocks());

  it('sends the bearer, and a 401 clears the token and notifies subscribers', async () => {
    const token = fakeToken({ sub: 'u1' });
    setAuthToken(token);
    const f = mockFetch(401);
    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);

    const resp = await authFetch('/x', { method: 'POST', headers: { 'Content-Type': 'application/json' } });

    expect(resp.status).toBe(401);
    expect(f).toHaveBeenCalledWith('/x', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    });
    expect(getAuthToken()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('a 401 with no token is not a session expiry, and no header is sent', async () => {
    const f = mockFetch(401);
    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);
    await authFetch('/x');
    expect(f).toHaveBeenCalledWith('/x', {});
    expect(onExpired).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('a non-401 failure leaves the session alone', async () => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    mockFetch(500);
    await authFetch('/x');
    expect(getAuthToken()).not.toBeNull();
  });

  it('the audit API goes through it: an expired session on a job poll signs the user out', async () => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    mockFetch(401);
    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);
    await expect(auditApi.getJob('j1')).rejects.toThrow(/HTTP 401/);
    expect(onExpired).toHaveBeenCalledTimes(1);
    expect(getAuthToken()).toBeNull();
    unsubscribe();
  });
});
