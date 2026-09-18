import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { chatService, getAuthToken, setAuthToken, subscribeSessionExpired } from '../api';

/** A decodable (unsigned) JWT-shaped token — same helper shape as auth.test.tsx. */
function fakeToken(claims: Record<string, unknown>): string {
  return `h.${btoa(JSON.stringify(claims))}.s`;
}

function mockFetchOnce(status: number, body: unknown = {}) {
  globalThis.fetch = vi.fn().mockResolvedValue({
    ok: status >= 200 && status < 300,
    status,
    statusText: String(status),
    json: async () => body,
  }) as unknown as typeof fetch;
}

describe('BUG-104/105: session-expiry notification', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => vi.restoreAllMocks());

  it('a 401 on an authenticated request clears the token and notifies subscribers', async () => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    mockFetchOnce(401, { message: 'token expired' });

    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);

    // getChatHistory propagates its error (BUG-107 fix) -- the session-expiry
    // side effect happens inside request() before that rejection reaches here.
    await expect(chatService.getChatHistory('session-1')).rejects.toBeTruthy();

    expect(getAuthToken()).toBeNull();
    expect(onExpired).toHaveBeenCalledTimes(1);
    unsubscribe();
  });

  it('a 401 with no ambient token (e.g. a failed login) does not notify subscribers', async () => {
    setAuthToken(null);
    mockFetchOnce(401, { message: 'invalid credentials' });

    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);

    await expect(chatService.getChatHistory('session-1')).rejects.toBeTruthy();

    expect(onExpired).not.toHaveBeenCalled();
    unsubscribe();
  });

  it('a successful request does not notify subscribers', async () => {
    setAuthToken(fakeToken({ sub: 'u1' }));
    mockFetchOnce(200, []);

    const onExpired = vi.fn();
    const unsubscribe = subscribeSessionExpired(onExpired);

    await chatService.getChatHistory('session-1');

    expect(onExpired).not.toHaveBeenCalled();
    expect(getAuthToken()).not.toBeNull();
    unsubscribe();
  });
});
