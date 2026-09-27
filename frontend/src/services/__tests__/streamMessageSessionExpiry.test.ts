import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { chatService, setAuthToken, subscribeSessionExpired } from '../api';

describe('chatService.streamMessage session expiry', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    setAuthToken('a-token');
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    setAuthToken(null);
    vi.restoreAllMocks();
  });

  it('clears the token and notifies session-expiry listeners on a 401', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(null, { status: 401, statusText: 'Unauthorized' })
    ) as unknown as typeof fetch;

    const fired: boolean[] = [];
    const unsubscribe = subscribeSessionExpired(() => fired.push(true));

    await expect(
      chatService.streamMessage('hi', { onEvent: () => {} })
    ).rejects.toThrow('stream failed: 401');

    expect(fired).toEqual([true]);
    unsubscribe();

    // the dead token must not be sent on the next request
    globalThis.fetch = vi.fn().mockResolvedValue(new Response('{}', { status: 200 })) as unknown as typeof fetch;
    await chatService.streamMessage('again', { onEvent: () => {} }).catch(() => {});
    const headers = (globalThis.fetch as ReturnType<typeof vi.fn>).mock.calls[0][1].headers;
    expect(headers.Authorization).toBeUndefined();
  });

  it('does not fire the listener on a 401 with no ambient token (failed login path is unaffected)', async () => {
    setAuthToken(null);
    globalThis.fetch = vi.fn().mockResolvedValue(
      new Response(null, { status: 401 })
    ) as unknown as typeof fetch;
    const fired: boolean[] = [];
    const unsubscribe = subscribeSessionExpired(() => fired.push(true));

    await expect(chatService.streamMessage('hi', { onEvent: () => {} })).rejects.toThrow();
    expect(fired).toEqual([]);
    unsubscribe();
  });

  it('does not fire the listener on a 404 (commander disabled, not an auth failure)', async () => {
    globalThis.fetch = vi.fn().mockResolvedValue(new Response(null, { status: 404 })) as unknown as typeof fetch;
    const fired: boolean[] = [];
    const unsubscribe = subscribeSessionExpired(() => fired.push(true));

    await expect(chatService.streamMessage('hi', { onEvent: () => {} })).rejects.toThrow('commander_disabled');
    expect(fired).toEqual([]);
    unsubscribe();
  });
});
