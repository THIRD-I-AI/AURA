import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { chatService, setAuthToken } from '../api';

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

// BUG-107: getChatHistory used to catch every error -- including a 401
// (expired token) or 403 (wrong tenant) -- and return [], so a caller
// couldn't distinguish "no history yet" from "you're not authenticated" or
// "you're not allowed to see this tenant's history."
describe('BUG-107: getChatHistory propagates auth errors instead of swallowing them', () => {
  beforeEach(() => setAuthToken(fakeToken({ sub: 'u1' })));
  afterEach(() => vi.restoreAllMocks());

  it('propagates a 401 instead of returning an empty array', async () => {
    mockFetchOnce(401, { message: 'token expired' });

    await expect(chatService.getChatHistory('session-1')).rejects.toMatchObject({ status: 401 });
  });

  it('propagates a 403 instead of returning an empty array', async () => {
    mockFetchOnce(403, { message: 'wrong tenant' });

    await expect(chatService.getChatHistory('session-1')).rejects.toMatchObject({ status: 403 });
  });

  it('still resolves to the real history on success', async () => {
    const history = [{ id: 'm1', type: 'user', content: 'hi', timestamp: '2026-01-01T00:00:00Z' }];
    mockFetchOnce(200, history);

    await expect(chatService.getChatHistory('session-1')).resolves.toEqual(history);
  });
});
