import { afterEach, describe, expect, it, vi } from 'vitest';

import { authService, authTokenSecondsLeft, getAuthToken, setAuthToken } from '../api';

function token(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}
const inSeconds = (s: number) => Math.floor(Date.now() / 1000) + s;

// BUG-397: tokens last 30 minutes and nothing renewed them.
describe('BUG-397: authService.refresh', () => {
  afterEach(() => { setAuthToken(null); vi.restoreAllMocks(); });

  it('stores the renewed token', async () => {
    const old = token({ sub: 'u1', exp: inSeconds(300) });
    const renewed = token({ sub: 'u1', exp: inSeconds(1800) });
    setAuthToken(old);
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, status: 200, json: async () => ({ access_token: renewed }) });
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    await expect(authService.refresh()).resolves.toBe(true);

    expect(getAuthToken()).toBe(renewed);
    expect(String(fetchSpy.mock.calls[0][0])).toMatch(/\/auth\/refresh$/);
    expect(fetchSpy.mock.calls[0][1].headers.Authorization).toBe(`Bearer ${old}`);
    expect(authTokenSecondsLeft()).toBeGreaterThan(1700);
  });

  it('keeps the current token when the server refuses', async () => {
    const old = token({ sub: 'u1', exp: inSeconds(300) });
    setAuthToken(old);
    globalThis.fetch = vi.fn().mockResolvedValue({ ok: false, status: 401, json: async () => ({}) }) as unknown as typeof fetch;

    await expect(authService.refresh()).resolves.toBe(false);
    expect(getAuthToken()).toBe(old);
  });

  it('does not overwrite a token that changed while the renewal was in flight', async () => {
    setAuthToken(token({ sub: 'alice', exp: inSeconds(300) }));
    const bob = token({ sub: 'bob', exp: inSeconds(1800) });
    globalThis.fetch = vi.fn().mockImplementation(async () => {
      setAuthToken(bob); // another tab switched account
      return { ok: true, status: 200, json: async () => ({ access_token: token({ sub: 'alice', exp: inSeconds(1800) }) }) };
    }) as unknown as typeof fetch;

    await expect(authService.refresh()).resolves.toBe(false);
    expect(getAuthToken()).toBe(bob);
  });

  it('does nothing when signed out', async () => {
    const fetchSpy = vi.fn();
    globalThis.fetch = fetchSpy as unknown as typeof fetch;
    await expect(authService.refresh()).resolves.toBe(false);
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
