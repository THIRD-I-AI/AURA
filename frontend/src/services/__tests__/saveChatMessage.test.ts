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

// BUG-111: saveChatMessage used to swallow every error unconditionally, so a
// caller couldn't tell a message was actually rejected server-side from one
// that saved fine.
describe('BUG-111: saveChatMessage propagates errors instead of swallowing them', () => {
  beforeEach(() => setAuthToken(fakeToken({ sub: 'u1' })));
  afterEach(() => vi.restoreAllMocks());

  it('propagates a server-side failure instead of resolving silently', async () => {
    mockFetchOnce(500, { message: 'internal error' });

    await expect(
      chatService.saveChatMessage('session-1', { type: 'user', content: 'hi' }),
    ).rejects.toMatchObject({ status: 500 });
  });

  it('still resolves on success', async () => {
    mockFetchOnce(204);

    await expect(
      chatService.saveChatMessage('session-1', { type: 'user', content: 'hi' }),
    ).resolves.toBeUndefined();
  });
});
