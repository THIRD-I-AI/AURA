import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { API_BASE_URL, setAuthToken, systemService } from '../api';

// BUG-423: the health report was fetched from the root path with no bearer. The
// deployed proxy hands that path to the SPA, and with JWT on the gateway answers 401.
describe('systemService.health', () => {
  beforeEach(() => setAuthToken(null));
  afterEach(() => vi.restoreAllMocks());

  it('calls the versioned endpoint with the session bearer', async () => {
    const token = `h.${btoa(JSON.stringify({ sub: 'u1' }))}.s`;
    setAuthToken(token);
    const f = vi.fn().mockResolvedValue({
      ok: true, status: 200, statusText: 'OK', json: async () => ({ overall: 'healthy' }),
    });
    globalThis.fetch = f as unknown as typeof fetch;

    expect(await systemService.health()).toEqual({ overall: 'healthy' });

    const [url, init] = f.mock.calls[0];
    expect(url).toBe(`${API_BASE_URL}/system/health`);
    expect(init.headers.Authorization).toBe(`Bearer ${token}`);
  });
});
