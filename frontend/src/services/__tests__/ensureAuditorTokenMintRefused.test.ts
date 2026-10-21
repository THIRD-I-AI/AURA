import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// The refusal is remembered for the life of the api module, so every case loads
// a fresh copy instead of inheriting another case's refusal.
async function freshApi() {
  vi.resetModules();
  return import('../api');
}

function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function respond(status: number, detail: string) {
  return {
    ok: false,
    status,
    statusText: 'refused',
    headers: new Headers({ 'content-type': 'application/json' }),
    json: async () => ({ detail }),
    text: async () => JSON.stringify({ detail }),
  };
}

const PASSWORD_MODE_422 = 'email and password are required in password auth mode';

function mintCalls(fetchMock: ReturnType<typeof vi.fn>): number {
  return fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/auth/token')).length;
}

// BUG-484: in password mode every Exception Queue mount and action re-POSTed the
// open-mode mint body to /auth/token. Each certain-to-fail call spent one of the
// 10-per-minute auth:<ip> rate-limit slots, so a busy auditor locked their whole
// office out of signing in.
describe('BUG-484: a refused auditor mint is not retried', () => {
  let fetchMock: ReturnType<typeof vi.fn>;
  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue(respond(422, PASSWORD_MODE_422));
    globalThis.fetch = fetchMock as unknown as typeof fetch;
  });
  afterEach(() => vi.restoreAllMocks());

  it('asks /auth/token once, then keeps using the signed-in session', async () => {
    const api = await freshApi();
    const own = fakeToken({ sub: 'u1', role: 'user' });
    api.setAuthToken(own);

    for (let i = 0; i < 12; i++) await api.financialAuditService.ensureAuditorToken();

    expect(mintCalls(fetchMock)).toBe(1);
    expect(api.getAuthToken()).toBe(own);
  });

  it('still fails with the server reason, without another request, when nobody is signed in', async () => {
    const api = await freshApi();
    api.setAuthToken(null);

    await expect(api.financialAuditService.ensureAuditorToken()).rejects.toThrow(/password auth mode/);
    await expect(api.financialAuditService.ensureAuditorToken()).rejects.toThrow(/password auth mode/);
    expect(mintCalls(fetchMock)).toBe(1);
  });

  it('a 429 is transient and does not stop the next attempt', async () => {
    const api = await freshApi();
    api.setAuthToken(fakeToken({ sub: 'u1', role: 'user' }));
    fetchMock.mockResolvedValue(respond(429, 'Too many sign-in attempts'));

    await api.financialAuditService.ensureAuditorToken();
    await api.financialAuditService.ensureAuditorToken();
    expect(mintCalls(fetchMock)).toBe(2);
  });
});
